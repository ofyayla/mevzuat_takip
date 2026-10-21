"""Portal için düzenleme sorguları ve iş kuralları (İK-6, plan §6).

Portalda görünen kayıtlar: YZ'nin ilgili bulduğu (``is_relevant``) ve birleştirilmemiş düzenlemeler. Analizi
tamamlanmamış olanlar (özet bekleyen, OCR bekleyen, YZ hatası) da gösterilir — yüksek duyarlılık ilkesi gereği bir
kaydın analiz aşamasında takılması onu Başkanlıktan gizlememeli; ``analysisStatus`` alanı durumu belirtir.

Yanıt alanları portalın veri sözleşmesiyle (``Mevzuat Takip Portali.dc.html``) aynı adlardadır (camelCase).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, exists, func, or_, select, text
from sqlalchemy.exc import OperationalError
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
from app.processing.normalize import SEARCH_FOLD_PAIRS, search_fold
from app.services.audit import Actor, add_event, trail
from app.settings import Settings

VISIBLE_STATUSES = ("RELEVANT", "SUMMARIZED", "READY", "AI_FAILED")
SEVERITY_ORDER = {"Kritik": 4, "Yüksek": 3, "Orta": 2, "Düşük": 1}
REVIEW_STATUSES = ("Bekliyor", "Onaylandı", "Reddedildi")
PREPARING_STATUSES = ("RELEVANT", "SUMMARIZED", "AI_FAILED")   # YZ işlemi bitmemiş: görünür ama karar kilitli
QUEUES = ("yeni", "birikim", "tum", "hazirlaniyor")
EFFECTIVE_FILTERS = ("7", "30", "in_force", "none")
AI_MAX_ATTEMPTS = 3
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
    sort: str | None = None
    direction: str = "desc"
    queue: str | None = None            # yeni | birikim | tum | hazirlaniyor
    effective: str | None = None        # "7,30,in_force,none" (virgülle çoklu, VEYA)
    needs_unit: bool = False
    unit_changed: bool = False          # arşiv: onayda birim YZ önerisinden farklı
    edited: bool = False                # arşiv: bilgileri uzman tarafından düzenlenmiş
    ids: list[int] | None = None


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _no_unit_cond(settings: Settings):
    """Güvenilir birim önerisi yok: aktif öneri hiç yok ya da hepsi ``unit_weak_score`` altında (elle atananlar sayılır)."""
    return ~exists().where(UnitSuggestion.regulation_id == Regulation.id, UnitSuggestion.is_active.is_(True),
                           or_(UnitSuggestion.origin == "manual", UnitSuggestion.score >= settings.unit_weak_score))


def _queue_cond(queue: str, settings: Settings):
    if queue == "hazirlaniyor":
        return Regulation.processing_status.in_(PREPARING_STATUSES)
    ready = and_(Regulation.review_status == "Bekliyor", Regulation.processing_status == "READY")
    if queue == "tum":
        return ready
    start, _ = _day_bounds_utc(today(settings) - timedelta(days=settings.new_window_days), settings)
    return and_(ready, Regulation.detected_at >= start) if queue == "yeni" else and_(ready, Regulation.detected_at < start)


def _effective_cond(values: list[str], settings: Settings):
    t = today(settings)
    parts = {"7": Regulation.effective_date.between(t, t + timedelta(days=7)),
             "30": Regulation.effective_date.between(t, t + timedelta(days=30)),
             "in_force": Regulation.effective_date < t, "none": Regulation.effective_date.is_(None)}
    return or_(*(parts[v] for v in values))


def _fold_sql(col):
    """``search_fold``'un SQL karşılığı. SQLite'ın ``lower()``'ı yalnız ASCII'yi küçülttüğü ve PostgreSQL'in
    ``ILIKE``'ı veritabanı yereline (çoğu kurulumda en_US) göre I → i yaptığı için eşleme açıkça uygulanır."""
    for src, dst in SEARCH_FOLD_PAIRS:
        col = func.replace(col, src, dst)
    return func.lower(col)


