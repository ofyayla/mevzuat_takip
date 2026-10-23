"""İK-3 sınıflandırma hattı: NEW düzenleme → ilgililik (+ güven) → ilgiliyse önem derecesi.

  regulation.processing_status:  NEW ─▶ RELEVANT     (portala düşer; İK-4 özet adımı bunu ilerletir)
                                    └─▶ IRRELEVANT  (eşik altı; silinmez, değerlendirme raporunda görünür)
                                    └─▶ AI_FAILED   (LLM hatası; ``ai_max_attempts`` kez yeniden denenir)
BASELINE ve MERGED kayıtlar sınıflandırılmaz.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import relevance, severity
from app.ai.common import (
    UNAVAILABLE_STRIKES,
    effective_settings,
    knowledge,
    record_unavailable,
    regulation_context,
)
from app.ai.llm_client import LLM, LLMError, LLMUnavailable
from app.collectors.dates import tr_lower
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
    skipped: int = 0           # kilitli (başka işçi işliyor) ya da seçimden sonra durumu değişmiş kayıtlar
    unavailable: bool = False  # LLM sunucusuna ulaşılamadı; toplu iş kesildi, kalanlar sonraki koşuya kaldı


def classify_regulation(session: Session, llm: LLM, reg: Regulation, settings: Settings, *,
                        force: bool = False) -> None:
    s = effective_settings(session, settings)
    kn = knowledge(s)
    ctx = regulation_context(session, reg, s.relevance_max_input_chars)
    rel = relevance.assess(llm, ctx, kn, s, session=session, force=force)
    reg.is_relevant, reg.relevance_score, reg.confidence_band = rel.is_relevant, rel.score, rel.band
    reg.ai_reg_type = rel.reg_type
    if re.search(r"\btaslağı?\b", tr_lower(" ".join([reg.title, *(ctx.alt_titles or [])]))):
        reg.ai_reg_type = "Düzenleme Taslağı"   # yürürlükte değil, görüşe açık (LLM "Tebliğ" diyebiliyor)
    cls = {"relevance": {"topics": rel.topics, "criteria": rel.criteria, "rationale": rel.rationale,
                         "uncertain": rel.uncertain, "prompt_version": f"relevance.{relevance.PROMPT}+{kn.version}",
                         **rel.components},
           "knowledge_version": kn.version}
    reg.severity = reg.severity_rationale = None
    if rel.is_relevant:
        # LLM kırpılmış bağlamı görür; deterministik kurallar ise tam metne uygulanır (yürürlük cümlesi / kritik kalıp
        # metnin sonunda kalabiliyor)
        full_text = regulation_context(session, reg, 10**9).text
        sev = severity.assess(llm, ctx, kn, reg_type=rel.reg_type or reg.reg_type, topics=rel.topics,
                              relevance_rationale=rel.rationale, session=session, force=force,
                              rules_text=full_text)
        reg.severity, reg.severity_rationale = sev.severity, sev.rationale
        cls["severity"] = {"llm": sev.llm_severity, "rules": sev.applied_rules}
    reg.classification = cls
    reg.processing_status = "RELEVANT" if rel.is_relevant else "IRRELEVANT"
    if rel.is_relevant:
        from app.services.audit import ensure_detected

        session.flush()
        ensure_detected(session, reg)
    reg.classified_at = utcnow()
    reg.extra = {k: v for k, v in (reg.extra or {}).items()
                 if k not in ("ai_attempts", "ai_error", "ai_stage", "ai_unavailable_strikes")}


def _mark_failed(reg: Regulation, error: str) -> None:
    """YZ alanları sıfırlanır, deneme sayılır; ``ai_max_attempts`` dolana dek sonraki koşularda yeniden denenir."""
    reg.is_relevant = reg.relevance_score = reg.confidence_band = reg.ai_reg_type = None
    reg.severity = reg.severity_rationale = None
    extra = dict(reg.extra or {})
    extra["ai_attempts"] = extra.get("ai_attempts", 0) + 1
    extra["ai_error"] = error[:500]
    extra["ai_stage"] = "relevance"
    reg.extra, reg.processing_status = extra, "AI_FAILED"


def _still_eligible(reg: Regulation, *, force: bool, include_baseline: bool) -> bool:
    """``classify_pending``'in SQL uygunluk koşulunun Python karşılığı: kayıt seçimden sonra başka işçi tarafından
    işlenmiş ya da birleştirilmiş olabilir. ``force`` açık yeniden işlemedir; MERGED yine atlanır."""
    status, extra = reg.processing_status, reg.extra or {}
    if status == "MERGED":
        return False
    if force or status == "NEW":
        return True
    if status == "BASELINE":
        return include_baseline
    return (status == "AI_FAILED" and extra.get("ai_stage", "relevance") == "relevance"
            and extra.get("ai_attempts", 0) < AI_MAX_ATTEMPTS)


def classify_pending(session_factory: sessionmaker[Session], llm: LLM, settings: Settings, *,
                     ids: list[int] | None = None, limit: int = 100, force: bool = False,
                     include_baseline: bool = False) -> ClassifyReport:
    """``include_baseline``: ilk taramadaki eski kayıtları da geriye dönük sınıflandırır (bilinçli backfill)."""
    report = ClassifyReport()
    with session_factory() as session:
        # deneme hakkı biten (veya özet/birim aşamasında kalan) AI_FAILED kayıt SQL'de elenir; Python'da sonradan
        # elenseydi tükenmiş kayıtlar LIMIT'i doldurup yeni kayıtların önünü keserdi
        retryable = and_(Regulation.processing_status == "AI_FAILED",
                         func.coalesce(Regulation.extra["ai_stage"].as_string(), "relevance") == "relevance",
                         func.coalesce(Regulation.extra["ai_attempts"].as_integer(), 0) < AI_MAX_ATTEMPTS)
        q = select(Regulation.id).order_by(Regulation.id).limit(limit)
        if ids:
            q = q.where(Regulation.id.in_(ids))
            if not force:
                q = q.where(or_(Regulation.processing_status == "NEW", retryable))
        else:
            statuses = ("NEW", "BASELINE") if include_baseline else ("NEW",)
            q = q.where(Regulation.processing_status.in_((*statuses, "AI_FAILED")) if force
                        else or_(Regulation.processing_status.in_(statuses), retryable))
        todo = list(session.scalars(q))
    for reg_id in todo:
        with session_factory() as session:
            # Satır kilidi: zincir görevi ile Beat güvenlik ağı aynı kaydı eşzamanlı işlemesin (SQLite'ta etkisiz).
            # Kilitliyse (None) başka işçi işliyordur; kayıt seçim ile kilit arasında durum değiştirmiş olabilir
            reg = session.get(Regulation, reg_id, with_for_update={"skip_locked": True})
            if reg is None or not _still_eligible(reg, force=force, include_baseline=include_baseline):
                report.skipped += 1
                continue
            report.processed += 1
            try:
                classify_regulation(session, llm, reg, settings, force=force)
            except LLMUnavailable as e:
                session.rollback()
                report.processed -= 1
                strikes = record_unavailable(reg)
                if strikes >= UNAVAILABLE_STRIKES:
                    _mark_failed(reg, f"art arda {strikes} kez LLM zaman aşımı/hata: {e}")
                    session.commit()
                    report.failed += 1
                    report.errors.append(f"#{reg_id}: art arda LLM hatası, kayıt AI_FAILED yapıldı: {e}")
                    log.warning("düzenleme %s art arda LLM hatası verdi; AI_FAILED yapıldı, sıradakine geçiliyor",
                                reg_id)
                    continue
                session.commit()
                report.unavailable = True
                report.errors.append(f"#{reg_id}: LLM kullanılamıyor, toplu iş durduruldu: {e}")
                log.warning("LLM kullanılamıyor; sınıflandırma durduruldu (düzenleme %s): %s", reg_id, e)
                break
            except Exception as e:  # noqa: BLE001 — bir kayıttaki hata toplu işi durdurmamalı
                if isinstance(e, LLMError):
                    # geri alma yok: başarısız çağrının llm_call kaydı denetim için korunur
                    msg = str(e)
                    log.warning("düzenleme %s sınıflandırılamadı: %s", reg_id, e)
                else:
                    session.rollback()
                    msg = f"{type(e).__name__}: {e}"
                    log.exception("düzenleme %s sınıflandırılırken beklenmeyen hata", reg_id)
                _mark_failed(reg, msg)
                session.commit()
                report.failed += 1
                report.errors.append(f"#{reg_id}: {msg}")
                continue
            session.commit()
            if reg.is_relevant:
                report.relevant += 1
                report.relevant_ids.append(reg.id)
                report.by_severity[reg.severity] = report.by_severity.get(reg.severity, 0) + 1
            else:
                report.irrelevant += 1
    return report
