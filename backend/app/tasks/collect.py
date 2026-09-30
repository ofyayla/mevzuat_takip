"""Toplama görevleri (Celery)."""
from __future__ import annotations

import logging
from functools import lru_cache

from app.collectors.config import load_sources
from app.collectors.locks import LockBusy, source_lock
from app.collectors.runner import SourceCollector
from app.db import make_sessionmaker
from app.settings import get_settings
from app.storage import FileSystemStorage
from app.worker import app

log = logging.getLogger(__name__)


@lru_cache
def _session_factory():
    return make_sessionmaker(get_settings().database_url)


@app.task(name="app.tasks.collect.collect_source", bind=True, max_retries=0)
def collect_source(self, code: str, channels: list[str] | None = None, max_details: int = 200) -> dict:
    """Bir kaynağı tarar. Aynı kaynak için eşzamanlı ikinci çalıştırma kilitle engellenir."""
    settings = get_settings()
    source = load_sources(settings.sources_file).get(code)
    try:
        with source_lock(code, redis_url=settings.redis_url, lock_dir=settings.lock_dir):
            collector = SourceCollector(source, settings, _session_factory(),
                                        FileSystemStorage(settings.raw_storage_dir))
            try:
                report = collector.run(channels=channels, max_details=max_details)
            finally:
                collector.fetcher.close()
    except LockBusy:
        log.info("%s: başka bir tarama sürüyor, atlandı", code)
        return {"source": code, "status": "skipped"}
    if report.new or report.changed:
        from app.tasks.process import process_pending

        process_pending.delay(source=code)
    return {"source": code, "status": report.status, "run_id": report.run_id, "new": report.new,
            "changed": report.changed, "structure_alert": any(c.empty_listing for c in report.channels)}


@app.task(name="app.tasks.collect.collect_all")
def collect_all() -> list[str]:
    codes = [s.code for s in load_sources(get_settings().sources_file).enabled()]
    for code in codes:
        collect_source.delay(code)
    return codes
