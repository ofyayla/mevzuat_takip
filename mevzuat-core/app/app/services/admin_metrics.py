"""Yönetim sayfası metrikleri (06 §10): kaynak başına son tarama, ``next_due``, bekleyen aktarım ve LLM gecikmesi.

``collect`` yalnızca okur; ``record_health_sample`` izleme görevi sırasında kuyruk geçmişini kaydeder.
Mongo'ya erişilemezse ilgili alan ``None`` döner: yönetim sayfası kalan metrikleri yine de gösterir.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import FetchRun, HealthSample, LlmCall, Regulation
from app.services.regulations import PREPARING_STATUSES, visible
from app.settings import Settings

log = logging.getLogger(__name__)
LLM_WINDOW_HOURS = 24
HISTORY_DAYS = 14
LLM_TARGET_MS = 60_000
SCAN_TARGET_RATE = 98


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    if len(values) < 2:
        return values[0]
    return round(statistics.quantiles(values, n=20, method="inclusive")[18])


def llm_latency(session: Session, now: datetime, hours: int = LLM_WINDOW_HOURS) -> dict:
    since = now - timedelta(hours=hours)
    rows = session.execute(select(LlmCall.created_at, LlmCall.latency_ms, LlmCall.status)
                           .where(LlmCall.created_at >= since, LlmCall.created_at <= now)).all()
    ok = [r.latency_ms for r in rows if r.status == "ok" and r.latency_ms is not None]
    buckets: list[list[int]] = [[] for _ in range(hours)]
    for r in rows:
        if r.status == "ok" and r.latency_ms is not None:
            i = min(hours - 1, int((_utc(r.created_at) - since).total_seconds() // 3600))
            buckets[i].append(r.latency_ms)
    return {"windowHours": hours, "calls": len(rows), "failed": sum(1 for r in rows if r.status != "ok"),
            "p50Ms": round(statistics.median(ok)) if ok else None, "p95Ms": _p95(ok),
            "targetMs": LLM_TARGET_MS,
            "series": [{"at": (since + timedelta(hours=i + 1)).isoformat(), "value": _p95(b)}
                       for i, b in enumerate(buckets)]}


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _ai_queue(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Regulation).where(
        visible(), Regulation.processing_status.in_(PREPARING_STATUSES))) or 0


def record_health_sample(session: Session, settings: Settings, now: datetime) -> None:
    """Çağıran izleme işlemiyle birlikte commit edilir; GET /metrics salt okunur kalır."""
    _, pending = _crawler_state(settings)
    session.add(HealthSample(captured_at=now, ai_queue=_ai_queue(session),
                             pending_ingest=pending, crawl_mode=settings.crawl_mode))
    session.execute(delete(HealthSample).where(HealthSample.captured_at < now - timedelta(days=HISTORY_DAYS)))


def scan_success(session: Session, now: datetime) -> dict:
    since = now - timedelta(days=7)
    rows = session.execute(select(FetchRun.finished_at, FetchRun.status).where(
        FetchRun.finished_at >= since, FetchRun.finished_at <= now,
        FetchRun.status.in_(("success", "partial", "failed")))).all()
    buckets: list[list[str]] = [[] for _ in range(7)]
    for r in rows:
        i = min(6, (_utc(r.finished_at) - since).days)
        buckets[i].append(r.status)

    def rate(statuses: list[str]) -> float | None:
        return round(100 * statuses.count("success") / len(statuses), 1) if statuses else None

    return {"windowDays": 7, "targetRate": SCAN_TARGET_RATE, "total": len(rows),
            "failed": sum(r.status != "success" for r in rows), "successRate": rate([r.status for r in rows]),
            "series": [{"at": (since + timedelta(days=i + 1)).isoformat(), "value": rate(b)}
                       for i, b in enumerate(buckets)]}


def queue_history(session: Session, settings: Settings, now: datetime, pending: int | None) -> dict:
    since = now - timedelta(days=HISTORY_DAYS)
    count = _ai_queue(session)
    rows = session.scalars(select(HealthSample).where(
        HealthSample.captured_at >= since, HealthSample.captured_at <= now)
        .order_by(HealthSample.captured_at, HealthSample.id)).all()

    def series(field: str, hours: int) -> list[dict]:
        start = now - timedelta(hours=hours)
        buckets: list[int | None] = [None] * hours
        for r in rows:
            at = _utc(r.captured_at)
            if at < start or (field == "pending_ingest" and r.crawl_mode != settings.crawl_mode):
                continue
            i = min(hours - 1, int((at - start).total_seconds() // 3600))
            buckets[i] = getattr(r, field)
        # Son nokta, izleme görevi henüz çalışmamış olsa bile mevcut gerçek ölçümdür.
        buckets[-1] = count if field == "ai_queue" else pending
        return [{"at": (start + timedelta(hours=i + 1)).isoformat(), "value": value}
                for i, value in enumerate(buckets)]

    return {"aiQueue": {"windowDays": HISTORY_DAYS, "count": count,
                        "series": series("ai_queue", HISTORY_DAYS * 24)},
            "ingest": {"windowHours": 24, "series": series("pending_ingest", 24)}}


def _crawler_state(settings: Settings) -> tuple[dict, int | None]:
    """(kaynak → next_due ISO, aktarılmayı bekleyen belge sayısı); yalnız DMZ kurulumunda Mongo'dan, yoksa boş."""
    if settings.crawl_mode != "remote":
        return {}, None
    try:
        from app.mongo import DOCS, crawl_db, last_heartbeat

        db = crawl_db(settings)
        hb = last_heartbeat(db) or {}
        return hb.get("next_due") or {}, db[DOCS].count_documents({"synced": False})
    except Exception as e:  # noqa: BLE001 — Mongo erişilemezse metrik boş kalır, sayfa açılır
        log.warning("admin metrics: Mongo okunamadı: %s: %s", type(e).__name__, e)
        return {}, None


def collect(session: Session, settings: Settings, now: datetime | None = None) -> dict:
    from app.collectors.config import load_sources
    from app.crawler.service import next_fire

    now = now or datetime.now(timezone.utc)
    next_due, pending_ingest = _crawler_state(settings)
    last_ids = dict(session.execute(select(FetchRun.source_code, func.max(FetchRun.id)).group_by(FetchRun.source_code)).all())
    runs = {r.source_code: r for r in session.scalars(select(FetchRun).where(FetchRun.id.in_(last_ids.values())))} \
        if last_ids else {}
    sources = []
    for src in load_sources(settings.sources_file).enabled():
        run, due = runs.get(src.code), next_due.get(src.code)
        if due is None:
            try:
                nf = next_fire(src, now, settings.timezone)
                due = nf.isoformat() if nf else None
            except Exception:  # noqa: BLE001 — bozuk cron ifadesi tek kaynağın alanını boş bırakır
                due = None
        sources.append({
            "code": src.code, "name": src.name, "nextDue": due,
            "lastRun": None if run is None else {
                "status": run.status, "startedAt": _iso(run.started_at), "finishedAt": _iso(run.finished_at),
                "durationSec": round((run.finished_at - run.started_at).total_seconds(), 1) if run.finished_at else None,
                "requests": run.requests, "itemsNew": run.items_new, "itemsListed": run.items_listed}})
    return {"generatedAt": now.isoformat(), "crawlMode": settings.crawl_mode, "pendingIngest": pending_ingest,
            "sources": sources, "llm": llm_latency(session, now), "scans": scan_success(session, now),
            **queue_history(session, settings, now, pending_ingest)}
