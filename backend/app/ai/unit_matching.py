"""İK-5 birim eşleştirme önerisi (plan §6 İK-5).

- Birim sayısı onlar mertebesinde (şema: 44) olduğu için tüm görev tanımları tek prompt'a sığar; vektör arama yok.
- ``unit_code`` JSON şemada ``enum`` ile kapalı listeye bağlanır.
- ``matched_responsibility`` birimin görev maddelerinden birinde bulunmalıdır (rapidfuzz ≥ eşik); bulunamazsa en yakın
  madde aranır, o da tutmazsa öneri elenir (gerekçesiz öneri gösterilmez).
- Skoru < ``unit_min_score`` olan öneri elenir; en fazla ``unit_max_suggestions``. Hiç öneri kalmazsa varsayılan birim
  atanmaz, liste boş kalır (portal: "Birim önerilemedi"; plan §13-10).
- Öğrenme döngüsü: Başkanlığın onayı (``user_decision``, ağırlık 1) ve düzeltmesi (``user_correction``, ağırlık 2)
  ``fewshot_example`` olur; yeni düzenleme için başlık/konu benzerliğine göre en yakın örnekler prompt'a girer.

Durum: SUMMARIZED → READY (portalda "Bekliyor"). Metni olmadığı için özetlenemeyen (RELEVANT + summary_blocked)
düzenlemeye de başlık ve ilgililik gerekçesiyle öneri üretilir; durumu RELEVANT kalır.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

import yaml
from pydantic import BaseModel, create_model
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.common import effective_settings, knowledge, messages, regulation_context
from app.ai.llm_client import LLM, LLMError
from app.collectors.dates import tr_lower
from app.db import FewshotExample, Regulation, RegulationSummary, Unit, UnitSuggestion
from app.settings import Settings

log = logging.getLogger(__name__)
PROMPT = "v1"
AI_MAX_ATTEMPTS = 3


# ------------------------------------------------------------------------------------------------ bilgi tabanı


def sync_units(session: Session, settings: Settings) -> tuple[int, int]:
    """config/units.yaml → unit tablosu (upsert; dosyada olmayan birim pasifleşir). (eklenen/güncellenen, pasif)."""
    data = yaml.safe_load(settings.units_file.read_text("utf-8"))
    seen, changed = set(), 0
    for u in data["units"]:
        row = session.get(Unit, u["code"]) or Unit(code=u["code"])
        row.name, row.group_name = u["name"], u.get("group")
        row.responsibilities, row.keywords = u.get("responsibilities", []), u.get("keywords", [])
        row.regulatory_areas, row.active = u.get("regulatory_areas", []), True
        row.source_doc_ref = data.get("source_doc_ref")
        session.add(row)
        seen.add(u["code"])
        changed += 1
    deactivated = 0
    for row in session.scalars(select(Unit).where(Unit.code.not_in(seen), Unit.active.is_(True))):
        row.active = False
        deactivated += 1
    session.flush()
    return changed, deactivated


def active_units(session: Session, settings: Settings) -> list[Unit]:
    units = session.scalars(select(Unit).where(Unit.active.is_(True)).order_by(Unit.code)).all()
    if not units:
        sync_units(session, settings)
        units = session.scalars(select(Unit).where(Unit.active.is_(True)).order_by(Unit.code)).all()
    return list(units)


# ------------------------------------------------------------------------------------------------ few-shot


def example_text(reg: Regulation, summary: RegulationSummary | None) -> str:
    topics = [t["text"] for t in (summary.relevant_topics if summary else [])]
    return f"{reg.title} | konular: {', '.join(topics) or '-'}"


def select_examples(session: Session, text: str, k: int) -> list[dict]:
    rows = session.scalars(select(FewshotExample).where(FewshotExample.task == "unit_match",
                                                        FewshotExample.active.is_(True))).all()
    scored = sorted(rows, key=lambda r: fuzz.token_set_ratio(tr_lower(text), tr_lower(r.input_text)) * r.weight,
                    reverse=True)
    return [{"input_text": r.input_text, "units": r.expected_output.get("units", []), "origin": r.origin}
            for r in scored[:k]]


def record_unit_decision(session: Session, reg: Regulation, final_units: list[str], *, corrected: bool) -> None:
    """İK-6 çağırır: Başkanlığın birim kararı örnek havuzuna girer (düzeltme ağırlığı 2)."""
    summary = current_summary(session, reg.id)
    session.add(FewshotExample(task="unit_match", regulation_id=reg.id, input_text=example_text(reg, summary),
                               expected_output={"units": final_units},
                               origin="user_correction" if corrected else "user_decision",
                               weight=2.0 if corrected else 1.0))


def current_summary(session: Session, reg_id: int) -> RegulationSummary | None:
    return session.scalars(select(RegulationSummary).where(RegulationSummary.regulation_id == reg_id,
                                                           RegulationSummary.is_current.is_(True))).first()


# ------------------------------------------------------------------------------------------------ eşleştirme


class _Suggestion(BaseModel):
    unit_code: str
    score: float
    matched_responsibility: str
    reason: str


def _stem_match(keyword: str, words: list[str]) -> bool:
    """Türkçe çekim eklerine dayanıklı eşleşme: anahtarın her kelimesinin kökü (son 2 harf atılmış, en az 4 harf)
    metinde ardışık kelimelerin başında geçmeli ("kredi kartı limiti" ~ "kredi kartı limitlerinin")."""
    kws = [k[:max(4, len(k) - 2)] if len(k) > 4 else k for k in re.findall(r"\w+", tr_lower(keyword))]
    n = len(kws)
    return any(all(words[i + j].startswith(kws[j]) for j in range(n)) for i in range(len(words) - n + 1))


def keyword_candidates(units: list[Unit], text: str, k: int = 6) -> list[dict]:
    """Deterministik ön eleme ipucu: birim anahtar kelimelerinin düzenleme metninde geçme sayısı."""
    words = re.findall(r"\w+", tr_lower(text))
    scored = []
    for u in units:
        hits = [kw for kw in u.keywords if _stem_match(kw, words)]
        if hits:
            scored.append({"code": u.code, "hits": hits})
    return sorted(scored, key=lambda x: -len(x["hits"]))[:k]


def output_model(codes: list[str]) -> type[BaseModel]:
    code_t = Literal[tuple(codes)]  # type: ignore[valid-type]
    item = create_model("UnitSuggestionOut", unit_code=(code_t, ...), score=(float, ...),
                        matched_responsibility=(str, ...), reason=(str, ...))
    return create_model("UnitMatchOut", suggestions=(list[item], ...))


@dataclass
class MatchResult:
    kept: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    call_id: int | None = None


def validate_suggestions(raw: list, units: dict[str, Unit], settings: Settings,
                         reg_areas: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """``reg_areas``: İK-3'ün düzenleme için bulduğu konu kodları. Birimin düzenleme alanlarıyla hiç örtüşmeyen öneri
    elenir — model görev maddesini birebir kopyalayıp anlamca ilgisiz bir birim önerebiliyor (canlı: KVKK kararına
    AML maddesiyle Mevzuat ve Uyum)."""
    kept, dropped, seen = [], [], set()
    for s in sorted(raw, key=lambda x: -x.score):
        unit = units.get(s.unit_code)
        if unit is None or s.unit_code in seen:
            continue
        unit_areas = set(getattr(unit, "regulatory_areas", None) or [])
        if reg_areas and unit_areas and not (reg_areas & unit_areas):
            dropped.append({"unit_code": s.unit_code, "score": s.score, "why": "alan uyuşmazlığı",
                            "unit_areas": sorted(unit_areas), "regulation_areas": sorted(reg_areas)})
            continue
        if s.score < settings.unit_min_score:
            dropped.append({"unit_code": s.unit_code, "score": s.score, "why": "düşük skor"})
            continue
        best, best_score = None, 0
        for r in unit.responsibilities:
            sc = fuzz.partial_ratio(tr_lower(s.matched_responsibility), tr_lower(r))
            if sc > best_score:
                best, best_score = r, sc
        if best is None or best_score < settings.unit_responsibility_threshold:
            dropped.append({"unit_code": s.unit_code, "score": s.score, "why": "görev tanımında karşılığı yok",
                            "claimed": s.matched_responsibility})
            continue
        seen.add(s.unit_code)
        kept.append({"unit_code": s.unit_code, "score": round(min(1.0, max(0.0, s.score)), 3),
                     "matched_responsibility": best, "reason": s.reason.strip()})
        if len(kept) >= settings.unit_max_suggestions:
            break
    return kept, dropped


def match_regulation(session: Session, llm: LLM, reg: Regulation, settings: Settings, *,
                     force: bool = False) -> MatchResult:
    s = effective_settings(session, settings)
    kn = knowledge(s)
    units = active_units(session, s)
    by_code = {u.code: u for u in units}
    summary = current_summary(session, reg.id)
    ctx = regulation_context(session, reg, 2000)
    rel = (reg.classification or {}).get("relevance", {})
    topics = [t["text"] for t in summary.relevant_topics] if summary else rel.get("topics", [])
    examples = select_examples(session, example_text(reg, summary), s.unit_fewshot_count)
    hint_text = " ".join([reg.title, *(ctx.alt_titles or []), " ".join(topics),
                          summary.short_content if summary else "", rel.get("rationale") or ""])
    candidates = keyword_candidates(units, hint_text)
    msgs = messages("unit_match", PROMPT,
                    {"institution": kn.taxonomy["institution"], "units": units, "max_suggestions": s.unit_max_suggestions},
                    {"examples": examples, "title": reg.title, "alt_titles": ctx.alt_titles or [],
                     "issuer": reg.issuer_name or reg.issuer, "reg_type": reg.ai_reg_type or reg.reg_type,
                     "severity": reg.severity, "topics": topics, "summary": summary.short_content if summary else None,
                     "relevance_rationale": rel.get("rationale"), "candidates": candidates})
    units_version = yaml.safe_load(s.units_file.read_text("utf-8")).get("version", "?")
    res = llm.complete_json("unit_match", f"unit_match.{PROMPT}+{kn.version}+{units_version}", msgs,
                            output_model(list(by_code)), temperature=0.1, session=session, regulation_id=reg.id,
                            force=force)
    reg_areas = set(rel.get("topics") or [])
    kept, dropped = validate_suggestions(res.outputs[0].suggestions, by_code, s, reg_areas)
    for old in session.scalars(select(UnitSuggestion).where(UnitSuggestion.regulation_id == reg.id,
                                                            UnitSuggestion.origin == "ai",
                                                            UnitSuggestion.is_active.is_(True))):
        old.is_active = False
    for i, k in enumerate(kept, 1):
        session.add(UnitSuggestion(regulation_id=reg.id, unit_code=k["unit_code"], rank=i, score=k["score"],
                                   reason=k["reason"], matched_responsibility=k["matched_responsibility"],
                                   origin="ai", llm_call_id=res.call_id))
    extra = {k: v for k, v in (reg.extra or {}).items() if k not in ("ai_attempts", "ai_error", "ai_stage")}
    extra["unit_match"] = {"status": "önerildi" if kept else "önerilemedi", "dropped": dropped,
                           "examples_used": len(examples), "keyword_candidates": [c["code"] for c in candidates]}
    reg.extra = extra
    if reg.processing_status == "SUMMARIZED":
        reg.processing_status = "READY"
    session.flush()
    return MatchResult(kept, dropped, res.call_id)


@dataclass
class MatchReport:
    processed: int = 0
    matched: int = 0
    no_suggestion: int = 0
    failed: int = 0
    suggestions: int = 0
    errors: list[str] = field(default_factory=list)


def match_pending(session_factory: sessionmaker[Session], llm: LLM, settings: Settings, *,
                  ids: list[int] | None = None, limit: int = 100, force: bool = False) -> MatchReport:
    rep = MatchReport()
    with session_factory() as session:
        active_units(session, settings)
        session.commit()
        q = select(Regulation).where(Regulation.is_relevant.is_(True)).order_by(Regulation.id)
        if ids:
            q = q.where(Regulation.id.in_(ids))
        todo = []
        for reg in session.scalars(q):
            extra = reg.extra or {}
            if force:
                todo.append(reg.id)
            elif reg.processing_status == "AI_FAILED":
                if extra.get("ai_stage") == "unit_match" and extra.get("ai_attempts", 0) < AI_MAX_ATTEMPTS:
                    todo.append(reg.id)
            elif "unit_match" not in extra and (
                    reg.processing_status == "SUMMARIZED"
                    or (reg.processing_status == "RELEVANT" and extra.get("summary_blocked"))):
                todo.append(reg.id)
        todo = todo[:limit]
    for reg_id in todo:
        with session_factory() as session:
            reg = session.get(Regulation, reg_id)
            rep.processed += 1
            retry = reg.processing_status == "AI_FAILED"
            if retry:   # hatadan önceki duruma dön: özeti varsa SUMMARIZED (→ READY), yoksa RELEVANT
                reg.processing_status = "SUMMARIZED" if current_summary(session, reg.id) else "RELEVANT"
            try:
                res = match_regulation(session, llm, reg, settings, force=force or retry)
            except LLMError as e:
                extra = dict(reg.extra or {})
                extra.update(ai_attempts=extra.get("ai_attempts", 0) + 1, ai_error=str(e)[:500], ai_stage="unit_match")
                reg.extra, reg.processing_status = extra, "AI_FAILED"
                session.commit()
                rep.failed += 1
                rep.errors.append(f"#{reg_id}: {e}")
                continue
            session.commit()
            rep.matched += bool(res.kept)
            rep.no_suggestion += not res.kept
            rep.suggestions += len(res.kept)
    return rep
