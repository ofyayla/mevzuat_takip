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
from app.collectors.store import CrawlStore, SqlCrawlStore
from app.db import RawDocument, Regulation, RegulationSourceLink
from app.settings import Settings
from app.storage import get_storage

STAGES = ("extract", "classify", "summarize", "match")


@dataclass
class BackfillReport:
    days: int = 0
    new: int = 0
    runs: list[dict] = field(default_factory=list)
    interrupted_at: date | None = None   # servis kapanırken yarıda kalan ilk gün (talep bu günden sürdürülür)


def local_store(session_factory: sessionmaker[Session], settings: Settings) -> SqlCrawlStore:
    return SqlCrawlStore(session_factory, get_storage(settings))


def backfill(store: CrawlStore, settings: Settings, code: str, date_from: date, date_to: date,
             *, fetcher_factory=None, should_stop=None) -> BackfillReport:
    """``store``: yerel kurulumda ``local_store(...)``; DMZ crawler'da ``MongoCrawlStore`` (LAN'dan tarama talebiyle)."""
    source = load_sources(settings.sources_file).get(code)
    rep = BackfillReport()
    if code != "RESMI_GAZETE":
        col = SourceCollector(source, settings, store, fetcher=fetcher_factory(source) if fetcher_factory else None,
                              should_stop=should_stop)
        try:
            r = col.run()
        finally:
            col.fetcher.close()
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
        if should_stop and should_stop():   # servis kapanıyor: kalan günler talep yeniden kuyruğa alınınca işlenir
            rep.interrupted_at = d
            break
        col = SourceCollector(day_source, settings, store, should_stop=should_stop,
                              fetcher=fetcher_factory(day_source) if fetcher_factory else None)
        try:
            r = col.run(now=datetime.combine(d, time(23, 0), tzinfo=tz))
        finally:
            col.fetcher.close()
        rep.new += r.new
        rep.runs.append({"date": d.isoformat(), "status": r.status, "new": r.new})
        if should_stop and should_stop():   # gün yarıda bırakılmış olabilir: yeniden taranır (idempotent)
            rep.interrupted_at = d
            break
        rep.days += 1
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
        r = DocumentProcessor(settings, get_storage(settings)).reextract(
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


# ``extra.ai_stage`` değeri → kullanıcıya gösterilen aşama adı (``STAGES``'teki karşılığı)
FAILED_STAGES = {"relevance": "classify", "summary": "summarize", "unit_match": "match"}


def failed_regulations(session: Session, *, stages: list[str] | None = None, source: str | None = None,
                       ids: list[int] | None = None) -> dict[int, str]:
    """``AI_FAILED`` düzenlemeler → hatanın aşaması (``FAILED_STAGES`` değeri). Deneme hakkı bitenler de dahildir."""
    q = select(Regulation).where(Regulation.processing_status == "AI_FAILED").order_by(Regulation.id)
    if ids:
        q = q.where(Regulation.id.in_(ids))
    if source:
        q = q.where(Regulation.id.in_(select(RegulationSourceLink.regulation_id).join(
            RawDocument, RawDocument.id == RegulationSourceLink.raw_document_id).where(
            RawDocument.source_code == source)))
    out = {}
    for reg in session.scalars(q):
        stage = FAILED_STAGES.get((reg.extra or {}).get("ai_stage", "relevance"), "classify")
        if not stages or stage in stages:
            out[reg.id] = stage
    return out


def retry_failed(session_factory: sessionmaker[Session], settings: Settings, *, llm, stages: list[str] | None = None,
                 source: str | None = None, ids: list[int] | None = None, limit: int = 1000, actor=None) -> dict:
    """YZ hattında hata veren (``AI_FAILED``) kayıtları, deneme hakkı bitmiş olanlar dahil, yeniden çalıştırır.

    Tarih aralığı gerekmez ve başarılı kayıtlara dokunulmaz (``reprocess`` ise aralıktaki her kaydı zorla yeniler).
    Deneme sayacı sıfırlanır, her kayıt hatalandığı aşamadan başlar ve zincirin sonuna kadar ilerler
    (classify → summarize → match). LLM sunucusu kapalıysa iş durur; kayıtlar yeniden denenebilir kalır."""
    from app.services.audit import add_event

    if llm is None:
        raise ValueError("bu işlem için LLM gerekli (LLM_PROVIDER)")
    unknown = set(stages or ()) - set(FAILED_STAGES.values())
    if unknown:
        raise ValueError(f"stage: {', '.join(FAILED_STAGES.values())}")
    with session_factory() as s:
        todo = dict(list(failed_regulations(s, stages=stages, source=source, ids=ids).items())[:limit])
        for reg in s.scalars(select(Regulation).where(Regulation.id.in_(todo))):
            reg.extra = {**(reg.extra or {}), "ai_attempts": 0}   # ai_error/ai_stage başarıda kendiliğinden temizlenir
        s.commit()
    by_stage = {st: [i for i, v in todo.items() if v == st] for st in FAILED_STAGES.values()}
    result: dict = {"retried": len(todo), "by_stage": {k: len(v) for k, v in by_stage.items() if v}}
    unavailable = False
    summarize_ids, match_ids = list(by_stage["summarize"]), list(by_stage["match"])
    if by_stage["classify"]:
        from app.ai.classify import classify_pending

        c = classify_pending(session_factory, llm, settings, ids=by_stage["classify"], limit=len(todo))
        summarize_ids += c.relevant_ids
        unavailable = c.unavailable
    if summarize_ids and not unavailable:
        from app.ai.summary import summarize_pending

        sm = summarize_pending(session_factory, llm, settings, ids=summarize_ids, limit=len(summarize_ids))
        match_ids += sm.ids
        unavailable = sm.unavailable
    if match_ids and not unavailable:
        from app.ai.unit_matching import match_pending

        unavailable = match_pending(session_factory, llm, settings, ids=match_ids, limit=len(match_ids)).unavailable
    with session_factory() as s:
        regs = list(s.scalars(select(Regulation).where(Regulation.id.in_(todo))))
        for reg in regs:
            if reg.processing_status != "AI_FAILED" and reg.is_relevant:
                add_event(s, reg.id, "reprocessed", actor=actor,
                          note=f"Başarısız YZ adımı yeniden denendi: {todo[reg.id]}")
        s.commit()
        result["still_failed"] = sum(1 for r in regs if r.processing_status == "AI_FAILED")
    result["recovered"] = len(todo) - result["still_failed"]
    result["llm_unavailable"] = unavailable
    return result