def _like_escape(value: str) -> str:
    """Kullanıcı girdisindeki ``%`` ve ``_`` joker olmasın."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filters(query: ListQuery, settings: Settings) -> list:
    conds = [visible()]
    if query.ids is not None:
        conds.append(Regulation.id.in_(query.ids))
    if query.queue:
        if query.queue not in QUEUES:
            raise ApiError(400, "invalid_queue", f"queue: {', '.join(QUEUES)}")
        conds.append(_queue_cond(query.queue, settings))
    if query.source:
        conds.append(exists().where(RegulationSourceLink.regulation_id == Regulation.id,
                                    RawDocument.id == RegulationSourceLink.raw_document_id,
                                    RawDocument.source_code.in_(_csv(query.source))))
    if query.severity:
        conds.append(Regulation.severity.in_(_csv(query.severity)))
    if query.status:
        conds.append(Regulation.review_status == query.status)
    if query.unit:
        conds.append(exists().where(UnitSuggestion.regulation_id == Regulation.id,
                                    UnitSuggestion.is_active.is_(True), UnitSuggestion.unit_code.in_(_csv(query.unit))))
    if query.effective:
        values = _csv(query.effective)
        bad = [v for v in values if v not in EFFECTIVE_FILTERS]
        if bad or not values:
            raise ApiError(400, "invalid_effective", f"effective: {', '.join(EFFECTIVE_FILTERS)}")
        conds.append(_effective_cond(values, settings))
    if query.needs_unit:
        conds.append(_no_unit_cond(settings))
    if query.unit_changed:
        conds.append(exists().where(UnitSuggestion.regulation_id == Regulation.id, or_(
            and_(UnitSuggestion.origin == "manual", UnitSuggestion.is_active.is_(True)),
            and_(UnitSuggestion.origin == "ai", UnitSuggestion.is_active.is_(False)))))
    if query.edited:
        from app.db import AuditEvent

        conds.append(exists().where(AuditEvent.regulation_id == Regulation.id, AuditEvent.event_type == "content_edited"))
    if query.date_range:
        conds.append(Regulation.publish_date >= today(settings) - timedelta(days=query.date_range))
    if query.q and query.q.strip():
        like = "%" + _like_escape(search_fold(query.q.strip())) + "%"
        conds.append(or_(_fold_sql(Regulation.title).like(like, escape="\\"), exists().where(
            RegulationSummary.regulation_id == Regulation.id, RegulationSummary.is_current.is_(True),
            _fold_sql(RegulationSummary.short_content).like(like, escape="\\"))))
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


def _viewed_ids(session: Session, reg_ids: list[int], actor_id: str | None) -> set[int]:
    """Bu kullanıcının ayrıntısını açtığı kayıtlar (``viewed`` denetim olayı): listede okunmamışları ayırt etmek için."""
    from app.db import AuditEvent

    if not actor_id or not reg_ids:
        return set()
    return set(session.scalars(select(AuditEvent.regulation_id).where(
        AuditEvent.regulation_id.in_(reg_ids), AuditEvent.event_type == "viewed", AuditEvent.actor_id == actor_id)))


def _priority(settings: Settings):
    """Öncelik skoru (portalın varsayılan sırası): önem × yürürlük aciliyeti + birimsiz + bugün gelen."""
    t = today(settings)
    sev = case(SEVERITY_ORDER, value=Regulation.severity, else_=0)
    urgency = case((Regulation.effective_date.is_(None), 1.0), (Regulation.effective_date < t, 1.8),
                   (Regulation.effective_date <= t + timedelta(days=7), 3.0),
                   (Regulation.effective_date <= t + timedelta(days=30), 2.2),
                   (Regulation.effective_date <= t + timedelta(days=90), 1.4), else_=1.0)
    start, _ = _day_bounds_utc(t - timedelta(days=1), settings)
    return (sev * urgency + case((_no_unit_cond(settings), 0.6), else_=0.0)
            + case((Regulation.detected_at >= start, 0.4), else_=0.0))


def _order(query: ListQuery, settings: Settings) -> tuple:
    sev_rank = case(SEVERITY_ORDER, value=Regulation.severity, else_=0)
    default_order = (sev_rank.desc(), Regulation.publish_date.desc().nulls_last(), Regulation.id.desc())
    top_unit = (select(Unit.name).join(UnitSuggestion, UnitSuggestion.unit_code == Unit.code)
                .where(UnitSuggestion.regulation_id == Regulation.id, UnitSuggestion.is_active.is_(True))
                .order_by(UnitSuggestion.rank).limit(1).correlate(Regulation).scalar_subquery())
    sort_cols = {"issuer": func.coalesce(Regulation.issuer_name, Regulation.issuer), "unit": top_unit,
                 "title": Regulation.title, "publish_date": Regulation.publish_date,
                 "effective_date": Regulation.effective_date, "severity": sev_rank,
                 "status": Regulation.review_status}
    sort_cols["detected_at"] = Regulation.detected_at
    sort_cols["decided_at"] = Regulation.decided_at
    if query.sort == "priority":
        primary = _priority(settings).asc() if query.direction == "asc" else _priority(settings).desc()
        return (primary, Regulation.detected_at.desc(), Regulation.id.desc())
    if query.sort in sort_cols:
        col = sort_cols[query.sort]
        primary = col.asc().nulls_last() if query.direction == "asc" else col.desc().nulls_last()
        return (primary, Regulation.publish_date.desc().nulls_last(), Regulation.id.desc())
    return default_order


def list_regulations(session: Session, query: ListQuery, settings: Settings, actor_id: str | None = None) -> dict:
    conds = _filters(query, settings)
    total = session.scalar(select(func.count()).select_from(Regulation).where(*conds))
    rows = session.scalars(select(Regulation).where(*conds)
                           .order_by(*_order(query, settings))
                           .offset((query.page - 1) * query.page_size).limit(query.page_size)).all()
    viewed = _viewed_ids(session, [r.id for r in rows], actor_id)
    items = [{**serialize(session, r, settings), "viewed": r.id in viewed} for r in rows]
    return {"items": items, "total": total, "page": query.page, "pageSize": query.page_size}


def export_regulations(session: Session, query: ListQuery, settings: Settings, limit: int) -> tuple[list[dict], int]:
    """Süzgeçe uyan tüm kayıtlar (sayfalamasız, en fazla ``limit``) ve süzgeçteki toplam sayı."""
    conds = _filters(query, settings)
    total = session.scalar(select(func.count()).select_from(Regulation).where(*conds))
    rows = session.scalars(select(Regulation).where(*conds).order_by(*_order(query, settings)).limit(limit)).all()
    return [serialize(session, r, settings) for r in rows], total


def stats(session: Session, settings: Settings) -> dict:
    def count(metric):
        return session.scalar(select(func.count()).select_from(Regulation).where(
            visible(), _metric_cond(metric, settings)))
    base = {"pending": count("pending"), "criticalHighPending": count("critical_high"), "today": count("today"),
            "upcoming": count("upcoming")}
    return {**base, **queue_stats(session, settings)}


def queue_stats(session: Session, settings: Settings) -> dict:
    """Portal kuyrukları: sayılar, önem dağılımı, birimi atanmamışlar ve YZ hazırlık durumu (tek GROUP BY'lar)."""
    from app.ai.classify import AI_MAX_ATTEMPTS as _max

    def n(*conds):
        return session.scalar(select(func.count()).select_from(Regulation).where(visible(), *conds)) or 0

    from app.services.archive import sample_count

    queues = {q: n(_queue_cond(q, settings)) for q in QUEUES}
    from app.ai.common import effective_settings

    queues["ilgisizOrneklem"] = sample_count(session, effective_settings(session, settings))
    dist = {}
    for q in ("yeni", "birikim", "tum"):
        rows = session.execute(select(Regulation.severity, func.count()).where(
            visible(), _queue_cond(q, settings)).group_by(Regulation.severity)).all()
        dist[q] = {sev or "": c for sev, c in rows}
    prep = [_queue_cond("hazirlaniyor", settings)]
    attempts = func.coalesce(Regulation.extra["ai_attempts"].as_integer(), 0)
    failed = n(*prep, Regulation.processing_status == "AI_FAILED")
    exhausted = n(*prep, Regulation.processing_status == "AI_FAILED", attempts >= _max)
    oldest = session.scalar(select(func.min(Regulation.detected_at)).where(visible(), *prep))
    oldest_days = (datetime.now(ZoneInfo("UTC")) - (oldest if oldest.tzinfo else oldest.replace(
        tzinfo=ZoneInfo("UTC")))).days if oldest else 0
    return {"queues": queues, "severityDistribution": dist, "needsUnit": n(_queue_cond("tum", settings),
                                                                        _no_unit_cond(settings)),
            "preparing": {"count": queues["hazirlaniyor"], "failed": failed, "exhausted": exhausted,
                          "retryable": failed - exhausted, "oldestDays": oldest_days}}


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


_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)")


