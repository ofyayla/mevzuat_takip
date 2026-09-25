"""İK-2 işleme görevleri (Celery): metin çıkarma + tekilleştirme.

``collect_source`` bittiğinde yeni belgeler için ``process_pending`` tetiklenir; ayrıca Beat her 15 dakikada bir
bekleyen/yeniden denenecek belgeleri tarar (OCR servisi geçici olarak kapalıysa vb.).
"""
from __future__ import annotations

from functools import lru_cache

from app.ai.dedupe_confirm import make_confirmer
from app.ai.llm_client import get_llm
from app.db import make_sessionmaker
from app.processing.pipeline import DocumentProcessor
from app.settings import get_settings
from app.storage import FileSystemStorage
from app.worker import app


@lru_cache
def _session_factory():
    return make_sessionmaker(get_settings().database_url)


@app.task(name="app.tasks.process.process_pending")
def process_pending(source: str | None = None, limit: int = 500) -> dict:
    s = get_settings()
    sf = _session_factory()
    llm = get_llm(s)
    confirmer = make_confirmer(llm, sf, s) if llm is not None else None
    report = DocumentProcessor(s, FileSystemStorage(s.raw_storage_dir), confirmer=confirmer).process_pending(
        sf, limit=limit, source=source)
    if report.new_regulation_ids and llm is not None:
        from app.tasks.ai import classify_regulations

        classify_regulations.delay(ids=report.new_regulation_ids)
    return {k: v for k, v in report.__dict__.items() if k != "errors"} | {"errors": report.errors[:20]}
