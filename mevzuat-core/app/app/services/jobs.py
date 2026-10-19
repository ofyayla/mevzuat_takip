"""Arka plan işi kaydı (yönetim sayfası "Taramalar ve işler").

Yönetimden veya CLI'dan başlatılan işler (yeniden işleme, başarısızları yeniden deneme, özet üretme, yerel backfill,
"şimdi tara") ``job_run`` tablosuna yazılır: ``create_job`` kaydı açar, ``track_job``/``run_job`` çalışırken durumu ve
kalp atışını günceller. Süreç ölürse kalp atışı bayatlar; ``sweep_stale`` kaydı ``interrupted`` işaretler.

Uzak moddaki taramaların kaydı Mongo ``crawl_requests``'tir; burada tutulmaz.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db import FetchRun, JobRun
from app.settings import Settings

log = logging.getLogger(__name__)

HEARTBEAT_S = 60                       # çalışan işin kalp atışı aralığı
JOB_STALE = timedelta(minutes=10)      # kalp atışı bu kadar bayatlarsa iş ölü sayılır
QUEUED_STALE = timedelta(hours=6)      # kuyrukta bu kadar bekleyen iş (worker kapalı) yarıda kalmış sayılır
ORPHAN_GRACE = timedelta(minutes=30)   # yerel tarama: Celery zaman sınırından sonra eklenen pay
LIVE_KINDS = ("run", "backfill")       # kaynak taraması yapan işler


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite zaman dilimini düşürür; karşılaştırma için UTC varsayılır."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


def _jsonable(value: Any) -> Any:
    """``result`` JSON sütununa yazılabilir olsun: tarih vb. dize olur, serileştirilemeyen ``None``."""
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return None


def create_job(session: Session, kind: str, *, source: str | None = None, params: dict | None = None,
               requested_by: str | None = None, via: str = "inline") -> int:
    """``queued`` bir kayıt açar ve commit eder (uç nokta yanıtı dönmeden portal yenilemesi işi görsün)."""
    job = JobRun(kind=kind, status="queued", via=via, source_code=source, params=_jsonable(params or {}) or {},
                 requested_by=requested_by)
    session.add(job)
    session.commit()
    return job.id


class JobHandle:
    """``track_job`` bloğunda sonucu yazmak ve işi atlandı olarak işaretlemek için."""

    def __init__(self) -> None:
        self.result: Any = None
        self.skipped: str | None = None

    def skip(self, reason: str) -> None:
        self.skipped = reason


def _beat(sf: sessionmaker[Session], job_id: int, stop: threading.Event, every: float) -> None:
    while not stop.wait(every):
        try:
            with sf() as s:
                job = s.get(JobRun, job_id)
                if job is not None and job.status == "running":
                    job.heartbeat_at = _now()
                    s.commit()
        except Exception:  # noqa: BLE001 — kalp atışı yazılamazsa iş sürer; en kötü ihtimalle ölü sayılır
            log.warning("job %s: kalp atışı yazılamadı", job_id, exc_info=True)


@contextmanager
def track_job(sf: sessionmaker[Session], job_id: int) -> Iterator[JobHandle]:
    """İşi ``running`` yapar ve kalp atışı iş parçacığını başlatır; çıkışta ``success`` / ``skipped`` / ``failed``
    yazar. İstisna yeniden fırlatılır."""
    handle = JobHandle()
    now = _now()
    with sf() as s:
        job = s.get(JobRun, job_id)
        if job is None:
            raise LookupError(f"job_run {job_id} yok")
        job.status, job.started_at, job.heartbeat_at = "running", now, now
        job.worker = f"{socket.gethostname()}:{os.getpid()}"
        s.commit()
    stop = threading.Event()
    beat = threading.Thread(target=_beat, args=(sf, job_id, stop, HEARTBEAT_S), daemon=True, name=f"job-beat-{job_id}")
    beat.start()
    status, error = "success", None
    try:
        yield handle
        if handle.skipped:
            status, error = "skipped", handle.skipped
    except BaseException as e:
        status, error = "failed", f"{type(e).__name__}: {e}"[:2000]
        raise
    finally:
        stop.set()
        beat.join(timeout=5)
        with sf() as s:
            job = s.get(JobRun, job_id)
            if job is not None:
                job.status, job.error, job.finished_at = status, error, _now()
                if status == "success":
                    job.result = _jsonable(handle.result)
                s.commit()


@contextmanager
def cli_job(sf: sessionmaker[Session], kind: str, *, source: str | None = None,
            params: dict | None = None) -> Iterator[JobHandle]:
    """CLI komutları için: kaydı açar ve izler (``via="cli"``), böylece iş yönetim sayfasında görünür."""
    with sf() as s:
        jid = create_job(s, kind, source=source, params=params, requested_by="cli", via="cli")
    with track_job(sf, jid) as h:
        yield h


def run_job(sf: sessionmaker[Session], job_id: int, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """BackgroundTasks giriş noktası: ``fn``'i izler, dönüş değerini ``result`` olarak saklar. Hata kayda geçer ve
    günlüğe yazılır; Starlette'in ikinci bir izleme çıktısı basmaması için yutulur."""
    try:
        with track_job(sf, job_id) as h:
            h.result = fn(*args, **kwargs)
            if isinstance(h.result, dict) and h.result.get("skipped"):
                h.skip(str(h.result["skipped"]))
    except Exception:  # noqa: BLE001 — kayıt ``failed`` olarak yazıldı
        log.exception("job %s başarısız", job_id)


