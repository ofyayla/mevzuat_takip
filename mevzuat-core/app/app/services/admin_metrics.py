"""Yönetim sayfası metrikleri (06 §10): kaynak başına son tarama, ``next_due``, bekleyen aktarım ve LLM gecikmesi.

Veri zaten var (``fetch_run``, ``llm_call``, crawler sinyali, Mongo ``raw_documents``); bu modül yalnızca okur ve özetler.
Mongo'ya erişilemezse ilgili alan ``None`` döner: yönetim sayfası kalan metrikleri yine de gösterir.
"""
from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import FetchRun, HealthSample, LlmCall
from app.settings import Settings

log = logging.getLogger(__name__)
LLM_WINDOW_HOURS = 24
SCAN_WINDOW_DAYS = 7
QUEUE_WINDOW_DAYS = 14
INGEST_WINDOW_HOURS = 24
HEALTH_SAMPLE_RETENTION_DAYS = 14
LLM_TARGET_MS = 60_000
SCAN_TARGET_RATE = 98.0


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    if len(values) < 2:
        return values[0]
    return round(statistics.quantiles(values, n=20, method="inclusive")[18])


def llm_latency(session: Session, now: datetime, hours: int = LLM_WINDOW_HOURS) -> dict:
    now = _utc(now)
    since = now - timedelta(hours=hours)
    rows = session.execute(select(LlmCall.latency_ms, LlmCall.status, LlmCall.created_at)
                           .where(LlmCall.created_at >= since, LlmCall.created_at <= now)).all()
    ok = [r.latency_ms for r in rows if r.status == "ok" and r.latency_ms is not None]
    hourly: list[list[int]] = [[] for _ in range(hours)]
    for row in rows:
        if row.status != "ok" or row.latency_ms is None:
            continue
        index = int((_utc(row.created_at) - since).total_seconds() // 3600)
        if 0 <= index < hours:
            hourly[index].append(row.latency_ms)
    return {"windowHours": hours, "calls": len(rows), "failed": sum(1 for r in rows if r.status != "ok"),
            "p50Ms": round(statistics.median(ok)) if ok else None, "p95Ms": _p95(ok),
            "targetMs": LLM_TARGET_MS,
            "series": [{"at": (since + timedelta(hours=i)).isoformat(), "value": _p95(values)}
                       for i, values in enumerate(hourly)]}


def scan_metrics(session: Session, settings: Settings, now: datetime) -> dict:
    now = _utc(now)
    tz = ZoneInfo(settings.timezone)
    today = now.astimezone(tz).date()
    first_day = today - timedelta(days=SCAN_WINDOW_DAYS - 1)
    first_local = datetime(first_day.year, first_day.month, first_day.day, tzinfo=tz)
    rows = session.execute(
        select(FetchRun.status, FetchRun.finished_at)
        .where(FetchRun.finished_at >= first_local.astimezone(timezone.utc), FetchRun.finished_at <= now,
               FetchRun.status.in_(("success", "partial", "failed")))
    ).all()

    daily: dict[date, list[int]] = {}
    for day_offset in range(SCAN_WINDOW_DAYS):
        daily[first_day + timedelta(days=day_offset)] = [0, 0]
    for status, finished_at in rows:
        day = _utc(finished_at).astimezone(tz).date()
        counts = daily.get(day)
        if counts is None:
            continue
        counts[1] += 1
        if status == "success":
            counts[0] += 1

    total = sum(counts[1] for counts in daily.values())
    successes = sum(counts[0] for counts in daily.values())
    return {
        "windowDays": SCAN_WINDOW_DAYS,
        "total": total,
        "failed": total - successes,
        "successRate": successes * 100 / total if total else None,
        "targetRate": SCAN_TARGET_RATE,
        "series": [
            {
                "at": datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc).isoformat(),
                "value": successes * 100 / count if count else None,
            }
            for day, (successes, count) in daily.items()
        ],
    }


def _sample_series(session: Session, now: datetime, hours: int, field: str) -> list[dict]:
    now = _utc(now)
    since = now - timedelta(hours=hours)
    values: list[int | None] = [None] * hours
    samples = session.scalars(
        select(HealthSample)
        .where(HealthSample.captured_at >= since, HealthSample.captured_at <= now)
        .order_by(HealthSample.captured_at, HealthSample.id)
    )
    for sample in samples:
        index = int((_utc(sample.captured_at) - since).total_seconds() // 3600)
        if 0 <= index < hours:
            values[index] = getattr(sample, field)
    return [
        {"at": (since + timedelta(hours=i)).isoformat(), "value": value}
        for i, value in enumerate(values)
    ]


def record_health_sample(session: Session, settings: Settings, queue: dict, now: datetime | None = None) -> None:
    now = _utc(now or datetime.now(timezone.utc))
    _, pending_ingest = _crawler_state(settings)
    session.execute(delete(HealthSample).where(
        HealthSample.captured_at < now - timedelta(days=HEALTH_SAMPLE_RETENTION_DAYS)
    ))
    session.add(HealthSample(captured_at=now, ai_queue_count=queue["preparing"], pending_ingest=pending_ingest))


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
    from app.services.scans import ai_queue

    now = _utc(now or datetime.now(timezone.utc))
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
    llm = llm_latency(session, now)
    queue = ai_queue(session)
    return {"generatedAt": now.isoformat(), "crawlMode": settings.crawl_mode, "pendingIngest": pending_ingest,
            "sources": sources, "llm": llm, "scans": scan_metrics(session, settings, now),
            "aiQueue": {"count": queue["preparing"], "windowDays": QUEUE_WINDOW_DAYS,
                        "series": _sample_series(session, now, QUEUE_WINDOW_DAYS * 24, "ai_queue_count")},
            "ingest": {"windowHours": INGEST_WINDOW_HOURS,
                       "series": _sample_series(session, now, INGEST_WINDOW_HOURS, "pending_ingest")}}