def effective_conflict(reg: Regulation) -> dict | None:
    """``effective_date_text`` içinde ``effective_date``'ten farklı bir tarih geçiyorsa uzmanı uyarır (örn. yürürlükten
    kaldırılan eski düzenlemenin tarihi). Uzman düzeltince ya da onaylayınca (``extra.effective_confirmed``) susar."""
    text = reg.effective_date_text or ""
    if not text or (reg.extra or {}).get("effective_confirmed"):
        return None
    found = []
    for d, m, y in _DATE_RE.findall(text):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    other = [x for x in found if x != reg.effective_date]
    if not other or (reg.effective_date is None and not found):
        return None
    return {"foundDates": [x.isoformat() for x in other], "foundText": text[:300],
            "message": "Belgede yürürlük tarihinden farklı bir tarih de geçiyor; hangisinin doğru olduğunu kontrol edin."}


def ai_stage(reg: Regulation) -> dict | None:
    """YZ işlemi bitmemiş kayıt için aşama: ``summary`` | ``unit_match`` | ``retry`` | ``failed`` (deneme hakkı bitti)."""
    if reg.processing_status not in PREPARING_STATUSES:
        return None
    extra = reg.extra or {}
    attempts = extra.get("ai_attempts", 0)
    if reg.processing_status == "AI_FAILED":
        stage = "failed" if attempts >= AI_MAX_ATTEMPTS else "retry"
    else:
        stage = "unit_match" if reg.processing_status == "SUMMARIZED" else "summary"
    return {"stage": stage, "attempts": attempts, "maxAttempts": AI_MAX_ATTEMPTS, "error": extra.get("ai_error"),
            "failedStage": extra.get("ai_stage"), "waitingSince": _iso(reg.updated_at or reg.detected_at)}


def analysis_status(reg: Regulation) -> str:
    if reg.processing_status == "READY":
        return "hazır"
    if reg.processing_status == "AI_FAILED":
        return "YZ analizi başarısız, yeniden denenecek"
    if (reg.extra or {}).get("summary_blocked"):
        return "metin bekleniyor (OCR)"
    return "analiz sürüyor"


def _ai_units(session: Session, reg_id: int) -> list[dict]:
    """YZ'nin ilk önerisi (uzman sonradan çıkarsa da kalır): ``origin='ai'`` satırları, aktif olsun olmasın."""
    rows = session.execute(select(UnitSuggestion, Unit).join(Unit, Unit.code == UnitSuggestion.unit_code).where(
        UnitSuggestion.regulation_id == reg_id, UnitSuggestion.origin == "ai").order_by(UnitSuggestion.rank,
                                                                                         UnitSuggestion.id))
    seen, out = set(), []
    for s_, u in rows:
        if s_.unit_code not in seen:
            seen.add(s_.unit_code)
            out.append({"code": s_.unit_code, "unit": u.name, "score": s_.score})
    return out


def _revision_count(session: Session, reg_id: int) -> int:
    from app.db import AuditEvent

    return session.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.regulation_id == reg_id, AuditEvent.event_type.in_(("decision_revised", "decision_reopened")))) or 0


def _possible_duplicate(reg: Regulation) -> dict | None:
    if not reg.needs_dedupe_review:
        return None
    cands = (reg.extra or {}).get("dedupe_candidates") or []
    best = max(cands, key=lambda c: c.get("score") or 0, default=None)
    return {"regulationId": best.get("regulation_id"), "score": best.get("score")} if best else {"regulationId": None,
                                                                                                "score": None}


