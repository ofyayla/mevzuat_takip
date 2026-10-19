"""Yönetim sayfası "Taramalar ve işler": canlı tarama durumu, crawler kuyruğu, tarama geçmişi ve arka plan işleri.

Veri zaten var; bu modül yalnızca okur ve özetler:

* ``fetch_run`` (PostgreSQL): ``running`` kayıtlar etkin, diğerleri geçmiştir.
* Mongo ``crawl_requests`` / ``crawler_status`` (yalnız ``CRAWL_MODE=remote``): kuyruktaki talepler ve crawler sinyali.
  Mongo'ya erişilemezse ``crawler.error`` dolar, sayfanın kalanı yine yüklenir.
* ``job_run``: yönetim/CLI işleri (``services.jobs``).

Backfill uyarısı: Resmî Gazete backfill'i ``fetch_run.started_at``'a gerçek başlangıcı değil tarama gününü yazar. Bu yüzden
geçmiş sırası ``id`` iledir; ``finished_at - started_at`` 12 saati aşan satır "geçmiş gün" sayılır ve süresi gösterilmez.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import FetchRun, JobRun, Source
from app.services.admin_metrics import _iso
from app.services.jobs import _aware, _jsonable, job_out
from app.settings import Settings

log = logging.getLogger(__name__)

BACKFILL_GAP = timedelta(hours=12)       # started_at ↔ finished_at farkı bunu aşarsa satır bir backfill günüdür
LIVE_SLACK = timedelta(minutes=1)
RECENT_REQUESTS_WINDOW = timedelta(hours=24)
RECENT_REQUESTS_LIMIT = 10
RECENT_JOBS_LIMIT = 20
MAX_ERRORS = 5


def _channels(run: FetchRun) -> list[dict]:
    out = []
    for name, c in (run.channels or {}).items():
        c = c if isinstance(c, dict) else {}
        out.append({"name": name, "items": c.get("items", 0), "new": c.get("new", 0), "changed": c.get("changed", 0),
                    "error": c.get("error"), "warnings": len(c.get("warnings") or []),
                    "stopped": bool(c.get("stopped")), "incomplete": bool(c.get("incomplete"))})
    return out


def _scan_out(run: FetchRun, names: dict[str, str], now: datetime, tz: ZoneInfo, live_start: datetime | None) -> dict:
    started, finished = _aware(run.started_at), _aware(run.finished_at)
    backfill_day = duration = elapsed = None
    if finished is None:
        elapsed_from = started
        if live_start is not None and started < live_start - LIVE_SLACK:   # canlı backfill: started_at geçmiş bir gün
            backfill_day, elapsed_from = started.astimezone(tz).date().isoformat(), live_start
        elapsed = round((now - elapsed_from).total_seconds())
    elif finished - started > BACKFILL_GAP:
        backfill_day = started.astimezone(tz).date().isoformat()
    else:
        duration = round((finished - started).total_seconds(), 1)
    errors = list(run.errors or [])
    return {"id": run.id, "source": run.source_code, "sourceName": names.get(run.source_code, run.source_code),
            "status": run.status, "startedAt": _iso(started), "finishedAt": _iso(finished), "durationSec": duration,
            "elapsedSec": elapsed, "backfillDay": backfill_day, "requests": run.requests,
            "itemsListed": run.items_listed, "itemsNew": run.items_new, "itemsChanged": run.items_changed,
            "structureAlert": run.structure_alert, "errorCount": len(errors), "errors": errors[:MAX_ERRORS],
            "channels": _channels(run), "pendingIngest": False}


def _pending_row(code: str, names: dict[str, str]) -> dict:
    """Crawler sinyalinde "çalışıyor" görünen ama ``fetch_run``'ı henüz PostgreSQL'e aktarılmamış (ingest ≤ 1 dk) tarama."""
    return {"id": None, "source": code, "sourceName": names.get(code, code), "status": "running", "startedAt": None,
            "finishedAt": None, "durationSec": None, "elapsedSec": None, "backfillDay": None, "requests": None,
            "itemsListed": None, "itemsNew": None, "itemsChanged": None, "structureAlert": False, "errorCount": 0,
            "errors": [], "channels": [], "pendingIngest": True}


def _request_out(doc: dict, names: dict[str, str], now: datetime) -> dict:
    created, started = _aware(doc.get("created_at")), _aware(doc.get("started_at"))
    end = _aware(doc.get("finished_at")) or started or now
    code = doc.get("source_code")
    return {"id": str(doc["_id"]), "kind": doc.get("kind"), "source": code, "sourceName": names.get(code, code),
            "params": _jsonable(doc.get("params") or {}), "requestedBy": doc.get("requested_by"),
            "status": doc.get("status"), "claimedBy": doc.get("claimed_by"), "createdAt": _iso(created),
            "startedAt": _iso(started), "finishedAt": _iso(doc.get("finished_at")),
            "waitSec": round((end - created).total_seconds()) if created else None,
            "error": doc.get("error"), "result": _jsonable(doc.get("result"))}


def _mongo_state(settings: Settings, names: dict[str, str], now: datetime) -> dict:
    """Uzak mod: ``{crawler, queued, recentRequests, backfillStarts}``; Mongo hatası ``crawler.error``'a yazılır."""
    from app.mongo import REQUESTS, STATUS, crawl_db

    crawler: dict[str, Any] = {"heartbeatAt": None, "stale": True, "hosts": [], "running": [], "error": None}
    out: dict[str, Any] = {"crawler": crawler, "queued": [], "recentRequests": [], "backfillStarts": {}}
    try:
        db = crawl_db(settings)
        beats = list(db[STATUS].find({"at": {"$gte": now - timedelta(days=1)}}).sort("at", -1))
        for b in beats:
            crawler["hosts"].append({"host": b["_id"], "at": _iso(b.get("at")), "running": list(b.get("running") or [])})
        if beats:
            latest = _aware(beats[0]["at"])
            crawler["heartbeatAt"] = _iso(latest)
            crawler["stale"] = now - latest > timedelta(minutes=settings.crawler_stale_minutes)
            # yalnız canlı sinyallerin "çalışıyor" listesi güvenilirdir
            fresh = [b for b in beats if now - _aware(b["at"]) <= timedelta(minutes=settings.crawler_stale_minutes)]
            crawler["running"] = sorted({c for b in fresh for c in b.get("running") or []})
        queued = list(db[REQUESTS].find({"status": {"$in": ["pending", "running"]}}).sort("created_at", 1))
        out["queued"] = [_request_out(d, names, now) for d in queued]
        for d in queued:
            if d.get("kind") == "backfill" and d.get("status") == "running" and d.get("started_at"):
                out["backfillStarts"][d["source_code"]] = _aware(d["started_at"])
        recent = db[REQUESTS].find({"status": {"$in": ["done", "failed"]},
                                    "finished_at": {"$gte": now - RECENT_REQUESTS_WINDOW}}
                                   ).sort("finished_at", -1).limit(RECENT_REQUESTS_LIMIT)
        out["recentRequests"] = [_request_out(d, names, now) for d in recent]
    except Exception as e:  # noqa: BLE001 — Mongo erişilemezse sayfanın kalanı yine yüklenir
        log.warning("admin scans: Mongo okunamadı: %s: %s", type(e).__name__, e)
        crawler["error"] = f"{type(e).__name__}: {e}"[:300]
    return out