def sweep_stale(session: Session, settings: Settings, now: datetime | None = None) -> dict[str, int]:
    """Ölü işleri ve (yalnız yerel modda) sahipsiz ``running`` tarama kayıtlarını kapatır; commit çağıranındır.

    * kalp atışı ``JOB_STALE``'den eski ``running`` iş → ``interrupted``
    * ``QUEUED_STALE``'den eski ``queued`` iş → ``interrupted`` (Celery worker kapalıydı)
    * yerel mod: canlı bir ``run``/``backfill`` işi olmayan kaynağın, ``celery_task_time_limit_s`` + pay süresini aşmış
      ``running`` tarama kaydı → ``failed``. Uzak modda crawler kendi kalıntılarını temizler (``interrupt_orphaned_runs``).
    """
    now = now or _now()
    jobs = runs = 0
    live_sources: set[str] = set()
    for job in session.scalars(select(JobRun).where(JobRun.status.in_(("queued", "running")))):
        if job.status == "running":
            beat = _aware(job.heartbeat_at) or _aware(job.started_at) or _aware(job.created_at)
            if now - beat > JOB_STALE:
                job.status, job.finished_at, job.error = "interrupted", now, "süreç iş sırasında durdu"
                jobs += 1
            elif job.kind in LIVE_KINDS and job.source_code:
                live_sources.add(job.source_code)
        elif now - _aware(job.created_at) > QUEUED_STALE:
            job.status, job.finished_at, job.error = "interrupted", now, "kuyrukta zaman aşımı"
            jobs += 1
    if settings.crawl_mode != "remote":
        limit = timedelta(seconds=settings.celery_task_time_limit_s) + ORPHAN_GRACE
        for run in session.scalars(select(FetchRun).where(FetchRun.status == "running")):
            # backfill ``started_at``'a tarama gününü yazar: canlı işi olan kaynağa dokunulmaz
            if run.source_code in live_sources or now - _aware(run.started_at) <= limit:
                continue
            run.status, run.finished_at = "failed", now
            run.errors = [*(run.errors or []), "interrupted: süreç tarama sırasında öldü"]
            runs += 1
    session.flush()
    return {"jobs": jobs, "runs": runs}


def job_out(job: JobRun, now: datetime | None = None) -> dict:
    now = now or _now()
    started, finished = _aware(job.started_at), _aware(job.finished_at)
    active = job.status in ("queued", "running")
    return {"id": job.id, "kind": job.kind, "status": job.status, "via": job.via, "source": job.source_code,
            "params": job.params or {}, "requestedBy": job.requested_by, "createdAt": _iso(job.created_at),
            "startedAt": _iso(started), "finishedAt": _iso(finished), "heartbeatAt": _iso(job.heartbeat_at),
            "elapsedSec": round((now - (started or _aware(job.created_at))).total_seconds()) if active else None,
            "durationSec": round((finished - started).total_seconds(), 1) if started and finished else None,
            "result": job.result, "error": job.error}