def serialize(session: Session, reg: Regulation, settings: Settings, *, detail: bool = False) -> dict:
    summ = current_summary(session, reg.id)
    t = today(settings)
    detected = reg.detected_at if reg.detected_at.tzinfo else reg.detected_at.replace(tzinfo=ZoneInfo("UTC"))
    item_units = _units(session, reg.id)
    item = {
        "id": reg.id, "title": reg.title, "issuer": reg.issuer_name or reg.issuer, "issuerCode": reg.issuer,
        "sources": _sources(session, reg.id), "type": effective_type(reg),
        "publishDate": reg.publish_date.isoformat() if reg.publish_date else None,
        "effectiveDate": reg.effective_date.isoformat() if reg.effective_date else None,
        "effectiveMode": effective_mode(reg),
        "effectiveSoon": bool(reg.effective_date and t <= reg.effective_date
                              <= t + timedelta(days=settings.effective_soon_days)),
        "isToday": detected.astimezone(ZoneInfo(settings.timezone)).date() == t,
        "severity": reg.severity, "status": reg.review_status, "confidence": reg.confidence_band,
        "sourceUrl": _primary_url(session, reg, summ),
        "summary": summ.short_content if summ else None,
        "topics": [x["text"] for x in summ.relevant_topics] if summ else [],
        "suggestedUnits": item_units,
        "decidedBy": {"name": reg.decided_by, "title": reg.decided_by_title, "at": _iso(reg.decided_at),
                      "decision": reg.review_status} if reg.decided_at else None,
        "analysisStatus": analysis_status(reg), "detectedAt": _iso(reg.detected_at), "version": reg.row_version,
        "canDecide": reg.processing_status == "READY" and reg.review_status == "Bekliyor",
        "needsUnit": not any(u["origin"] == "manual" or (u["score"] or 0) >= settings.unit_weak_score
                             for u in item_units),
        "aiStage": ai_stage(reg), "effectiveConflict": effective_conflict(reg),
        "possibleDuplicate": _possible_duplicate(reg),
        "draftDeadline": (reg.extra or {}).get("draft_deadline"),
    }
    if reg.review_status != "Bekliyor":
        item["aiSuggestedUnits"] = _ai_units(session, reg.id)
        item["decisionRevisions"] = _revision_count(session, reg.id)
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
            "editedFields": edited_fields(session, reg, summ), "aiOriginal": ai_original(session, reg),
            "summaryCitations": summary_citations(summ),
        })
    return item


# ------------------------------------------------------------------------------------------------ iş kuralları


LOCK_TIMEOUT = "5s"


def get_visible(session: Session, reg_id: int, *, for_update: bool = False) -> Regulation:
    """``for_update=True`` (karar, birim, içerik): satır commit'e kadar kilitlenir. İki uzman aynı anda karar verirse
    ikincisi birincisini bekler, güncel kaydı görür ve 412/409 alır (eskiden ikisi de geçip iki karar olayı yazıyordu).
    SQLite'ta ``FOR UPDATE`` yoktur; tek yazıcı olduğu için gerekmez."""
    if not for_update:
        reg = session.get(Regulation, reg_id)
    else:
        postgres = session.get_bind().dialect.name == "postgresql"
        if postgres:
            session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))   # YZ işçisi kaydı tutuyorsa sonsuz bekleme
        try:
            reg = session.get(Regulation, reg_id, with_for_update=True, populate_existing=True)
        except OperationalError as e:
            if postgres and getattr(e.orig, "sqlstate", None) == "55P03":   # lock_not_available
                raise ApiError(409, "record_busy", "kayıt şu an işleniyor, birkaç saniye sonra yeniden deneyin") from e
            raise
    if reg is None or not reg.is_relevant or reg.processing_status not in VISIBLE_STATUSES:
        raise ApiError(404, "not_found", "düzenleme bulunamadı")
    return reg


def _check_version(reg: Regulation, if_match: str | None) -> None:
    if if_match is not None and if_match.strip('W/"') != str(reg.row_version):
        raise ApiError(412, "version_mismatch", "kayıt başka bir kullanıcı tarafından değiştirildi; yeniden yükleyin",
                       {"current_version": reg.row_version})


def _require_ready(reg: Regulation) -> None:
    """Özeti/birim önerisi henüz hazır olmayan kayıtta karar verilemez (kayıt portalda görünür kalır)."""
    if reg.processing_status != "READY":
        raise ApiError(409, "not_ready", f"YZ işlemi tamamlanmadı ({analysis_status(reg)}); hazır olunca karar verilebilir",
                       {"analysisStatus": analysis_status(reg)})


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
           if_match: str | None, rejection_reason: str | None = None) -> None:
    if decision not in ("approve", "reject"):
        raise ApiError(400, "invalid_decision", "decision: approve | reject")
    _check_version(reg, if_match)
    _require_pending(reg)
    _require_ready(reg)
    approved = decision == "approve"
    if not approved and rejection_reason not in ("not_banking_related", "outdated", "duplicate", "other"):
        raise ApiError(400, "missing_rejection_reason", "reddetme nedeni zorunludur")
    if not approved and rejection_reason == "other" and not (note or "").strip():
        raise ApiError(400, "missing_rejection_details", "diğer nedeni için açıklama zorunludur")
    units = [u for u in _units(session, reg.id)]
    reg.review_status = "Onaylandı" if approved else "Reddedildi"
    reg.decided_by, reg.decided_by_title, reg.decided_at = actor.name, actor.title, utcnow()
    reg.decision_note = (note or "").strip() or None
    reg.row_version += 1
    reg.extra = {**(reg.extra or {}), "rejection_reason": None if approved else rejection_reason}
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
    rejection_feedback = {"rejection_reason": rejection_reason} if rejection_reason else {}
    add_event(session, reg.id, "approved" if approved else "rejected", actor=actor, note=full_note,
              payload={"units": [u["code"] for u in units], "user_note": reg.decision_note, **rejection_feedback})
    session.add(Outbox(event_type="record.approved" if approved else "record.rejected", aggregate_id=reg.id,
                       payload={"regulation_id": reg.id, "units": [u["code"] for u in units],
                                "decided_by": actor.name, "note": reg.decision_note, **rejection_feedback}))


