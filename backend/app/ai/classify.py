"""İK-3 sınıflandırma hattı: NEW düzenleme → ilgililik (+ güven) → ilgiliyse önem derecesi.

  regulation.processing_status:  NEW ─▶ RELEVANT     (portala düşer; İK-4 özet adımı bunu ilerletir)
                                    └─▶ IRRELEVANT  (eşik altı; silinmez, değerlendirme raporunda görünür)
                                    └─▶ AI_FAILED   (LLM hatası; ``ai_max_attempts`` kez yeniden denenir)
BASELINE ve MERGED kayıtlar sınıflandırılmaz.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import relevance, severity
from app.ai.common import effective_settings, knowledge, regulation_context
from app.ai.llm_client import LLM, LLMError
from app.db import Regulation, utcnow
from app.settings import Settings

log = logging.getLogger(__name__)
AI_MAX_ATTEMPTS = 3


@dataclass
class ClassifyReport:
    processed: int = 0
    relevant: int = 0
    irrelevant: int = 0
    failed: int = 0
    by_severity: dict[str, int] = field(default_factory=dict)
    relevant_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def classify_regulation(session: Session, llm: LLM, reg: Regulation, settings: Settings, *,
                        force: bool = False) -> None:
    s = effective_settings(session, settings)
    kn = knowledge(s)
    ctx = regulation_context(session, reg, s.relevance_max_input_chars)
    rel = relevance.assess(llm, ctx, kn, s, session=session, force=force)
    reg.is_relevant, reg.relevance_score, reg.confidence_band = rel.is_relevant, rel.score, rel.band
    reg.ai_reg_type = rel.reg_type
    cls = {"relevance": {"topics": rel.topics, "criteria": rel.criteria, "rationale": rel.rationale,
                         "uncertain": rel.uncertain, **rel.components},
           "knowledge_version": kn.version}
    reg.severity = reg.severity_rationale = None
    if rel.is_relevant:
        sev = severity.assess(llm, ctx, kn, reg_type=rel.reg_type or reg.reg_type, topics=rel.topics,
                              relevance_rationale=rel.rationale, session=session, force=force)
        reg.severity, reg.severity_rationale = sev.severity, sev.rationale
        cls["severity"] = {"llm": sev.llm_severity, "rules": sev.applied_rules}
    reg.classification = cls
    reg.processing_status = "RELEVANT" if rel.is_relevant else "IRRELEVANT"
    reg.classified_at = utcnow()
    reg.extra = {k: v for k, v in (reg.extra or {}).items() if k not in ("ai_attempts", "ai_error")}


def classify_pending(session_factory: sessionmaker[Session], llm: LLM, settings: Settings, *,
                     ids: list[int] | None = None, limit: int = 100, force: bool = False,
                     include_baseline: bool = False) -> ClassifyReport:
    """``include_baseline``: ilk taramadaki eski kayıtları da geriye dönük sınıflandırır (bilinçli backfill)."""
    report = ClassifyReport()
    with session_factory() as session:
        q = select(Regulation.id).order_by(Regulation.id).limit(limit)
        if ids:
            q = q.where(Regulation.id.in_(ids))
            if not force:
                q = q.where(Regulation.processing_status.in_(("NEW", "AI_FAILED")))
        else:
            statuses = ("NEW", "AI_FAILED", "BASELINE") if include_baseline else ("NEW", "AI_FAILED")
            q = q.where(Regulation.processing_status.in_(statuses))
        todo = list(session.scalars(q))
    for reg_id in todo:
        with session_factory() as session:
            reg = session.get(Regulation, reg_id)
            if reg.processing_status == "MERGED" or (reg.processing_status == "BASELINE" and not force
                                                         and not include_baseline):
                continue
            if reg.processing_status == "AI_FAILED" and (reg.extra or {}).get("ai_attempts", 0) >= AI_MAX_ATTEMPTS \
                    and not force:
                continue
            report.processed += 1
            try:
                classify_regulation(session, llm, reg, settings, force=force)
            except LLMError as e:
                # geri alma yok: başarısız çağrının llm_call kaydı denetim için korunur; YZ alanları sıfırlanır
                reg.is_relevant = reg.relevance_score = reg.confidence_band = reg.ai_reg_type = None
                reg.severity = reg.severity_rationale = None
                extra = dict(reg.extra or {})
                extra["ai_attempts"] = extra.get("ai_attempts", 0) + 1
                extra["ai_error"] = str(e)[:500]
                reg.extra, reg.processing_status = extra, "AI_FAILED"
                session.commit()
                report.failed += 1
                report.errors.append(f"#{reg_id}: {e}")
                log.warning("düzenleme %s sınıflandırılamadı: %s", reg_id, e)
                continue
            session.commit()
            if reg.is_relevant:
                report.relevant += 1
                report.relevant_ids.append(reg.id)
                report.by_severity[reg.severity] = report.by_severity.get(reg.severity, 0) + 1
            else:
                report.irrelevant += 1
    return report
