"""İK-2 işleme hattı: ham belge → metin çıkarma → tekilleştirme/bağlama (plan §2.2).

  raw_document:  FETCHED ─▶ EXTRACTED ─▶ LINKED
                      └───▶ EXTRACT_FAILED  (OCR servisi vb. geçici hata; ``process_max_attempts`` kez denenir)

Adımlar idempotenttir: LINKED belge yeniden işlenmez; ``reextract`` yalnızca metni yeniler, bağlantıya dokunmaz.
Desteklenmeyen içerik (ör. .doc, .xlsx) hata sayılmaz: metin boş kalır, belge başlığıyla bağlanır.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.collectors.config import ChannelConfig, SourcesFile, load_sources
from app.db import RawDocument, utcnow
from app.processing.dedupe import Confirmer, Linker, LinkResult
from app.processing.extract import UnsupportedContent, extract
from app.processing.ocr import OcrEngine, OcrError, get_ocr_engine
from app.settings import Settings
from app.storage import FileSystemStorage

log = logging.getLogger(__name__)

PENDING = ("FETCHED", "EXTRACTED")


@dataclass
class ProcessReport:
    processed: int = 0
    linked: int = 0
    new_regulations: int = 0
    merged: int = 0                 # mevcut düzenlemeye bağlanan (tekilleştirilen) ana belge
    failed: int = 0
    ocr_pending: int = 0
    new_regulation_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@lru_cache(maxsize=4)
def _sources(path: str) -> SourcesFile:
    return load_sources(Path(path))


class DocumentProcessor:
    def __init__(self, settings: Settings, storage: FileSystemStorage, *, ocr: OcrEngine | None = None,
                 confirmer: Confirmer | None = None, use_default_ocr: bool = True):
        self.settings = settings
        self.storage = storage
        self.ocr = ocr if ocr is not None or not use_default_ocr else get_ocr_engine(settings)
        self.confirmer = confirmer

    def _channel(self, raw: RawDocument) -> ChannelConfig | None:
        try:
            return _sources(str(self.settings.sources_file)).get(raw.source_code).channel(raw.channel)
        except KeyError:
            return None

    # ------------------------------------------------------------------ tek belge

    def extract_document(self, raw: RawDocument) -> bool:
        """Metni çıkarır; başarılıysa True. OCR hatasında EXTRACT_FAILED + deneme sayısı."""
        ch = self._channel(raw)
        allow_ocr = not raw.is_baseline or self.settings.ocr_baseline
        extra = dict(raw.extra or {})
        try:
            res = extract(self.storage.get(raw.storage_key), raw.content_type, raw.role, self.settings, self.ocr,
                          allow_ocr=allow_ocr, content_selector=ch.content_selector if ch else None)
        except UnsupportedContent as e:
            raw.text, raw.extraction_method, raw.extract_error = "", "unsupported", str(e)
        except (OcrError, OSError) as e:
            extra["extract_attempts"] = extra.get("extract_attempts", 0) + 1
            raw.extra, raw.processing_status, raw.extract_error = extra, "EXTRACT_FAILED", str(e)
            raw.processed_at = utcnow()
            log.warning("raw %s: metin çıkarılamadı: %s", raw.id, e)
            return False
        else:
            raw.text, raw.extraction_method = res.text, res.method
            raw.page_count, raw.ocr_confidence, raw.extract_error = res.page_count, res.ocr_confidence, None
            extra.pop("ocr_pending_pages", None)
            if res.ocr_pending_pages:
                extra["ocr_pending_pages"] = res.ocr_pending_pages
            if res.ocr_pages:
                extra["ocr_pages"] = res.ocr_pages
        raw.extra = extra
        raw.processing_status = "EXTRACTED"
        raw.processed_at = utcnow()
        return True

    def process(self, session: Session, raw: RawDocument, report: ProcessReport | None = None) -> LinkResult | None:
        report = report if report is not None else ProcessReport()
        if raw.processing_status == "LINKED":
            return None
        report.processed += 1
        if raw.text is None or raw.processing_status in ("FETCHED", "EXTRACT_FAILED"):
            if not self.extract_document(raw):
                report.failed += 1
                report.errors.append(f"raw {raw.id}: {raw.extract_error}")
                return None
        if (raw.extra or {}).get("ocr_pending_pages"):
            report.ocr_pending += 1
        try:
            res = Linker(session, self.settings, self.confirmer).link(raw)
        except LookupError as e:          # eki önce gelen, ana belgesi henüz bağlanmamış belge
            log.info("raw %s bekletiliyor: %s", raw.id, e)
            return None
        raw.processing_status = "LINKED"
        report.linked += 1
        if res.created:
            report.new_regulations += 1
            report.new_regulation_ids.append(res.regulation_id)
        elif raw.role != "attachment" and res.method != "same_document":
            report.merged += 1
        return res

    # ------------------------------------------------------------------ toplu

    def process_pending(self, session_factory: sessionmaker[Session], *, limit: int = 500,
                        source: str | None = None, raw_ids: list[int] | None = None) -> ProcessReport:
        report = ProcessReport()
        with session_factory() as session:
            q = select(RawDocument.id).where(or_(
                RawDocument.processing_status.in_(PENDING),
                RawDocument.processing_status == "EXTRACT_FAILED",
            )).order_by(RawDocument.id).limit(limit)
            if source:
                q = q.where(RawDocument.source_code == source)
            if raw_ids is not None:
                q = q.where(RawDocument.id.in_(raw_ids))
            ids = list(session.scalars(q))
        for raw_id in ids:
            with session_factory() as session:
                raw = session.get(RawDocument, raw_id)
                attempts = (raw.extra or {}).get("extract_attempts", 0)
                if raw.processing_status == "EXTRACT_FAILED" and attempts >= self.settings.process_max_attempts:
                    continue
                try:
                    self.process(session, raw, report)
                    session.commit()
                except Exception as e:  # noqa: BLE001 — bir belgedeki hata toplu işi durdurmamalı
                    session.rollback()
                    log.exception("raw %s işlenemedi", raw_id)
                    report.failed += 1
                    report.errors.append(f"raw {raw_id}: {type(e).__name__}: {e}")
        return report

    def reextract(self, session_factory: sessionmaker[Session], *, ocr_pending_only: bool = True,
                  raw_ids: list[int] | None = None, limit: int = 200) -> ProcessReport:
        """Metni yeniden çıkarır (ör. OCR servisi sonradan açıldı). Bağlantıya dokunmaz."""
        report = ProcessReport()
        with session_factory() as session:
            q = select(RawDocument).where(RawDocument.text.is_not(None)).order_by(RawDocument.id)
            if raw_ids is not None:
                q = q.where(RawDocument.id.in_(raw_ids))
            docs = [d for d in session.scalars(q)
                    if not ocr_pending_only or (d.extra or {}).get("ocr_pending_pages")][:limit]
            for raw in docs:
                status = raw.processing_status
                report.processed += 1
                if self.extract_document(raw):
                    raw.processing_status = status if status == "LINKED" else "EXTRACTED"
                    report.ocr_pending += bool((raw.extra or {}).get("ocr_pending_pages"))
                else:
                    report.failed += 1
                    raw.processing_status = status if status == "LINKED" else raw.processing_status
                session.commit()
        return report