EDIT_LABELS = {"title": "Başlık", "summary": "YZ özeti", "reg_type": "Düzenleme türü", "publish_date": "Yayım tarihi",
               "effective": "Yürürlük tarihi", "draft_deadline": "Görüş son tarihi", "topics": "İlgili konular"}
TOPIC_MAX, TOPIC_CHARS = 20, 120


def _iso_d(d) -> str | None:
    return d.isoformat() if d else None


def effective_mode(reg: Regulation) -> str:
    """``date`` (belirli tarih) | ``on_publish`` (yayımında) | ``none`` (belirtilmemiş)."""
    mode = (reg.extra or {}).get("effective_mode")
    if reg.effective_date is None:
        return "none"
    return "on_publish" if mode == "on_publish" and reg.effective_date == reg.publish_date else "date"


def _first_summary(session: Session, reg_id: int) -> RegulationSummary | None:
    return session.scalars(select(RegulationSummary).where(RegulationSummary.regulation_id == reg_id)
                           .order_by(RegulationSummary.version).limit(1)).first()


def ai_original(session: Session, reg: Regulation) -> dict:
    """YZ'nin ilk değerleri. Alanlar uzman düzenlemeden önce ``extra.ai_original``'a bir kez kopyalanır; özet ve konular
    her zaman ilk özet sürümünden okunur (sürümler silinmez)."""
    snap = (reg.extra or {}).get("ai_original") or {}
    first = _first_summary(session, reg.id)
    cur = {"title": reg.title, "type": reg.ai_reg_type or reg.reg_type, "publishDate": _iso_d(reg.publish_date),
           "effectiveDate": _iso_d(reg.effective_date), "effectiveMode": effective_mode(reg),
           "draftDeadline": (reg.extra or {}).get("draft_deadline")}
    out = {**cur, **snap}
    out["summary"] = first.short_content if first else None
    out["topics"] = [t["text"] for t in first.relevant_topics] if first else []
    return out


def edited_fields(session: Session, reg: Regulation, summ: RegulationSummary | None) -> list[str]:
    """Şu an YZ'nin ilk değerinden farklı olan alanlar (uzman geri alırsa listeden çıkar)."""
    o = ai_original(session, reg)
    now = {"title": reg.title, "type": effective_type(reg), "publishDate": _iso_d(reg.publish_date),
           "effectiveDate": _iso_d(reg.effective_date), "effectiveMode": effective_mode(reg),
           "draftDeadline": (reg.extra or {}).get("draft_deadline"),
           "summary": summ.short_content if summ else None,
           "topics": [t["text"] for t in summ.relevant_topics] if summ else []}
    diff = {k for k in now if now[k] != o.get(k)}
    out = []
    for key, fields in (("title", ("title",)), ("summary", ("summary",)), ("type", ("type",)),
                        ("publishDate", ("publishDate",)), ("effective", ("effectiveDate", "effectiveMode")),
                        ("draftDeadline", ("draftDeadline",)), ("topics", ("topics",))):
        if diff & set(fields):
            out.append(key)
    return out


def effective_type(reg: Regulation) -> str | None:
    return ((reg.extra or {}).get("expert") or {}).get("reg_type") or reg.ai_reg_type or reg.reg_type


