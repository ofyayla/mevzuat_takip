"""Geriye dönük işleme (İK-7): ham içerik asla silinmediği için bir aksaklık giderildiğinde ilgili dönem yeniden
toplanır (backfill) veya seçilen aşama yeniden çalıştırılır (reprocess). Böylece aksaklık süresince veri kaybı olmaz.

- backfill: Resmî Gazete'nin fihristi tarih bazlı olduğu için geçmiş günler doğrudan taranır (gün gün). Diğer
  kaynakların liste sayfaları güncel içeriği gösterdiğinden onlar için backfill = normal tarama (sitede duran
  öğeler toplanır).
- reprocess: stage = extract | classify | summarize | match; tarih aralığı yayım tarihine göre.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.collectors.config import SourceConfig, load_sources
from app.collectors.runner import SourceCollector
from app.db import RawDocument, Regulation, RegulationSourceLink
from app.settings import Settings
from app.storage import FileSystemStorage

STAGES = ("extract", "classify", "summarize", "match")


@dataclass
class BackfillReport:
    days: int = 0
    new: int = 0
    runs: list[dict] = field(default_factory=list)


def backfill(session_factory: sessionmaker[Session], settings: Settings, code: str, date_from: date, date_to: date,
             *, fetcher_factory=None) -> BackfillReport:
    source = load_sources(settings.sources_file).get(code)
    rep = BackfillReport()
    if code != "RESMI_GAZETE":
        col = SourceCollector(source, settings, session_factory, FileSystemStorage(settings.raw_storage_dir),
                              fetcher=fetcher_factory(source) if fetcher_factory else None)
        r = col.run()
        rep.days, rep.new = 1, r.new
        rep.runs.append({"status": r.status, "new": r.new})
        return rep
    # RG: her gün için "o günün akşamı" gibi tara, geriye bakış 0 (yalnızca o gün, asıl + mükerrer)
    data = source.model_dump()
    for ch in data["channels"]:
        ch["params"] = {**ch["params"], "lookback_days": 0}
    day_source = SourceConfig.model_validate(data)
    tz = ZoneInfo(settings.timezone)
    d = date_from
    while d <= date_to:
        col = SourceCollector(day_source, settings, session_factory, FileSystemStorage(settings.raw_storage_dir),
                              fetcher=fetcher_factory(day_source) if fetcher_factory else None)
        r = col.run(now=datetime.combine(d, time(23, 0), tzinfo=tz))
        rep.days += 1
        rep.new += r.new
        rep.runs.append({"date": d.isoformat(), "status": r.status, "new": r.new})
        d += timedelta(days=1)
    return rep


def _regulation_ids(session: Session, date_from: date, date_to: date, source: str | None) -> list[int]:
    q = select(Regulation.id).where(Regulation.publish_date.between(date_from, date_to),
                                    Regulation.processing_status != "MERGED")
    if source:
        q = q.where(Regulation.id.in_(select(RegulationSourceLink.regulation_id).join(
            RawDocument, RawDocument.id == RegulationSourceLink.raw_document_id).where(
            RawDocument.source_code == source)))
    return list(session.scalars(q.order_by(Regulation.id)))


def reprocess(session_factory: sessionmaker[Session], settings: Settings, stage: str, date_from: date, date_to: date,
              *, source: str | None = None, llm=None, actor=None) -> dict:
    from app.services.audit import add_event

    if stage not in STAGES:
        raise ValueError(f"stage: {', '.join(STAGES)}")
    with session_factory() as s:
        reg_ids = _regulation_ids(s, date_from, date_to, source)
    result: dict = {"stage": stage, "regulations": len(reg_ids)}
    if stage == "extract":
        from app.processing.pipeline import DocumentProcessor

        with session_factory() as s:
            q = select(RawDocument.id).where(RawDocument.regulation_id.in_(reg_ids)) if reg_ids else None
            raw_ids = list(s.scalars(q)) if q is not None else []
        r = DocumentProcessor(settings, FileSystemStorage(settings.raw_storage_dir)).reextract(
            session_factory, ocr_pending_only=False, raw_ids=raw_ids, limit=100000)
        result.update(documents=r.processed, failed=r.failed, ocr_pending=r.ocr_pending)
    else:
        if llm is None:
            raise ValueError("bu aşama için LLM gerekli (LLM_PROVIDER)")
        if stage == "classify":
            from app.ai.classify import classify_pending

            with session_factory() as s:   # BASELINE dahil: bilinçli geriye dönük işleme
                for rid in reg_ids:
                    reg = s.get(Regulation, rid)
                    if reg.processing_status == "BASELINE":
                        reg.processing_status = "NEW"
                s.commit()
            r = classify_pending(session_factory, llm, settings, ids=reg_ids, limit=100000, force=True)
            result.update(relevant=r.relevant, irrelevant=r.irrelevant, failed=r.failed)
        elif stage == "summarize":
            from app.ai.summary import summarize_pending

            r = summarize_pending(session_factory, llm, settings, ids=reg_ids, limit=100000, force=True)
            result.update(summarized=r.summarized, failed=r.failed, awaiting_text=r.awaiting_text)
        else:
            from app.ai.unit_matching import match_pending

            r = match_pending(session_factory, llm, settings, ids=reg_ids, limit=100000, force=True)
            result.update(matched=r.matched, failed=r.failed)
    with session_factory() as s:
        for rid in reg_ids:
            reg = s.get(Regulation, rid)
            if reg.is_relevant:
                add_event(s, rid, "reprocessed", actor=actor,
                          note=f"Geriye dönük yeniden işleme: {stage} ({date_from} – {date_to})")
        s.commit()
    return result
