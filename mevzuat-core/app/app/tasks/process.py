"""İK-2 işleme görevleri (Celery): metin çıkarma + tekilleştirme.

``collect_source`` bittiğinde yeni belgeler için ``process_pending`` tetiklenir; ayrıca Beat her 15 dakikada bir
bekleyen/yeniden denenecek belgeleri tarar (OCR servisi geçici olarak kapalıysa vb.). Beat koşusu ayrıca OCR
kapalıyken bağlanmış taranmış belgeleri küçük gruplar halinde OCR'dan geçirir (``catch_up_ocr``).
"""
from __future__ import annotations

from functools import lru_cache

from app.ai.dedupe_confirm import make_confirmer
from app.ai.llm_client import get_llm
from app.db import make_sessionmaker
from app.processing.pipeline import DocumentProcessor
from app.settings import get_settings
from app.storage import get_storage
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
    proc = DocumentProcessor(s, get_storage(s), confirmer=confirmer)
    report = proc.process_pending(sf, limit=limit, source=source)
    if report.new_regulation_ids and llm is not None:
        from app.tasks.ai import classify_regulations

        classify_regulations.delay(ids=report.new_regulation_ids)
    out = {k: v for k, v in report.__dict__.items() if k != "errors"} | {"errors": report.errors[:20]}
    if source is None:   # yalnızca Beat koşusu: OCR kapalıyken birikmiş taranmış belgeler (eşzamanlı tekrar olmasın)
        ocr_rep, refreshed = proc.catch_up_ocr(sf)
        if refreshed and llm is not None:
            from app.tasks.ai import summarize_regulations

            summarize_regulations.delay(ids=refreshed)   # özet bitince birim eşleştirme kendiliğinden tetiklenir
        out["ocr_catch_up"] = {"documents": ocr_rep.processed, "failed": ocr_rep.failed,
                               "still_pending": ocr_rep.ocr_pending, "refreshed_regulations": refreshed}
    return out