def edit_content(session: Session, reg: Regulation, title: str | None, summary: str | None, actor: Actor,
                 if_match: str | None, changes: dict | None = None) -> None:
    """Uzman düzenlemesi. ``changes`` yalnızca gönderilen ek alanları taşır (``model_fields_set``): ``reg_type``,
    ``publish_date``, ``effective`` ({mode, date}), ``draft_deadline`` (None = temizle), ``topics``, ``note``,
    ``confirm_effective``. Karar verilmiş kayıt da düzenlenebilir. İlk düzenlemede YZ'nin ilk değerleri
    ``extra.ai_original``'a kopyalanır; özet/konu sürümleri zaten ayrı satırlardır."""
    ch = changes or {}
    editable = ("reg_type", "publish_date", "effective", "draft_deadline", "topics", "confirm_effective")
    if title is None and summary is None and not any(k in ch for k in editable):
        raise ApiError(400, "empty_update", "güncellenecek bir alan gönderilmedi")
    _check_version(reg, if_match)

    title = title.strip() if title is not None else None
    summary = summary.strip() if summary is not None else None
    if title == "" or summary == "":
        raise ApiError(400, "empty_content", "başlık ve özet boş bırakılamaz")
    reg_type = (ch.get("reg_type") or "").strip() if "reg_type" in ch else None
    if "reg_type" in ch and not reg_type:
        raise ApiError(400, "empty_content", "düzenleme türü boş bırakılamaz")
    topics = None
    if ch.get("topics") is not None:
        seen: dict[str, str] = {}
        for t in ch["topics"]:
            if t and t.strip():
                clean = " ".join(t.split())
                seen.setdefault(search_fold(clean), clean)      # büyük/küçük harf farkı yok sayılır, ilk yazım kalır
        topics = list(seen.values())
        if len(topics) > TOPIC_MAX or any(len(t) > TOPIC_CHARS for t in topics):
            raise ApiError(400, "invalid_topics", f"en fazla {TOPIC_MAX} konu, her biri en fazla {TOPIC_CHARS} karakter")

    current = current_summary(session, reg.id) if (summary is not None or topics is not None) else None
    if (summary is not None or topics is not None) and current is None:
        raise ApiError(409, "summary_unavailable", "henüz düzenlenebilir bir YZ özeti yok")

    new_pub = ch["publish_date"] if "publish_date" in ch else reg.publish_date
    if "publish_date" in ch and new_pub is None:
        raise ApiError(400, "invalid_dates", "yayım tarihi boş bırakılamaz")
    eff = ch.get("effective")
    new_eff = reg.effective_date
    if eff is not None:
        mode = eff["mode"]
        new_eff = eff.get("date") if mode == "date" else new_pub if mode == "on_publish" else None
        if mode == "date" and new_eff is None:
            raise ApiError(400, "invalid_dates", "yürürlük tarihi gerekli")
    elif "publish_date" in ch and effective_mode(reg) == "on_publish":
        new_eff = new_pub                       # "yayımında" ise yayım tarihiyle birlikte kayar
    if new_eff and new_pub and new_eff < new_pub:
        raise ApiError(400, "invalid_dates", "yürürlük tarihi yayım tarihinden önce olamaz")

    # --- değişiklik listesi (eski → yeni); denetim izine ve geçmişe yazılır
    fields, rows = [], []

    def note_change(key, old, new):
        fields.append(key)
        rows.append({"field": key, "label": EDIT_LABELS[key], "from": old, "to": new})

    expert = dict((reg.extra or {}).get("expert") or {})
    new_extra = dict(reg.extra or {})
    snapshot = {"title": reg.title, "type": reg.ai_reg_type or reg.reg_type, "publishDate": _iso_d(reg.publish_date),
                "effectiveDate": _iso_d(reg.effective_date), "effectiveMode": effective_mode(reg),
                "draftDeadline": new_extra.get("draft_deadline")}
    payload: dict = {"fields": fields}
    if title is not None and title != reg.title:
        payload["old_title"], payload["new_title"] = reg.title, title
        note_change("title", reg.title, title)
        reg.title = title
    if reg_type is not None and reg_type != effective_type(reg):
        note_change("reg_type", effective_type(reg), reg_type)
        expert["reg_type"] = reg_type
    if "publish_date" in ch and new_pub != reg.publish_date:
        note_change("publish_date", _iso_d(reg.publish_date), _iso_d(new_pub))
        reg.publish_date = new_pub
    if eff is not None or "publish_date" in ch:
        old_mode, old_eff = snapshot["effectiveMode"], snapshot["effectiveDate"]
        after_mode = eff["mode"] if eff is not None else old_mode

        def eff_text(mode, d):
            return "yayımında" if mode == "on_publish" else (d or "belirtilmemiş")

        if _iso_d(new_eff) != old_eff or after_mode != old_mode:
            note_change("effective", eff_text(old_mode, old_eff), eff_text(after_mode, _iso_d(new_eff)))
            reg.effective_date = new_eff
            new_extra["effective_mode"] = after_mode
    if eff is not None or ch.get("confirm_effective"):
        new_extra["effective_confirmed"] = True       # çelişki uyarısı sustu
    if "draft_deadline" in ch and _iso_d(ch["draft_deadline"]) != new_extra.get("draft_deadline"):
        note_change("draft_deadline", new_extra.get("draft_deadline"), _iso_d(ch["draft_deadline"]))
        if ch["draft_deadline"]:
            new_extra["draft_deadline"] = _iso_d(ch["draft_deadline"])
        else:
            new_extra.pop("draft_deadline", None)

    # özet ve konular tek yeni sürümde birleşir
    new_text = summary if summary is not None else (current.short_content if current else None)
    new_topics = topics if topics is not None else ([t["text"] for t in current.relevant_topics] if current else [])
    summary_changed = current is not None and summary is not None and summary != current.short_content
    topics_changed = current is not None and topics is not None and new_topics != [t["text"] for t in current.relevant_topics]
    if summary_changed:
        note_change("summary", current.short_content, summary)
    if topics_changed:
        note_change("topics", [t["text"] for t in current.relevant_topics], new_topics)

    if not fields and not ch.get("confirm_effective"):
        return
    if fields and "ai_original" not in new_extra:
        new_extra["ai_original"] = snapshot          # yalnızca ilk düzenlemede; sonrası değişmez
    if expert:
        new_extra["expert"] = expert

    if summary_changed or topics_changed:
        latest_version = session.scalar(select(func.max(RegulationSummary.version)).where(
            RegulationSummary.regulation_id == reg.id)) or current.version
        current.is_current = False
        next_version = latest_version + 1
        by_text = {t["text"]: t for t in current.relevant_topics}
        session.add(RegulationSummary(
            regulation_id=reg.id, version=next_version, is_current=True, short_content=new_text,
            short_content_evidence=[] if summary_changed else current.short_content_evidence,
            relevant_topics=[by_text.get(t) or {"text": t, "evidence": []} for t in new_topics],
            effective_date_evidence=current.effective_date_evidence,
            grounding_score=0.0 if summary_changed else current.grounding_score,
            unverified_claims=[] if summary_changed else current.unverified_claims,
            source_links=current.source_links, method="manual", prompt_version="manual", llm_call_ids=[]))
        payload["previous_summary_version"], payload["summary_version"] = current.version, next_version

    reg.extra = new_extra
    reg.row_version += 1
    payload["changes"] = rows
    if ch.get("note"):
        payload["user_note"] = ch["note"].strip()
    labels = [EDIT_LABELS[k] for k in fields]
    add_event(session, reg.id, "content_edited", actor=actor,
              note=(f"{', '.join(labels)} güncellendi." if labels else "Yürürlük tarihi doğrulandı.")
              + (f" Not: {ch['note'].strip()}" if ch.get("note") else ""), payload=payload)


def change_units(session: Session, reg: Regulation, unit_codes: list[str], note: str | None, actor: Actor,
                 if_match: str | None, *, allow_decided: bool = False) -> None:
    codes = list(dict.fromkeys(c for c in unit_codes if c))
    if not codes:
        raise ApiError(400, "units_required", "en az bir birim seçilmelidir")
    active = {u.code: u for u in session.scalars(select(Unit).where(Unit.code.in_(codes), Unit.active.is_(True)))}
    unknown = [c for c in codes if c not in active]
    if unknown:
        raise ApiError(400, "unknown_unit", "bilinmeyen veya pasif birim", {"unit_codes": unknown})
    _check_version(reg, if_match)
    if not allow_decided:
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


