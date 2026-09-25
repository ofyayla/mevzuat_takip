"""İK-2 işleme görevleri (Celery): metin çıkarma + tekilleştirme.

``collect_source`` bittiğinde yeni belgeler için ``process_pending`` tetiklenir; ayrıca Beat her 15 dakikada bir
bekleyen/yeniden denenecek belgeleri tarar (OCR servisi geçici olarak kapalıysa vb.).
"""
from __future__ import annotations

from functools import lru_cache

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
    report = DocumentProcessor(s, FileSystemStorage(s.raw_storage_dir)).process_pending(
        _session_factory(), limit=limit, source=source)
    # İK-3: report.new_regulation_ids (BASELINE olmayanlar) sınıflandırma kuyruğuna verilecek
    return {k: v for k, v in report.__dict__.items() if k != "errors"} | {"errors": report.errors[:20]}
