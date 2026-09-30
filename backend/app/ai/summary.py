"""İK-4 yapılandırılmış özet üretimi ve kaynağa dayandırma (plan §6 İK-4).

  RELEVANT ─▶ SUMMARIZED   (regulation_summary yeni sürüm; eski sürüm korunur)
          └─▶ AI_FAILED    (LLM hatası ya da hiçbir cümle doğrulanamadı; yeniden denenir)

1. LLM kısa içerik cümleleri, ilgili konular ve yürürlük hükmü için kaynak metinden birebir alıntı verir.
2. Her alıntı kaynakta aranır (``grounding.find_quote``). Alıntısı bulunamayan ifade için bir kez geri bildirimle
   yeniden üretim yapılır; yine doğrulanamayan ifade özetten çıkarılır ve ``unverified_claims``'e yazılır (portal
   "doğrulanamayan ifade kaldırıldı" gösterir).
3. Yürürlük tarihi deterministik kontrol edilir; LLM tarihi ifadeyle tutarsızsa ifadeden hesaplanan kullanılır. LLM
   yürürlük hükmü bulamazsa metindeki "…yürürlüğe girer" cümlesi aranır.
4. Metin ``summary_max_input_chars``'tan uzunsa map-reduce: bölüm notları (alıntıları doğrulanır) → birleşik özet
   (alıntılar notlardan kopyalanır ve yeniden doğrulanır).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.common import RegulationContext, effective_settings, knowledge, messages, regulation_context
from app.ai.grounding import SourceText, find_effective_sentence, find_quote, resolve_effective_date
from app.ai.llm_client import LLM, LLMError
from app.collectors.dates import tr_lower
from app.db import RawDocument, Regulation, RegulationSourceLink, RegulationSummary
from app.processing.normalize import ISSUERS
from app.settings import Settings

log = logging.getLogger(__name__)
PROMPT = "v1"
AI_MAX_ATTEMPTS = 3
MIN_SOURCE_CHARS = 200   # altında özet üretilmez: kayıt "metin bekliyor" (OCR bekleyen taranmış belge vb.)
_EFFECTIVE_WORDS = re.compile(r"yürürlü|uygulan")
SOURCE_NAMES = {"RESMI_GAZETE": "Resmî Gazete", **{k: k for k in ISSUERS}}


class SummaryError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ şemalar


class Claim(BaseModel):
    sentence: str
    quotes: list[str]


class Topic(BaseModel):
    text: str
    quotes: list[str]


class EffectiveDate(BaseModel):
    value: str | None
    text: str | None
    quote: str | None


class SummaryOut(BaseModel):
    short_content: list[Claim]
    relevant_topics: list[Topic]
    effective_date: EffectiveDate


class Note(BaseModel):
    point: str
    quotes: list[str]


class NotesOut(BaseModel):
    notes: list[Note]
    effective_date: EffectiveDate


# ------------------------------------------------------------------------------------------------ doğrulama


@dataclass
class Verified:
    text: str
    evidence: list[dict]

    @property
    def ok(self) -> bool:
        return bool(self.evidence)


@dataclass
class SummaryResult:
    sentences: list[Verified]
    topics: list[Verified]
    removed: list[dict]
    effective: dict | None
    effective_date: date | None
    effective_text: str | None
    grounding_score: float
    method: str
    call_ids: list[int] = field(default_factory=list)


def verify(text: str, quotes: list[str], sources: list[SourceText], threshold: int) -> Verified:
    ev = [e for q in quotes if (e := find_quote(q, sources, threshold))]
    return Verified(text.strip(), ev)


def _verify_output(out: SummaryOut, sources, threshold) -> tuple[list[Verified], list[Verified]]:
    return ([verify(c.sentence, c.quotes, sources, threshold) for c in out.short_content if c.sentence.strip()],
            [verify(t.text, t.quotes, sources, threshold) for t in out.relevant_topics if t.text.strip()])


def sources_for(session: Session, reg: Regulation) -> list[SourceText]:
    docs = session.scalars(select(RawDocument).join(
        RegulationSourceLink, RegulationSourceLink.raw_document_id == RawDocument.id
    ).where(RegulationSourceLink.regulation_id == reg.id, RawDocument.is_latest.is_(True))
        .order_by(RawDocument.id)).all()
    return [SourceText(d.id, d.text) for d in docs if (d.text or "").strip()]


def source_links(session: Session, reg: Regulation) -> list[dict]:
    """Portal: yayım sayfası ve belge dosyası ayrı etiketlerle (plan §13-7 varsayılanı)."""
    docs = session.scalars(select(RawDocument).join(
        RegulationSourceLink, RegulationSourceLink.raw_document_id == RawDocument.id
    ).where(RegulationSourceLink.regulation_id == reg.id, RawDocument.is_latest.is_(True))
        .order_by(RawDocument.id)).all()
    out, seen = [], set()

    def add(label, url):
        if url and url not in seen:
            seen.add(url)
            out.append({"label": label, "url": url})

    for d in sorted(docs, key=lambda d: (d.role == "attachment", d.source_code != "RESMI_GAZETE", d.id)):
        name = SOURCE_NAMES.get(d.source_code, d.source_code)
        public = (d.extra or {}).get("public_url")
        if d.role == "attachment":
            add(f"Ek: {d.title or 'belge'}", d.url)
        elif d.external_id.startswith("mevzuat.gov.tr:"):
            add("Mevzuat metni (mevzuat.gov.tr)", d.url)
        elif d.source_code == "RESMI_GAZETE":
            add("Resmî Gazete", d.url)
        elif public:
            add(f"Yayım sayfası ({name})", public)
            add(f"Belge dosyası ({name})", d.url)
        elif d.content_type in ("text/html", "application/json"):
            add(f"Yayım sayfası ({name})", d.url)
        else:
            add(f"Belge dosyası ({name})", d.url)
    return out


# ------------------------------------------------------------------------------------------------ üretim


class Summarizer:
    def __init__(self, llm: LLM, settings: Settings, session: Session | None = None, force: bool = False):
        self.llm, self.s, self.session, self.force = llm, settings, session, force
        self.kn = knowledge(settings)
        self.call_ids: list[int] = []

    def _call(self, task: str, schema, system_ctx: dict, user_ctx: dict, reg_id: int | None):
        msgs = messages(task, PROMPT, system_ctx, user_ctx)
        res = self.llm.complete_json("summary" if task != "notes" else "summary_notes",
                                     f"{task}.{PROMPT}+{self.kn.version}", msgs, schema, temperature=0.1,
                                     session=self.session, regulation_id=reg_id, force=self.force)
        if res.call_id:
            self.call_ids.append(res.call_id)
        return res.outputs[0]

    def _base(self, ctx: RegulationContext, reg: Regulation) -> dict:
        rel = (reg.classification or {}).get("relevance", {})
        return {"title": ctx.title, "alt_titles": ctx.alt_titles or [], "issuer": ctx.issuer,
                "reg_type": reg.ai_reg_type or reg.reg_type, "publish_date": ctx.publish_date,
                "relevance_rationale": rel.get("rationale"), "topics": rel.get("topics", [])}

    def summarize(self, ctx: RegulationContext, reg: Regulation, sources: list[SourceText]) -> SummaryResult:
        thr = self.s.grounding_fuzzy_threshold
        system = {"institution": self.kn.taxonomy["institution"]}
        if len(ctx.text) <= self.s.summary_max_input_chars:
            method = "single"

            def produce(feedback):
                return self._call("summary", SummaryOut, system,
                                  {**self._base(ctx, reg), "text": ctx.text, "feedback": feedback}, reg.id)
        else:
            method = "map_reduce"
            notes, effective = self._map(ctx, reg, sources)

            def produce(feedback):
                return self._call("reduce", SummaryOut, system,
                                  {**self._base(ctx, reg), "notes": notes, "effective": effective,
                                   "feedback": feedback}, reg.id)

        out = produce(None)
        sentences, topics = _verify_output(out, sources, thr)
        for _ in range(self.s.summary_max_regenerations):
            failed = [v.text for v in sentences + topics if not v.ok]
            if not failed:
                break
            out = produce(failed)
            sentences, topics = _verify_output(out, sources, thr)
        total = len(sentences) + len(topics)
        score = round(sum(v.ok for v in sentences + topics) / total, 4) if total else 0.0
        removed = [{"kind": "sentence", "text": v.text} for v in sentences if not v.ok] + \
                  [{"kind": "topic", "text": v.text} for v in topics if not v.ok]
        eff, eff_date, eff_text = self._effective(out.effective_date, ctx, sources, removed)
        return SummaryResult([v for v in sentences if v.ok], [v for v in topics if v.ok], removed, eff, eff_date,
                             eff_text, score, method, list(self.call_ids))

    def _map(self, ctx: RegulationContext, reg: Regulation, sources: list[SourceText]):
        chunks = split_chunks(ctx.text, self.s.summary_chunk_chars)
        notes, effective = [], None
        for i, chunk in enumerate(chunks, 1):
            out = self._call("notes", NotesOut, {}, {"title": ctx.title, "issuer": ctx.issuer, "part": i,
                                                     "parts": len(chunks), "text": chunk}, reg.id)
            for n in out.notes:
                v = verify(n.point, n.quotes, sources, self.s.grounding_fuzzy_threshold)
                if v.ok:   # doğrulanamayan not birleşik özete girmez
                    notes.append({"point": n.point, "quotes": [e["quote"] for e in v.evidence]})
            if out.effective_date.quote and find_quote(out.effective_date.quote, sources):
                effective = out.effective_date.model_dump()
        return notes, effective

    def _effective(self, e: EffectiveDate, ctx: RegulationContext, sources, removed):
        publish = date.fromisoformat(ctx.publish_date) if ctx.publish_date else None
        llm_value = None
        if e.value:
            try:
                llm_value = date.fromisoformat(e.value)
            except ValueError:
                llm_value = None
        # Yürürlük kanıtı yürürlük/uygulama hükmü olmalı ("Kurulun 18.09.2026 tarihli toplantısında" değil)
        is_effective_clause = bool(e.quote and _EFFECTIVE_WORDS.search(tr_lower(e.quote)))
        evidence = find_quote(e.quote, sources, self.s.grounding_fuzzy_threshold) if is_effective_clause else None
        basis_text = e.quote if evidence else None
        # Kullanıcıya gösterilecek bir iddia olmadığı için yalnızca alıntısı bulunamayan yürürlük hükmü "kaldırılan"
        # sayılır; karar/yayım tarihinin yürürlük diye önerilmesi sessizce yok sayılır (deterministik arama devrede)
        if is_effective_clause and not evidence:
            removed.append({"kind": "effective_date", "text": e.text or e.quote, "reason": "alıntı bulunamadı"})
        if basis_text is None and (sentence := find_effective_sentence(ctx.text)):
            evidence = find_quote(sentence, sources, self.s.grounding_fuzzy_threshold)
            basis_text = sentence if evidence else None
            llm_value = llm_value if basis_text else None
        if basis_text is None:
            return None, None, None
        chk = resolve_effective_date(basis_text, publish, llm_value)
        return ({"value": chk.value.isoformat() if chk.value else None, "text": e.text or basis_text,
                 "basis": chk.basis, "note": chk.note, "evidence": evidence},
                chk.value, e.text or basis_text)


def split_chunks(text: str, size: int) -> list[str]:
    """Paragraf sınırlarında bölümler (map-reduce için)."""
    parts, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > size and cur:
            parts.append(cur)
            cur = ""
        while len(para) > size:
            parts.append(para[:size])
            para = para[size:]
        cur = f"{cur}\n{para}" if cur else para
    if cur.strip():
        parts.append(cur)
    return parts


# ------------------------------------------------------------------------------------------------ hat


def summarize_regulation(session: Session, llm: LLM, reg: Regulation, settings: Settings, *,
                         force: bool = False) -> RegulationSummary:
    s = effective_settings(session, settings)
    ctx = regulation_context(session, reg, 10**9)       # tam metin; uzunsa map-reduce
    sources = sources_for(session, reg)
    if not sources:
        sources = [SourceText(0, reg.title)]
    res = Summarizer(llm, s, session, force).summarize(ctx, reg, sources)
    if not res.sentences:
        raise SummaryError("hiçbir özet cümlesi kaynakla doğrulanamadı")
    prev = session.scalars(select(RegulationSummary).where(RegulationSummary.regulation_id == reg.id)
                           .order_by(RegulationSummary.version.desc())).first()
    for old in session.scalars(select(RegulationSummary).where(RegulationSummary.regulation_id == reg.id,
                                                               RegulationSummary.is_current.is_(True))):
        old.is_current = False
    row = RegulationSummary(
        regulation_id=reg.id, version=(prev.version + 1) if prev else 1, is_current=True,
        short_content=" ".join(v.text for v in res.sentences),
        short_content_evidence=[{"sentence": v.text, "evidence": v.evidence} for v in res.sentences],
        relevant_topics=[{"text": v.text, "evidence": v.evidence} for v in res.topics],
        effective_date_evidence=res.effective, grounding_score=res.grounding_score,
        unverified_claims=res.removed, source_links=source_links(session, reg), method=res.method,
        prompt_version=f"summary.{PROMPT}+{knowledge(s).version}", llm_call_ids=res.call_ids)
    session.add(row)
    reg.effective_date, reg.effective_date_text = res.effective_date, res.effective_text
    reg.processing_status = "SUMMARIZED"
    reg.extra = {k: v for k, v in (reg.extra or {}).items()
                 if k not in ("ai_attempts", "ai_error", "ai_stage", "summary_blocked")}
    session.flush()
    return row


@dataclass
class SummarizeReport:
    processed: int = 0
    summarized: int = 0
    failed: int = 0
    awaiting_text: int = 0
    removed_claims: int = 0
    avg_grounding: float | None = None
    ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def summarize_pending(session_factory: sessionmaker[Session], llm: LLM, settings: Settings, *,
                      ids: list[int] | None = None, limit: int = 50, force: bool = False) -> SummarizeReport:
    rep = SummarizeReport()
    scores = []
    with session_factory() as session:
        statuses = ("RELEVANT", "SUMMARIZED") if force else ("RELEVANT",)
        q = select(Regulation.id).where(Regulation.processing_status.in_(statuses)).order_by(Regulation.id)
        q_failed = select(Regulation.id).where(Regulation.processing_status == "AI_FAILED",
                                               Regulation.is_relevant.is_(True)).order_by(Regulation.id)
        if ids:
            q, q_failed = q.where(Regulation.id.in_(ids)), q_failed.where(Regulation.id.in_(ids))
        todo = (list(session.scalars(q)) + list(session.scalars(q_failed)))[:limit]
    for reg_id in todo:
        with session_factory() as session:
            reg = session.get(Regulation, reg_id)
            if reg.processing_status == "AI_FAILED" and (
                    (reg.extra or {}).get("ai_stage") != "summary"
                    or (reg.extra or {}).get("ai_attempts", 0) >= AI_MAX_ATTEMPTS) and not force:
                continue
            if sum(len(src.text) for src in sources_for(session, reg)) < MIN_SOURCE_CHARS:
                # LLM çağrılmaz, deneme hakkı tüketilmez; reextract/OCR sonrası metin gelince özetlenir
                reg.extra = {**{k: v for k, v in (reg.extra or {}).items() if k not in ("ai_error", "ai_stage")},
                             "summary_blocked": "metin_yok"}
                reg.processing_status = "RELEVANT"
                session.commit()
                rep.awaiting_text += 1
                continue
            rep.processed += 1
            # Doğrulamada başarısız olan özetin LLM çağrısı teknik olarak "ok" kaydedilir; yeniden denemede önbellek
            # aynı yanıtı döndürmesin diye önbellek atlanır
            retry = reg.processing_status == "AI_FAILED"
            try:
                row = summarize_regulation(session, llm, reg, settings, force=force or retry)
            except (LLMError, SummaryError) as e:
                extra = dict(reg.extra or {})
                extra.update(ai_attempts=extra.get("ai_attempts", 0) + 1, ai_error=str(e)[:500], ai_stage="summary")
                reg.extra, reg.processing_status = extra, "AI_FAILED"
                session.commit()
                rep.failed += 1
                rep.errors.append(f"#{reg_id}: {e}")
                continue
            session.commit()
            rep.summarized += 1
            rep.ids.append(reg_id)
            rep.removed_claims += len(row.unverified_claims)
            scores.append(row.grounding_score)
    rep.avg_grounding = round(sum(scores) / len(scores), 3) if scores else None
    return rep