def bulk_decide(session: Session, ids: list[int], decision: str, unit_codes: list[str] | None, rejection_reason: str | None,
                note: str | None, actor: Actor) -> dict:
    """Birden çok kayıt için tek karar. Her kayıt kendi savepoint'inde işlenir; biri reddedilirse (hazır değil, zaten
    karar verilmiş, birim yok…) diğerleri etkilenmez. ``unit_codes`` verilirse kayıtların birimi önce bu listeyle değişir
    (düzeltme olarak öğrenme döngüsüne girer); verilmezse her kayıtta YZ'nin önerdiği birimler onaylanır."""
    ids = list(dict.fromkeys(ids))
    done, skipped = [], []
    for reg_id in ids:
        try:
            with session.begin_nested():
                reg = get_visible(session, reg_id, for_update=True)
                if decision == "approve":
                    if unit_codes:
                        change_units(session, reg, unit_codes, None, actor, None)
                    elif not _units(session, reg.id):
                        raise ApiError(400, "units_required", "kayıtta onaylanacak birim yok")
                decide(session, reg, decision, note, actor, None, rejection_reason)
            done.append(reg_id)
        except ApiError as e:
            skipped.append({"id": reg_id, "code": e.code, "message": e.message})
    return {"done": done, "skipped": skipped}


# ------------------------------------------------------------------------------------------------ karar düzeltme


MIN_REASON = 5


def _decision_state(session: Session, reg: Regulation) -> dict:
    return {"status": reg.review_status, "units": [u["code"] for u in _units(session, reg.id)],
            "rejectionReason": (reg.extra or {}).get("rejection_reason")}


def _validate_decision_args(decision: str, rejection_reason: str | None, note: str | None) -> None:
    if decision not in ("approve", "reject"):
        raise ApiError(400, "invalid_decision", "decision: approve | reject")
    if decision == "reject":
        if rejection_reason not in ("not_banking_related", "outdated", "duplicate", "other"):
            raise ApiError(400, "missing_rejection_reason", "reddetme nedeni zorunludur")
        if rejection_reason == "other" and not (note or "").strip():
            raise ApiError(400, "missing_rejection_details", "diğer nedeni için açıklama zorunludur")


def _require_reason(reason: str | None) -> str:
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON:
        raise ApiError(400, "reason_required", f"değişiklik gerekçesi zorunludur (en az {MIN_REASON} karakter)")
    return reason


def _describe(state: dict, names: dict[str, str]) -> str:
    if state["status"] == "Onaylandı":
        return "Onay · " + (", ".join(names.get(c, c) for c in state["units"]) or "birim yok")
    return "Ret · " + (state["rejectionReason"] or "-")


def revise_decision(session: Session, reg: Regulation, decision: str, unit_codes: list[str] | None,
                    rejection_reason: str | None, note: str | None, reason: str | None, actor: Actor,
                    if_match: str | None) -> None:
    """Karar verilmiş kaydın kararını değiştirir (onay↔ret, birim ekleme/çıkarma, ret nedeni). Önceki karar silinmez:
    ``decision_revised`` olayı eski ve yeni durumu, gerekçeyi ve kimin yaptığını taşır."""
    _check_version(reg, if_match)
    if reg.review_status not in ("Onaylandı", "Reddedildi"):
        raise ApiError(409, "not_decided", "kayıt henüz karara bağlanmamış; önce karar verin")
    reason = _require_reason(reason)
    _validate_decision_args(decision, rejection_reason, note)
    before = _decision_state(session, reg)
    codes = list(dict.fromkeys(c for c in (unit_codes or before["units"]) if c))
    approve = decision == "approve"
    if approve:
        if not codes:
            raise ApiError(400, "units_required", "onay için en az bir birim gerekli")
        same = before["status"] == "Onaylandı" and set(codes) == set(before["units"])
    else:
        same = before["status"] == "Reddedildi" and before["rejectionReason"] == rejection_reason
    if same:
        raise ApiError(400, "no_change", "karar değişmedi")
    if approve and set(codes) != set(before["units"]):
        change_units(session, reg, codes, None, actor, None, allow_decided=True)
    elif approve:
        record_unit_decision(session, reg, codes, corrected=False)
    now_units = [u["code"] for u in _units(session, reg.id)]
    if approve:
        for s in session.scalars(select(UnitSuggestion).where(UnitSuggestion.regulation_id == reg.id,
                                                              UnitSuggestion.is_active.is_(True))):
            s.is_final = True
    else:
        from app.db import FewshotExample

        for s in session.scalars(select(UnitSuggestion).where(UnitSuggestion.regulation_id == reg.id)):
            s.is_final = False
        for ex in session.scalars(select(FewshotExample).where(FewshotExample.regulation_id == reg.id,
                                                               FewshotExample.task == "unit_match",
                                                               FewshotExample.active.is_(True))):
            ex.active = False        # reddedilen kayıt birim eşleştirmesine örnek olmasın
    reg.review_status = "Onaylandı" if approve else "Reddedildi"
    reg.decided_by, reg.decided_by_title, reg.decided_at = actor.name, actor.title, utcnow()
    reg.decision_note = (note or "").strip() or None
    reg.extra = {**(reg.extra or {}), "rejection_reason": None if approve else rejection_reason}
    reg.row_version += 1
    after = {"status": reg.review_status, "units": now_units, "rejectionReason": None if approve else rejection_reason}
    names = {u.code: u.name for u in session.scalars(
        select(Unit).where(Unit.code.in_(set(before["units"]) | set(now_units))))}
    add_event(session, reg.id, "decision_revised", actor=actor,
              note=f"Karar değiştirildi: {_describe(before, names)} → {_describe(after, names)}. Gerekçe: {reason}",
              payload={"from": before, "to": after, "reason": reason, "user_note": reg.decision_note})
    session.add(Outbox(event_type="record.decision_revised", aggregate_id=reg.id,
                       payload={"regulation_id": reg.id, "from": before, "to": after, "reason": reason,
                                "decided_by": actor.name}))


