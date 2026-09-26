"""Portal için düzenleme sorguları ve iş kuralları (İK-6, plan §6).

Portalda görünen kayıtlar: YZ'nin ilgili bulduğu (``is_relevant``) ve birleştirilmemiş düzenlemeler. Analizi
tamamlanmamış olanlar (özet bekleyen, OCR bekleyen, YZ hatası) da gösterilir — yüksek duyarlılık ilkesi gereği bir
kaydın analiz aşamasında takılması onu Başkanlıktan gizlememeli; ``analysisStatus`` alanı durumu belirtir.

Yanıt alanları portalın veri sözleşmesiyle (``Mevzuat Takip Portali.dc.html``) aynı adlardadır (camelCase).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session

from app.ai.unit_matching import current_summary, record_unit_decision
from app.db import (
    Outbox,
    RawDocument,
    Regulation,
    RegulationSourceLink,
    RegulationSummary,
    Unit,
    UnitSuggestion,
    utcnow,
)
from app.services.audit import Actor, add_event, trail
from app.settings import Settings

VISIBLE_STATUSES = ("RELEVANT", "SUMMARIZED", "READY", "AI_FAILED")
SEVERITY_ORDER = {"Kritik": 4, "Yüksek": 3, "Orta": 2, "Düşük": 1}
REVIEW_STATUSES = ("Bekliyor", "Onaylandı", "Reddedildi")
MANUAL_REASON = "Uzman tarafından manuel olarak atandı."
AI_FIELDS = ["type", "severity", "confidence", "summary", "topics", "suggestedUnits", "effectiveDate"]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


# ------------------------------------------------------------------------------------------------ zaman


def today(settings: Settings) -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _day_bounds_utc(d: date, settings: Settings) -> tuple[datetime, datetime]:
    tz = ZoneInfo(settings.timezone)
    start = datetime.combine(d, time.min, tzinfo=tz)
    return start.astimezone(ZoneInfo("UTC")), (start + timedelta(days=1)).astimezone(ZoneInfo("UTC"))


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime) and dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.isoformat()


# ------------------------------------------------------------------------------------------------ sorgu


def visible():
    return and_(Regulation.is_relevant.is_(True), Regulation.processing_status.in_(VISIBLE_STATUSES))


@dataclass
class ListQuery:
    source: str | None = None
    severity: str | None = None
    status: str | None = None
    unit: str | None = None
    date_range: int | None = None
    q: str | None = None
    metric: str | None = None
    page: int = 1
    page_size: int = 10


def _filters(query: ListQuery, settings: Settings) -> list:
    conds = [visible()]
    if query.source:
        conds.append(exists().where(RegulationSourceLink.regulation_id == Regulation.id,
                                    RawDocument.id == RegulationSourceLink.raw_document_id,
                                    RawDocument.source_code == query.source))
    if query.severity:
        conds.append(Regulation.severity == query.severity)
    if query.status:
        conds.append(Regulation.review_status == query.status)
    if query.unit:
        conds.append(exists().where(UnitSuggestion.regulation_id == Regulation.id,
                                    UnitSuggestion.is_active.is_(True), UnitSuggestion.unit_code == query.unit))
    if query.date_range:
        conds.append(Regulation.publish_date >= today(settings) - timedelta(days=query.date_range))
    if query.q:
        like = f"%{query.q.strip()}%"
        conds.append(or_(Regulation.title.ilike(like), exists().where(
            RegulationSummary.regulation_id == Regulation.id, RegulationSummary.is_current.is_(True),
            RegulationSummary.short_content.ilike(like))))
    if query.metric:
        conds.append(_metric_cond(query.metric, settings))
    return conds


def _metric_cond(metric: str, settings: Settings):
    t = today(settings)
    if metric == "pending":
        return Regulation.review_status == "Bekliyor"
    if metric in ("critical_high", "criticalHigh"):
        return and_(Regulation.review_status == "Bekliyor", Regulation.severity.in_(("Kritik", "Yüksek")))
    if metric == "today":
        start, end = _day_bounds_utc(t, settings)
        return and_(Regulation.detected_at >= start, Regulation.detected_at < end)
    if metric == "upcoming":
        return Regulation.effective_date.between(t, t + timedelta(days=settings.effective_soon_days))
    raise ApiError(400, "invalid_metric", f"bilinmeyen metrik: {metric}")


def list_regulations(session: Session, query: ListQuery, settings: Settings) -> dict:
    conds = _filters(query, settings)
    total = session.scalar(select(func.count()).select_from(Regulation).where(*conds))
    sev_rank = case(SEVERITY_ORDER, value=Regulation.severity, else_=0)
    rows = session.scalars(select(Regulation).where(*conds)
                           .order_by(sev_rank.desc(), Regulation.publish_date.desc().nulls_last(), Regulation.id.desc())
                           .offset((query.page - 1) * query.page_size).limit(query.page_size)).all()
    return {"items": [serialize(session, r, settings) for r in rows], "total": total, "page": query.page,
            "pageSize": query.page_size}


def stats(session: Session, settings: Settings) -> dict:
    def count(metric):
        return session.scalar(select(func.count()).select_from(Regulation).where(
            visible(), _metric_cond(metric, settings)))
    return {"pending": count("pending"), "criticalHighPending": count("critical_high"), "today": count("today"),
            "upcoming": count("upcoming")}


# ------------------------------------------------------------------------------------------------ serileştirme


def _units(session: Session, reg_id: int) -> list[dict]:
    rows = session.execute(select(UnitSuggestion, Unit).join(Unit, Unit.code == UnitSuggestion.unit_code).where(
        UnitSuggestion.regulation_id == reg_id, UnitSuggestion.is_active.is_(True)).order_by(UnitSuggestion.rank))
    return [{"code": s.unit_code, "unit": u.name, "reason": s.reason, "matchedResponsibility": s.matched_responsibility,
             "score": s.score, "origin": s.origin, "isFinal": s.is_final} for s, u in rows]


def _primary_url(session: Session, reg: Regulation, summ: RegulationSummary | None) -> str | None:
    if summ and summ.source_links:
        return summ.source_links[0]["url"]
    return session.scalar(select(RawDocument.url).join(
        RegulationSourceLink, RegulationSourceLink.raw_document_id == RawDocument.id
    ).where(RegulationSourceLink.regulation_id == reg.id, RawDocument.role != "attachment").order_by(
        (RawDocument.source_code == "RESMI_GAZETE").desc(), RawDocument.id).limit(1))


def _sources(session: Session, reg_id: int) -> list[str]:
    return sorted(set(session.scalars(select(RawDocument.source_code).join(
        RegulationSourceLink, RegulationSourceLink.raw_document_id == RawDocument.id
    ).where(RegulationSourceLink.regulation_id == reg_id))))


def analysis_status(reg: Regulation) -> str:
    if reg.processing_status == "READY":
        return "hazır"
    if reg.processing_status == "AI_FAILED":
        return "YZ analizi başarısız, yeniden denenecek"
    if (reg.extra or {}).get("summary_blocked"):
        return "metin bekleniyor (OCR)"
    return "analiz sürüyor"


def serialize(session: Session, reg: Regulation, settings: Settings, *, detail: bool = False) -> dict:
    summ = current_summary(session, reg.id)
    t = today(settings)
    detected = reg.detected_at if reg.detected_at.tzinfo else reg.detected_at.replace(tzinfo=ZoneInfo("UTC"))
    item = {
        "id": reg.id, "title": reg.title, "issuer": reg.issuer_name or reg.issuer, "issuerCode": reg.issuer,
        "sources": _sources(session, reg.id), "type": reg.ai_reg_type or reg.reg_type,
        "publishDate": reg.publish_date.isoformat() if reg.publish_date else None,
        "effectiveDate": reg.effective_date.isoformat() if reg.effective_date else None,
        "effectiveSoon": bool(reg.effective_date and t <= reg.effective_date
                              <= t + timedelta(days=settings.effective_soon_days)),
        "isToday": detected.astimezone(ZoneInfo(settings.timezone)).date() == t,
        "severity": reg.severity, "status": reg.review_status, "confidence": reg.confidence_band,
        "sourceUrl": _primary_url(session, reg, summ),
        "summary": summ.short_content if summ else None,
        "topics": [x["text"] for x in summ.relevant_topics] if summ else [],
        "suggestedUnits": _units(session, reg.id),
        "decidedBy": {"name": reg.decided_by, "title": reg.decided_by_title, "at": _iso(reg.decided_at),
                      "decision": reg.review_status} if reg.decided_at else None,
        "analysisStatus": analysis_status(reg), "detectedAt": _iso(reg.detected_at), "version": reg.row_version,
    }
    if detail:
        rel = (reg.classification or {}).get("relevance", {})
        item.update({
            "auditTrail": trail(session, reg), "aiGeneratedFields": AI_FIELDS,
            "sourceLinks": summ.source_links if summ else ([{"label": "Kaynak", "url": item["sourceUrl"]}]
                                                           if item["sourceUrl"] else []),
            "effectiveDateText": reg.effective_date_text,
            "relevance": {"score": reg.relevance_score, "rationale": rel.get("rationale"),
                          "uncertain": rel.get("uncertain"), "criteria": rel.get("criteria", [])},
            "severityRationale": reg.severity_rationale,
            "evidence": {"summary": summ.short_content_evidence, "topics": summ.relevant_topics,
                         "effectiveDate": summ.effective_date_evidence} if summ else None,
            "groundingScore": summ.grounding_score if summ else None,
            "removedClaims": summ.unverified_claims if summ else [],
            "summaryVersion": summ.version if summ else None,
            "unitMatch": {k: v for k, v in ((reg.extra or {}).get("unit_match") or {}).items()
                          if k in ("status", "dropped")},
            "decisionNote": reg.decision_note,
        })
    return item


# ------------------------------------------------------------------------------------------------ iş kuralları


def get_visible(session: Session, reg_id: int) -> Regulation:
    reg = session.get(Regulation, reg_id)
    if reg is None or not reg.is_relevant or reg.processing_status not in VISIBLE_STATUSES:
        raise ApiError(404, "not_found", "düzenleme bulunamadı")
    return reg


def _check_version(reg: Regulation, if_match: str | None) -> None:
    if if_match is not None and if_match.strip('W/"') != str(reg.row_version):
        raise ApiError(412, "version_mismatch", "kayıt başka bir kullanıcı tarafından değiştirildi; yeniden yükleyin",
                       {"current_version": reg.row_version})


def _require_pending(reg: Regulation) -> None:
    if reg.review_status != "Bekliyor":
        raise ApiError(409, "already_decided", f"kayıt zaten '{reg.review_status}' durumunda")


def mark_viewed(session: Session, reg: Regulation, actor: Actor) -> bool:
    from app.db import AuditEvent

    seen = session.scalar(select(AuditEvent.id).where(
        AuditEvent.regulation_id == reg.id, AuditEvent.event_type == "viewed", AuditEvent.actor_id == actor.id).limit(1))
    if seen is not None:
        return False
    add_event(session, reg.id, "viewed", actor=actor)
    return True


def decide(session: Session, reg: Regulation, decision: str, note: str | None, actor: Actor,
           if_match: str | None) -> None:
    if decision not in ("approve", "reject"):
        raise ApiError(400, "invalid_decision", "decision: approve | reject")
    _check_version(reg, if_match)
    _require_pending(reg)
    approved = decision == "approve"
    units = [u for u in _units(session, reg.id)]
    reg.review_status = "Onaylandı" if approved else "Reddedildi"
    reg.decided_by, reg.decided_by_title, reg.decided_at = actor.name, actor.title, utcnow()
    reg.decision_note = (note or "").strip() or None
    reg.row_version += 1
    if approved:
        for s in session.scalars(select(UnitSuggestion).where(UnitSuggestion.regulation_id == reg.id,
                                                              UnitSuggestion.is_active.is_(True))):
            s.is_final = True   # Faz 2 yönlendirmesi bu alanı kullanır
        manual_change = any(u["origin"] == "manual" for u in units)
        if units and not manual_change:   # düzeltme zaten units_changed'de kaydedildi
            record_unit_decision(session, reg, [u["code"] for u in units], corrected=False)
        auto_note = f"{', '.join(u['unit'] for u in units)} birimine yönlendirildi." if units else \
            "Onaylandı; birim ataması yok."
    else:
        auto_note = "Kayıt reddedildi, aksiyon alınmayacak."
    full_note = auto_note + (f" Not: {reg.decision_note}" if reg.decision_note else "")
    add_event(session, reg.id, "approved" if approved else "rejected", actor=actor, note=full_note,
              payload={"units": [u["code"] for u in units], "user_note": reg.decision_note})
    session.add(Outbox(event_type="record.approved" if approved else "record.rejected", aggregate_id=reg.id,
                       payload={"regulation_id": reg.id, "units": [u["code"] for u in units],
                                "decided_by": actor.name, "note": reg.decision_note}))


def change_units(session: Session, reg: Regulation, unit_codes: list[str], note: str | None, actor: Actor,
                 if_match: str | None) -> None:
    codes = list(dict.fromkeys(c for c in unit_codes if c))
    if not codes:
        raise ApiError(400, "units_required", "en az bir birim seçilmelidir")
    active = {u.code: u for u in session.scalars(select(Unit).where(Unit.code.in_(codes), Unit.active.is_(True)))}
    unknown = [c for c in codes if c not in active]
    if unknown:
        raise ApiError(400, "unknown_unit", "bilinmeyen veya pasif birim", {"unit_codes": unknown})
    _check_version(reg, if_match)
    _require_pending(reg)
    current = {s.unit_code: s for s in session.scalars(select(UnitSuggestion).where(
        UnitSuggestion.regulation_id == reg.id, UnitSuggestion.is_active.is_(True)))}
    old_names = [s["unit"] for s in _units(session, reg.id)]
    if list(current) == codes:
        return
    for code, s in current.items():
        if code not in codes:
            s.is_active = False
    for rank, code in enumerate(codes, 1):
        if code in current:
            current[code].rank = rank           # mevcut YZ gerekçesi korunur
        else:
            session.add(UnitSuggestion(regulation_id=reg.id, unit_code=code, rank=rank, score=None,
                                       reason=MANUAL_REASON, origin="manual"))
    reg.row_version += 1
    new_names = [active[c].name for c in codes]
    session.flush()
    record_unit_decision(session, reg, codes, corrected=True)   # öğrenme döngüsü (ağırlık 2)
    msg = f'Önerilen birim "{", ".join(old_names) or "-"}" iken "{", ".join(new_names)}" olarak güncellendi.'
    add_event(session, reg.id, "units_changed", actor=actor, note=msg + (f" Not: {note.strip()}" if note else ""),
              payload={"old_units": list(current), "new_units": codes})
