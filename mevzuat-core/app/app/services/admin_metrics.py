"""Yönetim sayfası metrikleri (06 §10): kaynak başına son tarama, ``next_due``, bekleyen aktarım ve LLM gecikmesi.

Veri zaten var (``fetch_run``, ``llm_call``, crawler sinyali, Mongo ``raw_documents``); bu modül yalnızca okur ve özetler.
Mongo'ya erişilemezse ilgili alan ``None`` döner: yönetim sayfası kalan metrikleri yine de gösterir.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import FetchRun, LlmCall
from app.settings import Settings

log = logging.getLogger(__name__)
LLM_WINDOW_HOURS = 24


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
    rows = session.execute(select(LlmCall.task, LlmCall.latency_ms, LlmCall.status)
                           .where(LlmCall.created_at >= since)).all()
    ok = [r.latency_ms for r in rows if r.status == "ok" and r.latency_ms is not None]
    return {"windowHours": hours, "calls": len(rows), "failed": sum(1 for r in rows if r.status != "ok"),
            "p50Ms": round(statistics.median(ok)) if ok else None, "p95Ms": _p95(ok)}


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
            "sources": sources, "llm": llm_latency(session, now)}