def reopen_decision(session: Session, reg: Regulation, reason: str | None, actor: Actor, if_match: str | None) -> None:
    """Kararı kaldırır; kayıt yeniden ``Bekliyor`` olur ve karar bekleyen kuyruğuna döner."""
    _check_version(reg, if_match)
    if reg.review_status not in ("Onaylandı", "Reddedildi"):
        raise ApiError(409, "not_decided", "kayıt zaten karar bekliyor")
    reason = _require_reason(reason)
    before = _decision_state(session, reg)
    for s in session.scalars(select(UnitSuggestion).where(UnitSuggestion.regulation_id == reg.id)):
        s.is_final = False
    reg.review_status = "Bekliyor"
    reg.decided_by = reg.decided_by_title = reg.decided_at = reg.decision_note = None
    reg.extra = {**(reg.extra or {}), "rejection_reason": None}
    reg.row_version += 1
    add_event(session, reg.id, "decision_reopened", actor=actor,
              note=f"Karar kaldırıldı ({before['status']}); kayıt yeniden karar bekliyor. Gerekçe: {reason}",
              payload={"from": before, "reason": reason})
    session.add(Outbox(event_type="record.decision_reopened", aggregate_id=reg.id,
                       payload={"regulation_id": reg.id, "from": before, "reason": reason, "by": actor.name}))


def decision_stats(session: Session) -> dict:
    """Uzman kararlarının özeti ve birim önerisi isabeti: aynen onaylanan / onaylananların tümü."""
    from app.db import AuditEvent

    approved_ids = select(Regulation.id).where(Regulation.review_status == "Onaylandı")
    changed = session.scalar(select(func.count(func.distinct(UnitSuggestion.regulation_id))).where(
        UnitSuggestion.regulation_id.in_(approved_ids),
        or_(and_(UnitSuggestion.origin == "manual", UnitSuggestion.is_active.is_(True)),
            and_(UnitSuggestion.origin == "ai", UnitSuggestion.is_active.is_(False))))) or 0
    approved = session.scalar(select(func.count()).select_from(Regulation).where(
        Regulation.review_status == "Onaylandı")) or 0
    rejected = session.scalar(select(func.count()).select_from(Regulation).where(
        Regulation.review_status == "Reddedildi")) or 0
    confirmed_irr = session.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.event_type == "irrelevant_confirmed")) or 0
    revised = session.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.event_type.in_(("decision_revised", "decision_reopened")))) or 0
    return {"approved": approved, "approvedAsSuggested": approved - changed, "approvedWithChange": changed,
            "rejected": rejected, "irrelevantConfirmed": confirmed_irr, "revisions": revised,
            "unitAccuracy": round((approved - changed) / approved, 3) if approved else None}


def summary_citations(summ: RegulationSummary | None) -> list[dict]:
    """Özet cümlelerinin belgedeki dayanak pasajları: ``[{n, sentence, passages:[{text, rawDocumentId, charStart,
    charEnd, score, method}]}]``. Uzman özeti elle düzenlediyse dayanak yoktur (boş liste)."""
    if summ is None or summ.method == "manual":
        return []
    out = []
    for i, item in enumerate(summ.short_content_evidence or [], 1):
        ev = item.get("evidence") or []
        ev = [ev] if isinstance(ev, dict) else ev
        out.append({"n": i, "sentence": item.get("sentence"), "passages": [
            {"text": e.get("quote"), "rawDocumentId": e.get("raw_document_id"), "charStart": e.get("char_start"),
             "charEnd": e.get("char_end"), "score": e.get("score"), "method": e.get("method")} for e in ev if e]})
    return out


DOC_TEXT_LIMIT = 300_000


def primary_document(session: Session, reg: Regulation, summ: RegulationSummary | None = None) -> dict | None:
    """Kaydın ana belgesi: özet dayanaklarının işaret ettiği belge; yoksa metni olan, ek olmayan belge (Resmî Gazete
    önce). ``summaryCitations`` karakter aralıkları bu belgenin metnine göredir."""
    docs = session.scalars(select(RawDocument).join(
        RegulationSourceLink, RegulationSourceLink.raw_document_id == RawDocument.id
    ).where(RegulationSourceLink.regulation_id == reg.id, RawDocument.role != "attachment").order_by(
        (func.coalesce(func.length(RawDocument.text), 0) > 0).desc(),
        (RawDocument.source_code == "RESMI_GAZETE").desc(), RawDocument.id)).all()
    if not docs:
        return None
    cited = [p["rawDocumentId"] for c in summary_citations(summ) for p in c["passages"] if p["rawDocumentId"]]
    raw = next((d for d in docs if cited and d.id == max(set(cited), key=cited.count)), docs[0])
    text = raw.text or ""
    return {"rawDocumentId": raw.id, "title": raw.title or reg.title, "url": raw.final_url or raw.url,
            "source": raw.source_code, "extractionMethod": raw.extraction_method, "extractedAt": _iso(raw.processed_at),
            "pageCount": raw.page_count, "text": text[:DOC_TEXT_LIMIT], "truncated": len(text) > DOC_TEXT_LIMIT}