def collect_scans(session: Session, settings: Settings, now: datetime | None = None, *, source: str | None = None,
                  status: str | None = None, limit: int = 30) -> dict:
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(settings.timezone)
    names = {s.code: s.name for s in session.scalars(select(Source))}
    remote = settings.crawl_mode == "remote"
    mongo = _mongo_state(settings, names, now) if remote else None

    active_jobs = list(session.scalars(select(JobRun).where(JobRun.status.in_(("queued", "running")))
                                       .order_by(JobRun.id.desc())))
    live_starts: dict[str, datetime] = dict(mongo["backfillStarts"]) if mongo else {}
    for j in active_jobs:   # yerel backfill işi: gerçek başlangıç (fetch_run.started_at tarama günü olabilir)
        if j.kind == "backfill" and j.status == "running" and j.source_code and j.started_at:
            live_starts[j.source_code] = _aware(j.started_at)

    running = list(session.scalars(select(FetchRun).where(FetchRun.status == "running").order_by(FetchRun.id.desc())))
    active = [_scan_out(r, names, now, tz, live_starts.get(r.source_code)) for r in running]
    if mongo:
        seen = {r.source_code for r in running}
        active += [_pending_row(c, names) for c in mongo["crawler"]["running"] if c not in seen]

    q = select(FetchRun).where(FetchRun.status != "running")
    if source:
        q = q.where(FetchRun.source_code == source)
    if status:
        q = q.where(FetchRun.status == status)
    recent = [_scan_out(r, names, now, tz, None) for r in session.scalars(q.order_by(FetchRun.id.desc()).limit(limit))]

    recent_jobs = session.scalars(select(JobRun).where(JobRun.status.notin_(("queued", "running")))
                                  .order_by(JobRun.id.desc()).limit(RECENT_JOBS_LIMIT))
    return {"generatedAt": now.isoformat(), "crawlMode": settings.crawl_mode,
            "crawler": mongo["crawler"] if mongo else None,
            "active": active, "queued": mongo["queued"] if mongo else [],
            "recentRequests": mongo["recentRequests"] if mongo else [], "recent": recent,
            "jobs": {"active": [job_out(j, now) for j in active_jobs],
                     "recent": [job_out(j, now) for j in recent_jobs]}}
