"""Portal API (İK-6, plan §6). ``uvicorn app.api.main:app`` — portal ``/`` adresinden sunulur.

Genel kurallar: ``/api/v1`` öneki; hata biçimi ``{"error": {"code", "message", "details"}}``; sayfalama
``page``/``page_size`` (10/25/50, en fazla 100); yazma işlemleri denetim izi kaydıyla tek transaction'da.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.auth import ROLE_ADMIN, ROLE_EXPERT, ROLE_VIEWER, current_actor, require
from app.db import Unit, make_sessionmaker
from app.services.archive import ArchiveQuery, archive_detail, list_archive
from app.services.audit import Actor
from app.services.export import MAX_EXPORT_ROWS, build_workbook, export_filename
from app.services.health import sources_health
from app.services.regulations import (
    REVIEW_STATUSES,
    ApiError,
    ListQuery,
    change_units,
    decide,
    edit_content,
    export_regulations,
    get_visible,
    list_regulations,
    mark_viewed,
    serialize,
    stats,
    today,
)
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)
PORTAL_FILE = "Mevzuat Takip Portali.dc.html"
# Eski arayüz (React, ``portal/eski/``): bir sürüm yedekte tutulur, ``/eski`` altından açılır
LEGACY_DIR = "eski"
LEGACY_PAGES = {"": PORTAL_FILE, "surec": "surec-haritasi.html", "arsiv": "arsiv.html", "karsilastirma": "karsilastirma.html"}
# Eski adresler yeni sayfanın ilgili sekmesine yönlenir
OLD_ROUTES = {"/surec": "/#hat", "/arsiv": "/#arsiv", "/karsilastirma": "/#karsilastirma", "/admin": "/#yonetim"}
VENDOR_TYPES = {".js": "application/javascript", ".css": "text/css", ".map": "application/json"}
ASSET_TYPES = {".js": "application/javascript", ".css": "text/css; charset=utf-8"}
# Tam bir CSP (script-src vb.) bilinçli yok: portal satır içi betik ve stil kullanıyor. Yalnızca çerçeveye gömülmeyi
# (clickjacking) ve içerik türü koklamayı kapatır.
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                    "Content-Security-Policy": "frame-ancestors 'none'",
                    "Referrer-Policy": "strict-origin-when-cross-origin"}


# ------------------------------------------------------------------------------------------------ bağımlılıklar


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_session(request: Request) -> Iterator[Session]:
    sf: sessionmaker[Session] = request.app.state.session_factory
    with sf() as session:
        yield session


def get_actor(request: Request, settings: Annotated[Settings, Depends(get_app_settings)]) -> Actor:
    return current_actor(request, settings)


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
ActorDep = Annotated[Actor, Depends(get_actor)]


# ------------------------------------------------------------------------------------------------ gövdeler


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)
    rejection_reason: Literal["not_banking_related", "outdated", "duplicate", "other"] | None = None

    @model_validator(mode="after")
    def validate_rejection_feedback(self) -> "DecisionIn":
        if self.decision == "reject":
            if self.rejection_reason is None:
                raise ValueError("reddetme nedeni zorunludur")
            if self.rejection_reason == "other" and not (self.note or "").strip():
                raise ValueError("diğer nedeni için açıklama zorunludur")
        elif self.rejection_reason is not None:
            raise ValueError("onay kararında reddetme nedeni kullanılamaz")
        return self


class UnitsIn(BaseModel):
    unit_codes: list[str] = Field(min_length=1, max_length=10)
    note: str | None = Field(default=None, max_length=2000)


class EffectiveIn(BaseModel):
    mode: Literal["date", "on_publish", "none"]
    on: date | None = Field(default=None, alias="date")

    @model_validator(mode="after")
    def check(self) -> "EffectiveIn":
        if self.mode == "date" and self.on is None:
            raise ValueError("mode=date için date gerekli")
        return self


class ContentIn(BaseModel):
    """Yalnızca gönderilen alanlar değişir (``draft_deadline: null`` görüş tarihini temizler)."""
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    summary: str | None = Field(default=None, min_length=1, max_length=20000)
    reg_type: str | None = Field(default=None, min_length=1, max_length=64)
    publish_date: date | None = None
    effective: EffectiveIn | None = None
    draft_deadline: date | None = None
    topics: list[str] | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=2000)
    confirm_effective: bool = False


class SettingsIn(BaseModel):
    values: dict[str, str]
    reason: str | None = Field(default=None, max_length=1000)    # değişiklik gerekçesi (geçmişe yazılır)


class SettingsPreviewIn(BaseModel):
    values: dict[str, str]


class AlertResolveIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class BulkDecisionIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)
    decision: Literal["approve", "reject"]
    unit_codes: list[str] | None = Field(default=None, max_length=10)
    note: str | None = Field(default=None, max_length=2000)
    rejection_reason: Literal["not_banking_related", "outdated", "duplicate", "other"] | None = None

    @model_validator(mode="after")
    def validate_reason(self) -> "BulkDecisionIn":
        if self.decision == "reject":
            if self.rejection_reason is None:
                raise ValueError("reddetme nedeni zorunludur")
            if self.rejection_reason == "other" and not (self.note or "").strip():
                raise ValueError("diğer nedeni için açıklama zorunludur")
        elif self.rejection_reason is not None or self.unit_codes == []:
            raise ValueError("onay kararında reddetme nedeni kullanılamaz; unit_codes boş olamaz")
        return self


class ReviseIn(BaseModel):
    """Arşivde kararı değiştirme: yeni karar + zorunlu gerekçe. Onayda ``unit_codes`` verilmezse mevcut birimler korunur."""
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=5, max_length=2000)
    unit_codes: list[str] | None = Field(default=None, min_length=1, max_length=10)
    note: str | None = Field(default=None, max_length=2000)
    rejection_reason: Literal["not_banking_related", "outdated", "duplicate", "other"] | None = None


class IrrelevantReviewIn(BaseModel):
    verdict: Literal["confirm", "relevant"]
    note: str | None = Field(default=None, max_length=2000)


class ReopenIn(BaseModel):
    reason: str = Field(min_length=5, max_length=2000)


class SplitIn(BaseModel):
    raw_document_id: int


# ------------------------------------------------------------------------------------------------ portal API

api = APIRouter(prefix="/api/v1")


@api.get("/regulations")
def regulations(session: SessionDep, settings: SettingsDep, actor: ActorDep,
                source: str | None = None, severity: str | None = None, status: str | None = None,
                unit: str | None = None, date_range: Literal["7", "30"] | None = None, q: str | None = None,
                metric: Literal["pending", "critical_high", "criticalHigh", "today", "upcoming"] | None = None,
                page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 10,
                sort: Literal["issuer", "unit", "title", "publish_date", "effective_date", "severity", "status",
                              "priority", "detected_at", "decided_at"] | None = None,
                direction: Literal["asc", "desc"] = "desc",
                queue: Literal["yeni", "birikim", "tum", "hazirlaniyor"] | None = None,
                effective: str | None = None, needs_unit: bool = False, unit_changed: bool = False,
                edited: bool = False):
    """``source``/``severity``/``unit``/``effective`` virgülle çoklu değer alır (VEYA). ``queue``: portal kuyruğu."""
    require(actor, ROLE_VIEWER)
    if status and status not in REVIEW_STATUSES:
        raise ApiError(400, "invalid_status", f"status: {', '.join(REVIEW_STATUSES)}")
    return list_regulations(session, ListQuery(source, severity, status, unit, int(date_range) if date_range else None,
                                               q, metric, page, page_size, sort, direction, queue, effective,
                                               needs_unit, unit_changed, edited), settings, actor.id)


@api.get("/regulations/stats")
def regulation_stats(session: SessionDep, settings: SettingsDep, actor: ActorDep):
    require(actor, ROLE_VIEWER)
    return stats(session, settings)


XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@api.get("/regulations/export")
def regulations_export(session: SessionDep, settings: SettingsDep, actor: ActorDep,
                       source: str | None = None, severity: str | None = None, status: str | None = None,
                       unit: str | None = None, date_range: Literal["7", "30"] | None = None, q: str | None = None,
                       metric: Literal["pending", "critical_high", "criticalHigh", "today", "upcoming"] | None = None,
                       sort: Literal["issuer", "unit", "title", "publish_date", "effective_date", "severity", "status",
                                     "priority", "detected_at", "decided_at"] | None = None,
                       direction: Literal["asc", "desc"] = "desc",
                       queue: Literal["yeni", "birikim", "tum", "hazirlaniyor"] | None = None,
                       effective: str | None = None, needs_unit: bool = False):
    """Liste süzgeçlerine uyan kayıtları Excel (.xlsx) olarak indirir (sayfalamasız, en fazla ``MAX_EXPORT_ROWS``;
    fazlası varsa ``X-Export-Truncated: true`` başlığı döner)."""
    require(actor, ROLE_VIEWER)
    if status and status not in REVIEW_STATUSES:
        raise ApiError(400, "invalid_status", f"status: {', '.join(REVIEW_STATUSES)}")
    query = ListQuery(source, severity, status, unit, int(date_range) if date_range else None, q, metric,
                      sort=sort, direction=direction, queue=queue, effective=effective, needs_unit=needs_unit)
    items, total = export_regulations(session, query, settings, MAX_EXPORT_ROWS)
    headers = {"Content-Disposition": f'attachment; filename="{export_filename(today(settings))}"',
               "X-Export-Total": str(total), "X-Export-Truncated": str(total > len(items)).lower(),
               "Access-Control-Expose-Headers": "Content-Disposition, X-Export-Total, X-Export-Truncated"}
    return Response(build_workbook(items), media_type=XLSX_TYPE, headers=headers)


@api.get("/archive/regulations")
def archive_regulations(session: SessionDep, actor: ActorDep,
                        kind: Literal["irrelevant", "baseline", "all"] = "irrelevant",
                        source: str | None = None, q: str | None = None,
                        date_from: Annotated[date | None, Query(alias="from")] = None,
                        date_to: Annotated[date | None, Query(alias="to")] = None,
                        page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 25,
                        sort: Literal["publish_date", "detected_at", "title", "issuer", "score"] | None = None,
                        direction: Literal["asc", "desc"] = "desc"):
    """Portalda görünmeyen kayıtlar: YZ'nin ilgisiz bulduğu (``irrelevant``) ve ilk taramada sitede duran
    (``baseline``). Sayfalıdır; ``counts`` geçerli süzgeçlere göre her türün sayısını verir."""
    require(actor, ROLE_VIEWER)
    return list_archive(session, ArchiveQuery(kind, source, q, date_from, date_to, page, page_size, sort, direction))


@api.get("/regulations/irrelevant-sample")
def irrelevant_sample_list(session: SessionDep, settings: SettingsDep, actor: ActorDep,
                           page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 25):
    """Kontrol örneklemi: YZ'nin eşiğe yakın skorla ilgisiz bulduğu, uzmanca henüz incelenmemiş kayıtlar."""
    from app.ai.common import effective_settings
    from app.services.archive import irrelevant_sample

    require(actor, ROLE_VIEWER)
    return irrelevant_sample(session, effective_settings(session, settings), page, page_size)


@api.post("/regulations/{reg_id}/irrelevant-review")
def irrelevant_review(reg_id: int, body: IrrelevantReviewIn, session: SessionDep, actor: ActorDep):
    """``confirm`` ya da ``relevant`` (kayıt YZ hattına geri alınır). Karar öğrenme havuzuna ve denetim izine yazılır."""
    from app.services.archive import review_irrelevant

    require(actor, ROLE_EXPERT)
    reg = review_irrelevant(session, reg_id, body.verdict, body.note, actor)
    session.commit()
    return {"id": reg.id, "processingStatus": reg.processing_status, "verdict": body.verdict}


@api.get("/archive/regulations/{reg_id}")
def archive_regulation_detail(reg_id: int, session: SessionDep, actor: ActorDep):
    require(actor, ROLE_VIEWER)
    return archive_detail(session, reg_id)


@api.post("/regulations/bulk-decision")
def regulation_bulk_decision(body: BulkDecisionIn, session: SessionDep, actor: ActorDep):
    """Birden çok kayda tek karar (en fazla 100). Kısmi başarı: ``done`` işlenenler, ``skipped`` her biri için neden
    (``not_ready``, ``already_decided``, ``units_required``…). İyimser kilit kullanılmaz; kararı satır kilidi sıralar."""
    from app.services.regulations import bulk_decide

    require(actor, ROLE_EXPERT)
    result = bulk_decide(session, body.ids, body.decision, body.unit_codes, body.rejection_reason, body.note, actor)
    session.commit()
    return result


@api.get("/regulations/decision-stats")
def regulation_decision_stats(session: SessionDep, actor: ActorDep):
    """Karar özeti ve birim önerisi isabeti (aynen onaylanan / onaylanan)."""
    from app.services.regulations import decision_stats

    require(actor, ROLE_VIEWER)
    return decision_stats(session)


@api.get("/regulations/{reg_id}")
def regulation_detail(reg_id: int, session: SessionDep, settings: SettingsDep, actor: ActorDep, response: Response):
    require(actor, ROLE_VIEWER)
    reg = get_visible(session, reg_id)
    response.headers["ETag"] = f'"{reg.row_version}"'
    return serialize(session, reg, settings, detail=True)


@api.patch("/regulations/{reg_id}/content")
def regulation_content(reg_id: int, body: ContentIn, session: SessionDep, settings: SettingsDep,
                       actor: ActorDep, response: Response, if_match: Annotated[str | None, Header()] = None):
    require(actor, ROLE_EXPERT)
    reg = get_visible(session, reg_id, for_update=True)
    sent = body.model_fields_set
    changes = {k: (v.model_dump(by_alias=True) if k == "effective" and v else v) for k, v in
               ((k, getattr(body, k)) for k in sent if k not in ("title", "summary"))}
    changes = {k: v for k, v in changes.items() if v is not None or k == "draft_deadline"}
    edit_content(session, reg, body.title, body.summary, actor, if_match, changes)
    session.commit()
    response.headers["ETag"] = f'"{reg.row_version}"'
    return serialize(session, reg, settings, detail=True)


@api.get("/regulations/{reg_id}/document")
def regulation_document(reg_id: int, session: SessionDep, actor: ActorDep):
    """Belge sekmesi: ana belgenin çıkarılmış metni; ``summaryCitations`` aralıkları bu metne göre okunur."""
    from app.ai.unit_matching import current_summary
    from app.services.regulations import primary_document

    require(actor, ROLE_VIEWER)
    reg = get_visible(session, reg_id)
    doc = primary_document(session, reg, current_summary(session, reg.id))
    if doc is None:
        raise ApiError(404, "not_found", "kayda bağlı belge bulunamadı")
    return doc


@api.post("/regulations/{reg_id}/views")
def regulation_viewed(reg_id: int, session: SessionDep, actor: ActorDep):
    require(actor, ROLE_VIEWER)
    created = mark_viewed(session, get_visible(session, reg_id), actor)
    session.commit()
    return {"created": created}


@api.post("/regulations/{reg_id}/decision")
def regulation_decision(reg_id: int, body: DecisionIn, session: SessionDep, settings: SettingsDep, actor: ActorDep,
                        response: Response, if_match: Annotated[str | None, Header()] = None):
    require(actor, ROLE_EXPERT)
    reg = get_visible(session, reg_id, for_update=True)
    decide(session, reg, body.decision, body.note, actor, if_match, body.rejection_reason)
    session.commit()
    response.headers["ETag"] = f'"{reg.row_version}"'
    return serialize(session, reg, settings, detail=True)


@api.post("/regulations/{reg_id}/decision/revise")
def regulation_decision_revise(reg_id: int, body: ReviseIn, session: SessionDep, settings: SettingsDep, actor: ActorDep,
                               response: Response, if_match: Annotated[str | None, Header()] = None):
    """Verilmiş kararı değiştirir (onay↔ret, birim ekleme/çıkarma, ret nedeni). Gerekçe zorunlu; önceki karar denetim
    izinde kalır."""
    from app.services.regulations import revise_decision

    require(actor, ROLE_EXPERT)
    reg = get_visible(session, reg_id, for_update=True)
    revise_decision(session, reg, body.decision, body.unit_codes, body.rejection_reason, body.note, body.reason, actor,
                    if_match)
    session.commit()
    response.headers["ETag"] = f'"{reg.row_version}"'
    return serialize(session, reg, settings, detail=True)


@api.post("/regulations/{reg_id}/decision/reopen")
def regulation_decision_reopen(reg_id: int, body: ReopenIn, session: SessionDep, settings: SettingsDep, actor: ActorDep,
                               response: Response, if_match: Annotated[str | None, Header()] = None):
    """Kararı kaldırır; kayıt yeniden karar bekler."""
    from app.services.regulations import reopen_decision

    require(actor, ROLE_EXPERT)
    reg = get_visible(session, reg_id, for_update=True)
    reopen_decision(session, reg, body.reason, actor, if_match)
    session.commit()
    response.headers["ETag"] = f'"{reg.row_version}"'
    return serialize(session, reg, settings, detail=True)


@api.put("/regulations/{reg_id}/units")
def regulation_units(reg_id: int, body: UnitsIn, session: SessionDep, settings: SettingsDep, actor: ActorDep,
                     response: Response, if_match: Annotated[str | None, Header()] = None):
    require(actor, ROLE_EXPERT)
    reg = get_visible(session, reg_id, for_update=True)
    change_units(session, reg, body.unit_codes, body.note, actor, if_match)
    session.commit()
    response.headers["ETag"] = f'"{reg.row_version}"'
    return serialize(session, reg, settings, detail=True)


@api.get("/units")
def units(session: SessionDep, actor: ActorDep):
    require(actor, ROLE_VIEWER)
    return [{"code": u.code, "name": u.name, "group": u.group_name}
            for u in session.scalars(select(Unit).where(Unit.active.is_(True)).order_by(Unit.name))]


@api.get("/sources")
def sources(settings: SettingsDep, actor: ActorDep):
    from app.collectors.config import load_sources

    require(actor, ROLE_VIEWER)
    return [{"code": s.code, "name": s.name} for s in load_sources(settings.sources_file).enabled()]


@api.get("/sources/health")
def health_of_sources(session: SessionDep, settings: SettingsDep, actor: ActorDep):
    require(actor, ROLE_VIEWER)
    return sources_health(session, settings)


@api.get("/pipeline")
def pipeline(session: SessionDep, settings: SettingsDep, actor: ActorDep,
             days: Annotated[int | None, Query(ge=1, le=365)] = None, format: Literal["json", "mermaid"] = "json"):
    from app.services.pipeline import mermaid, overview

    require(actor, ROLE_VIEWER)
    data = overview(session, settings, days)
    if format == "mermaid":
        return PlainTextResponse(mermaid(data))
    return data


@api.get("/pipeline/nodes/{node_id}")
def pipeline_node(node_id: str, session: SessionDep, actor: ActorDep,
                  days: Annotated[int | None, Query(ge=1, le=365)] = None,
                  limit: Annotated[int, Query(ge=1, le=100)] = 20):
    from app.services.pipeline import node_items

    require(actor, ROLE_VIEWER)
    return node_items(session, node_id, days, limit)


@api.get("/me")
def me(actor: ActorDep):
    return {"name": actor.name, "title": actor.title, "roles": sorted(actor.roles),
            "canDecide": _has(actor, ROLE_EXPERT), "isAdmin": _has(actor, ROLE_ADMIN)}


def _has(actor: Actor, role: str) -> bool:
    try:
        require(actor, role)
        return True
    except ApiError:
        return False


# ------------------------------------------------------------------------------------------------ yönetim

admin = APIRouter(prefix="/api/v1/admin")


@admin.get("/settings")
def admin_settings(session: SessionDep, settings: SettingsDep, actor: ActorDep, detail: bool = False):
    """Düz ``{anahtar: değer}``; ``?detail=true`` ile ``{values, specs}`` (specs: tip, aralık, açıklama — form için)."""
    from app.ai.common import RUNTIME_KEYS, RUNTIME_SPECS, effective_settings

    require(actor, ROLE_ADMIN)
    eff = effective_settings(session, settings)
    values = {k: getattr(eff, k) for k in sorted(RUNTIME_KEYS)}
    if not detail:
        return values
    return {"values": values, "specs": {k: {"type": "int" if s.type is int else "float", "min": s.min, "max": s.max,
                                            "description": s.description} for k, s in sorted(RUNTIME_SPECS.items())}}


@admin.put("/settings")
def admin_settings_put(body: SettingsIn, session: SessionDep, settings: SettingsDep, actor: ActorDep,
                       detail: bool = False):
    """Hata gövdesi ``details.fields``: alan bazlı iletiler ({anahtar: ileti}); tutarlılık hatası ilgili alana bağlanır."""
    import re

    from app.ai.common import RUNTIME_KEYS, check_runtime_settings, effective_settings, set_runtime_setting

    require(actor, ROLE_ADMIN)
    reason = (body.reason or "").strip()
    if settings.require_setting_reason and len(reason) < 5:
        raise ApiError(400, "reason_required", "değişiklik gerekçesi zorunludur (en az 5 karakter)")
    before = effective_settings(session, settings)
    fields: dict[str, str] = {}
    for key, value in body.values.items():
        try:
            set_runtime_setting(session, key, value, by=actor.name, reason=reason,
                                old=str(getattr(before, key)) if key in RUNTIME_KEYS else None)
        except (KeyError, ValueError) as e:
            fields[key] = str(e.args[0]) if e.args else str(e)   # KeyError str()'i iletiyi tırnaklar
    general = ""
    if not fields:
        try:
            check_runtime_settings(session, settings)   # birbirine bağlı eşikler; tümü yazıldıktan sonra denetlenir
        except ValueError as e:
            m = re.match(r"(\w+)", str(e))
            if m and m.group(1) in RUNTIME_KEYS and m.group(1) in body.values:
                fields[m.group(1)] = str(e)
            else:
                general = str(e)
    if fields or general:
        session.rollback()
        raise ApiError(400, "invalid_setting", "; ".join(fields.values()) or general, {"fields": fields})
    session.commit()
    return admin_settings(session, settings, actor, detail)


@admin.get("/settings/history")
def admin_settings_history(session: SessionDep, actor: ActorDep, key: str | None = None,
                           limit: Annotated[int, Query(ge=1, le=200)] = 20):
    """Ayar değişiklikleri (en yeni önce): kim, ne zaman, eski → yeni değer, gerekçe."""
    from app.db import AppSettingHistory

    require(actor, ROLE_ADMIN)
    q = select(AppSettingHistory).order_by(AppSettingHistory.id.desc()).limit(limit)
    if key:
        q = q.where(AppSettingHistory.key == key)
    return [{"id": h.id, "key": h.key, "oldValue": h.old_value, "newValue": h.new_value, "reason": h.reason,
             "changedBy": h.changed_by, "changedAt": _iso_dt(h.changed_at)} for h in session.scalars(q)]


@admin.post("/settings/preview")
def admin_settings_preview(body: SettingsPreviewIn, session: SessionDep, settings: SettingsDep, actor: ActorDep):
    """Kaydetmeden önce etki: son 30 günün kayıtları yeni ağırlık/eşiklerle yeniden değerlendirilseydi kaç ilgili↔ilgisiz
    değişirdi, kaç mükerrer adayı otomatik birleşirdi, LLM çağrı sayısı nasıl değişirdi. LLM çağrısı yapmaz, yazmaz."""
    from app.services.settings_preview import preview

    require(actor, ROLE_ADMIN)
    return preview(session, settings, body.values)


@admin.get("/metrics")
def admin_metrics(session: SessionDep, settings: SettingsDep, actor: ActorDep):
    from app.services.admin_metrics import collect

    require(actor, ROLE_ADMIN)
    return collect(session, settings)


@admin.get("/scans")
def admin_scans(session: SessionDep, settings: SettingsDep, actor: ActorDep, source: str | None = None,
                status: Literal["success", "partial", "failed"] | None = None,
                limit: Annotated[int, Query(ge=1, le=100)] = 30):
    """Etkin taramalar, crawler kuyruğu, tarama geçmişi ve arka plan işleri (yönetim sayfası)."""
    from app.services.jobs import sweep_stale
    from app.services.scans import collect_scans

    require(actor, ROLE_ADMIN)
    sweep_stale(session, settings)   # takılı kalan kayıtlar sayfada hayalet olarak görünmesin
    session.commit()
    return collect_scans(session, settings, source=source, status=status, limit=limit)


@admin.post("/regulations/{reg_id}/split")
def admin_split(reg_id: int, body: SplitIn, session: SessionDep, actor: ActorDep):
    from app.db import RawDocument
    from app.processing.dedupe import split_document

    require(actor, ROLE_ADMIN)
    raw = session.get(RawDocument, body.raw_document_id)
    if raw is None or raw.regulation_id != reg_id:
        raise ApiError(404, "not_found", "belge bu düzenlemeye bağlı değil")
    new_id = split_document(session, body.raw_document_id)
    session.commit()
    return {"new_regulation_id": new_id}


@admin.post("/regulations/{reg_id}/regenerate", status_code=202)
def admin_regenerate(reg_id: int, background: BackgroundTasks, session: SessionDep, settings: SettingsDep,
                     actor: ActorDep, request: Request):
    from app.services.jobs import create_job, run_job

    require(actor, ROLE_ADMIN)
    get_visible(session, reg_id)
    sf = request.app.state.session_factory
    jid = create_job(session, "regenerate", params={"regulationId": reg_id}, requested_by=actor.name)
    background.add_task(run_job, sf, jid, _regenerate, sf, settings, reg_id, actor)
    return {"status": "queued", "jobId": jid}


def _regenerate(sf: sessionmaker[Session], settings: Settings, reg_id: int, actor: Actor) -> dict:
    from app.ai.llm_client import get_llm
    from app.ai.summary import summarize_pending
    from app.ai.unit_matching import match_pending
    from app.services.audit import add_event

    llm = get_llm(settings)
    if llm is None:
        log.warning("regenerate: LLM yapılandırılmamış")
        return {"skipped": "LLM yapılandırılmamış"}
    rep = summarize_pending(sf, llm, settings, ids=[reg_id], force=True)
    match_pending(sf, llm, settings, ids=[reg_id], force=True)
    with sf() as s:
        add_event(s, reg_id, "summary_regenerated", actor=actor,
                  note="Özet ve birim önerileri yeniden üretildi." if rep.summarized else "Yeniden üretim başarısız.")
        s.commit()
    return {"summarized": rep.summarized}


class ReprocessIn(BaseModel):
    stage: Literal["extract", "classify", "summarize", "match"]
    date_from: date = Field(alias="from")
    date_to: date = Field(alias="to")
    source: str | None = None


class RetryFailedIn(BaseModel):
    stage: Literal["classify", "summarize", "match"] | None = None
    source: str | None = None
    ids: list[int] | None = Field(default=None, min_length=1, max_length=500)   # yalnızca bu kayıtlar


@admin.post("/retry-failed", status_code=202)
def admin_retry_failed(session: SessionDep, background: BackgroundTasks, settings: SettingsDep, actor: ActorDep,
                       request: Request, body: RetryFailedIn | None = None):
    """Deneme hakkı bitmiş olanlar dahil tüm ``AI_FAILED`` kayıtları, hata aşamasından başlayarak yeniden çalıştırır."""
    from app.monitoring.reprocess import failed_regulations
    from app.services.jobs import create_job, run_job

    require(actor, ROLE_ADMIN)
    if settings.llm_provider in ("", "none", None):
        raise ApiError(400, "llm_required", "bu işlem için LLM yapılandırılmalı")
    body = body or RetryFailedIn()
    count = len(failed_regulations(session, stages=[body.stage] if body.stage else None, source=body.source,
                                   ids=body.ids))
    out: dict = {"status": "queued" if count else "nothing_to_do", "count": count}
    if count:
        sf = request.app.state.session_factory
        out["jobId"] = create_job(session, "retry_failed", source=body.source, requested_by=actor.name,
                                  params={"stage": body.stage, "source": body.source, "count": count,
                                          "ids": body.ids})
        background.add_task(run_job, sf, out["jobId"], _retry_failed_bg, sf, settings, body, actor)
    return out


def _retry_failed_bg(sf, settings: Settings, body: "RetryFailedIn", actor: Actor) -> dict:
    from app.ai.llm_client import get_llm
    from app.monitoring.reprocess import retry_failed

    result = retry_failed(sf, settings, llm=get_llm(settings), stages=[body.stage] if body.stage else None,
                          source=body.source, ids=body.ids, actor=actor)
    log.info("retry-failed: %s", result)
    return result


class RebaselineIn(BaseModel):
    cutoff: date


@admin.get("/rebaseline")
def admin_rebaseline_preview(session: SessionDep, settings: SettingsDep, actor: ActorDep, cutoff: date | None = None):
    """Baseline sınırı önizlemesi: kaynak başına dönüştürülecek kayıt ve taranacak aralık. ``cutoff`` verilmezse yalnızca
    kayıtlı sınır döner."""
    from app.services.rebaseline import get_cutoff, plan_rebaseline

    require(actor, ROLE_ADMIN)
    if cutoff is None:
        current = get_cutoff(session)
        return {"current": current.isoformat() if current else None}
    return plan_rebaseline(session, settings, cutoff)


@admin.post("/rebaseline", status_code=202)
def admin_rebaseline(body: RebaselineIn, session: SessionDep, background: BackgroundTasks, settings: SettingsDep,
                     actor: ActorDep, request: Request):
    """Sınır tarihi ve sonrasındaki BASELINE kayıtları YZ hattına alır; sınıra kadar taranmamış kaynakları tarar."""
    from app.services.jobs import create_job, run_job
    from app.services.rebaseline import plan_rebaseline

    require(actor, ROLE_ADMIN)
    if settings.llm_provider in ("", "none", None):
        raise ApiError(400, "llm_required", "bu işlem için LLM yapılandırılmalı")
    if body.cutoff > date.today():
        raise ApiError(400, "invalid_cutoff", "sınır tarihi bugünden ileri olamaz")
    from app.db import JobRun
    from app.services.jobs import sweep_stale

    sweep_stale(session, settings)
    session.commit()
    if session.scalars(select(JobRun).where(JobRun.kind == "rebaseline",
                                            JobRun.status.in_(("queued", "running")))).first():
        raise ApiError(409, "rebaseline_running", "Baseline sınırı işi zaten sürüyor; bitmesini bekleyin")
    plan = plan_rebaseline(session, settings, body.cutoff)
    sf = request.app.state.session_factory
    jid = create_job(session, "rebaseline", requested_by=actor.name,
                     params={"cutoff": body.cutoff.isoformat(), "count": plan["converted"], "crawl": plan["crawl"]})
    background.add_task(run_job, sf, jid, _rebaseline_bg, sf, settings, body, actor, jid)
    return {"status": "queued", "count": plan["converted"], "crawl": plan["crawl"], "jobId": jid}


def _rebaseline_bg(sf, settings: Settings, body: "RebaselineIn", actor: Actor, job_id: int) -> dict:
    from app.ai.llm_client import get_llm
    from app.services.rebaseline import apply_rebaseline

    result = apply_rebaseline(sf, settings, body.cutoff, llm=get_llm(settings), actor=actor, job_id=job_id)
    log.info("rebaseline: %s", result)
    return result


class BackfillIn(BaseModel):
    date_from: date = Field(alias="from")
    date_to: date = Field(alias="to")


@admin.get("/alerts")
def admin_alerts(session: SessionDep, actor: ActorDep, status: Literal["open", "closed", "all"] = "open",
                 limit: Annotated[int, Query(ge=1, le=500)] = 100):
    from app.db import Alert

    require(actor, ROLE_ADMIN)
    q = select(Alert).order_by(Alert.opened_at.desc()).limit(limit)
    if status == "open":
        q = q.where(Alert.resolved_at.is_(None))
    elif status == "closed":
        q = q.where(Alert.resolved_at.is_not(None))
    from app.monitoring.alerts import alert_link

    return [{"id": a.id, "key": a.key, "type": a.alert_type, "severity": a.severity, "source": a.source_code,
             "message": a.message, "details": a.details, "openedAt": _iso_dt(a.opened_at),
             "lastSeenAt": _iso_dt(a.last_seen_at), "resolvedAt": _iso_dt(a.resolved_at),
             "link": alert_link(a.alert_type), "acknowledgedBy": (a.details or {}).get("acknowledged_by")}
            for a in session.scalars(q)]


@admin.post("/alerts/{alert_id}/resolve")
def admin_alert_resolve(alert_id: int, body: AlertResolveIn, session: SessionDep, settings: SettingsDep,
                        actor: ActorDep):
    """Alarmı elle kapatır. Bulgu sürerse ``alert_snooze_hours`` boyunca yeniden açılmaz; kimin ne zaman kapattığı
    ``details``'ta kalır."""
    from app.db import Alert, utcnow

    require(actor, ROLE_ADMIN)
    a = session.get(Alert, alert_id)
    if a is None:
        raise ApiError(404, "not_found", "alarm bulunamadı")
    if a.resolved_at is not None:
        raise ApiError(409, "already_resolved", "alarm zaten kapalı")
    now = utcnow()
    a.resolved_at = now
    a.details = {**(a.details or {}), "acknowledged_by": actor.name, "acknowledged_at": now.isoformat(),
                 "note": (body.note or "").strip() or None}
    session.commit()
    log.info("alarm elle kapatıldı: %s (%s) — %s", a.key, a.id, actor.name)
    return {"id": a.id, "resolvedAt": _iso_dt(a.resolved_at), "snoozeHours": settings.alert_snooze_hours}


def _iso_dt(dt):
    from app.services.regulations import _iso

    return _iso(dt)


@admin.get("/audit/verify")
def admin_audit_verify(session: SessionDep, actor: ActorDep, regulation_id: int | None = None):
    from app.services.audit import verify_chain

    require(actor, ROLE_ADMIN)
    problems = verify_chain(session, regulation_id)
    return {"ok": not any(p["problem"] != "hash yok (zincir öncesi)" for p in problems), "problems": problems}


@admin.post("/monitor/run")
def admin_monitor_run(session: SessionDep, settings: SettingsDep, actor: ActorDep):
    from app.monitoring.service import run_monitor

    require(actor, ROLE_ADMIN)
    res = run_monitor(session, settings)
    session.commit()
    return {"statuses": {s.code: s.status for s in res.statuses}, "opened": len(res.sync.opened),
            "resolved": len(res.sync.resolved)}


@admin.post("/reprocess", status_code=202)
def admin_reprocess(body: ReprocessIn, background: BackgroundTasks, session: SessionDep, settings: SettingsDep,
                    actor: ActorDep, request: Request, dry_run: bool = False):
    """``dry_run=true``: kuyruğa almadan etki tahmini (kayıt sayısı, süre, LLM çağrısı, kararı korunacak kayıt)."""
    from app.services.jobs import create_job, run_job

    require(actor, ROLE_ADMIN)
    if body.date_to < body.date_from:
        raise ApiError(400, "invalid_range", "from ≤ to olmalı")
    if dry_run:
        from app.monitoring.reprocess import estimate

        return {"dryRun": True, "stage": body.stage, **estimate(session, settings, body.stage, body.date_from,
                                                                   body.date_to, body.source)}
    if body.stage != "extract" and settings.llm_provider in ("", "none", None):
        raise ApiError(400, "llm_required", "bu aşama için LLM yapılandırılmalı")
    sf = request.app.state.session_factory
    jid = create_job(session, "reprocess", source=body.source, requested_by=actor.name,
                     params={"stage": body.stage, "from": body.date_from.isoformat(), "to": body.date_to.isoformat(),
                             "source": body.source})
    background.add_task(run_job, sf, jid, _reprocess_bg, sf, settings, body, actor)
    return {"status": "queued", "stage": body.stage, "jobId": jid}


def _reprocess_bg(sf, settings: Settings, body: "ReprocessIn", actor: Actor) -> dict:
    from app.ai.llm_client import get_llm
    from app.monitoring.reprocess import reprocess

    result = reprocess(sf, settings, body.stage, body.date_from, body.date_to, source=body.source,
                       llm=get_llm(settings), actor=actor)
    log.info("reprocess: %s", result)
    return result


@admin.post("/sources/{code}/backfill", status_code=202)
def admin_backfill(code: str, body: BackfillIn, background: BackgroundTasks, session: SessionDep, settings: SettingsDep,
                   actor: ActorDep, request: Request):
    from app.collectors.config import load_sources
    from app.services.jobs import create_job, run_job

    require(actor, ROLE_ADMIN)
    try:
        code = load_sources(settings.sources_file).get(code).code   # kanonik kod: crawler büyük/küçük harfe duyarlı
    except KeyError as e:
        raise ApiError(404, "not_found", str(e)) from e
    if body.date_to < body.date_from or (body.date_to - body.date_from).days > 62:
        raise ApiError(400, "invalid_range", "from ≤ to ve en fazla 62 gün")
    days = (body.date_to - body.date_from).days + 1
    if settings.crawl_mode == "remote":   # LAN internete çıkamaz: DMZ crawler'a tarama talebi
        rid = _crawl_request(settings, "backfill", code, {"date_from": body.date_from.isoformat(),
                                                          "date_to": body.date_to.isoformat()}, actor)
        return {"status": "queued", "via": "crawler", "requestId": rid, "days": days}
    sf = request.app.state.session_factory
    jid = create_job(session, "backfill", source=code, requested_by=actor.name,
                     params={"from": body.date_from.isoformat(), "to": body.date_to.isoformat()})
    background.add_task(run_job, sf, jid, _backfill_bg, sf, settings, code, body)
    return {"status": "queued", "days": days, "jobId": jid}


def _crawl_request(settings: Settings, kind: str, code: str, params: dict, actor) -> str:
    from app.mongo import crawl_db, submit_crawl_request

    return submit_crawl_request(crawl_db(settings), kind, code, params, requested_by=actor.id)


def _backfill_bg(sf, settings: Settings, code: str, body: "BackfillIn") -> dict:
    from dataclasses import asdict

    from app.collectors.locks import LockBusy, source_lock
    from app.monitoring.reprocess import backfill, local_store

    try:   # zamanlanmış taramayla yarışmasın
        with source_lock(code, redis_url=settings.redis_url, lock_dir=settings.lock_dir):
            rep = backfill(local_store(sf, settings), settings, code, body.date_from, body.date_to)
    except LockBusy:
        log.info("backfill %s: başka bir tarama sürüyor, atlandı", code)
        return {"skipped": "başka bir tarama sürüyor"}
    log.info("backfill %s: %s", code, rep.runs)
    return asdict(rep)


@admin.post("/sources/{code}/run", status_code=202)
def admin_run_source(code: str, background: BackgroundTasks, session: SessionDep, settings: SettingsDep,
                     actor: ActorDep, request: Request):
    from app.collectors.config import load_sources
    from app.services.jobs import create_job, run_job

    require(actor, ROLE_ADMIN)
    try:
        code = load_sources(settings.sources_file).get(code).code   # kanonik kod: crawler büyük/küçük harfe duyarlı
    except KeyError as e:
        raise ApiError(404, "not_found", str(e)) from e
    if settings.crawl_mode == "remote":
        return {"status": "queued", "via": "crawler", "requestId": _crawl_request(settings, "run", code, {}, actor)}
    if settings.redis_url:
        from app.tasks.collect import collect_source

        jid = create_job(session, "run", source=code, requested_by=actor.name, via="celery")
        collect_source.delay(code, job_id=jid)
        return {"status": "queued", "via": "celery", "jobId": jid}
    sf = request.app.state.session_factory
    jid = create_job(session, "run", source=code, requested_by=actor.name, via="inline")
    background.add_task(run_job, sf, jid, _run_source_inline, sf, settings, code)
    return {"status": "queued", "via": "inline", "jobId": jid}


def _run_source_inline(sf, settings: Settings, code: str) -> dict:
    """``tasks.collect.collect_source`` ile aynı yol (kilit + toplayıcı), Celery olmadan."""
    from app.collectors.config import load_sources
    from app.collectors.locks import LockBusy, source_lock
    from app.collectors.runner import SourceCollector
    from app.collectors.store import SqlCrawlStore
    from app.storage import get_storage

    source = load_sources(settings.sources_file).get(code)
    try:
        with source_lock(code, redis_url=settings.redis_url, lock_dir=settings.lock_dir):
            collector = SourceCollector(source, settings, SqlCrawlStore(sf, get_storage(settings)))
            try:
                report = collector.run()
            finally:
                collector.fetcher.close()
    except LockBusy:
        log.info("%s: başka bir tarama sürüyor, atlandı", code)
        return {"skipped": "başka bir tarama sürüyor"}
    return {"runId": report.run_id, "status": report.status, "new": report.new, "changed": report.changed}


# ------------------------------------------------------------------------------------------------ paralel çalışma (İK-8)

evaluation = APIRouter(prefix="/api/v1/evaluation")


class ManualDetectionIn(BaseModel):
    title: str = Field(min_length=3, max_length=1000)
    issuer: str | None = None
    publish_date: date | None = None
    url: str | None = None
    severity: Literal["Kritik", "Yüksek", "Orta", "Düşük"] | None = None
    unit_codes: list[str] = Field(default_factory=list)
    note: str | None = None


def _md_out(md) -> dict:
    return {"id": md.id, "title": md.title, "issuer": md.issuer,
            "publishDate": md.publish_date.isoformat() if md.publish_date else None, "url": md.url,
            "matchedRegulationId": md.matched_regulation_id, "matchMethod": md.match_method,
            "matchScore": md.match_score}


@evaluation.post("/manual-detections", status_code=201)
def manual_detection(body: ManualDetectionIn, session: SessionDep, actor: ActorDep):
    from app.evaluation.parallel import add_detection

    require(actor, ROLE_EXPERT)
    md = add_detection(session, body.model_dump(), actor.name)
    session.commit()
    return _md_out(md)


@evaluation.post("/manual-detections/upload", status_code=201)
async def manual_detection_upload(session: SessionDep, actor: ActorDep, file: UploadFile):
    from app.evaluation.parallel import add_detection, normalize_row, parse_rows

    require(actor, ROLE_EXPERT)
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise ApiError(413, "too_large", "dosya en fazla 5 MB olabilir")
    if not (file.filename or "").lower().endswith((".csv", ".txt", ".xlsx", ".xlsm")):
        raise ApiError(400, "unsupported_file", "CSV veya Excel (.xlsx) yükleyin")
    try:
        rows = [normalize_row(r) for r in parse_rows(file.filename, content)]
    except Exception as e:  # noqa: BLE001 — bozuk dosya
        raise ApiError(400, "invalid_file", f"dosya okunamadı: {e}") from e
    added = [add_detection(session, r, actor.name) for r in rows]
    session.commit()
    return {"added": len(added), "matched": sum(1 for m in added if m.matched_regulation_id),
            "items": [_md_out(m) for m in added]}


@evaluation.post("/excel-compare")
async def excel_compare(session: SessionDep, settings: SettingsDep, actor: ActorDep, file: UploadFile,
                        format: Literal["json", "xlsx"] = "json",
                        title_threshold: Annotated[float, Query(ge=50, le=100)] = 85,
                        text_threshold: Annotated[float, Query(ge=50, le=100)] = 80,
                        date_from: Annotated[date | None, Query(alias="from")] = None,
                        date_to: Annotated[date | None, Query(alias="to")] = None):
    """Başkanlık Excel listesini sistem kayıtlarıyla karşılaştırır; veritabanına yazmaz. ``format=xlsx`` aynı sonucu
    çalışma kitabı olarak döner (sunucu durum tutmaz, dosya yeniden gönderilir)."""
    from app.evaluation.excel_compare import build_workbook as compare_workbook
    from app.evaluation.excel_compare import compare, compare_filename, parse_sheet

    require(actor, ROLE_VIEWER)
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise ApiError(413, "too_large", "dosya en fazla 5 MB olabilir")
    if not (file.filename or "").lower().endswith((".csv", ".txt", ".xlsx", ".xlsm")):
        raise ApiError(400, "unsupported_file", "CSV veya Excel (.xlsx) yükleyin")
    try:
        rows = parse_sheet(file.filename, content)
    except Exception as e:  # noqa: BLE001 — bozuk dosya
        raise ApiError(400, "invalid_file", f"dosya okunamadı: {e}") from e
    if not rows:
        raise ApiError(400, "empty_file", "dosyada Konu veya Metin sütunu dolu satır bulunamadı")
    result = compare(session, rows, title_threshold=title_threshold, text_threshold=text_threshold,
                     date_from=date_from, date_to=date_to)
    session.rollback()   # salt okunur: eşleştirme sırasında oluşan hiçbir şey yazılmaz
    if format == "xlsx":
        headers = {"Content-Disposition": f'attachment; filename="{compare_filename(today(settings))}"',
                   "Access-Control-Expose-Headers": "Content-Disposition"}
        return Response(compare_workbook(result), media_type=XLSX_TYPE, headers=headers)
    return result


@evaluation.get("/manual-detections")
def manual_detections(session: SessionDep, actor: ActorDep, date_from: Annotated[date | None, Query(alias="from")] = None,
                      date_to: Annotated[date | None, Query(alias="to")] = None):
    from app.db import ManualDetection

    require(actor, ROLE_VIEWER)
    q = select(ManualDetection).order_by(ManualDetection.publish_date.desc())
    if date_from and date_to:
        q = q.where(ManualDetection.publish_date.between(date_from, date_to))
    return [_md_out(m) for m in session.scalars(q.limit(1000))]


@evaluation.get("/report")
def evaluation_report(session: SessionDep, actor: ActorDep, date_from: Annotated[date, Query(alias="from")],
                      date_to: Annotated[date, Query(alias="to")]):
    from app.evaluation.parallel import report

    require(actor, ROLE_VIEWER)
    return report(session, date_from, date_to)


@evaluation.get("/filtered-out")
def evaluation_filtered_out(session: SessionDep, actor: ActorDep, date_from: Annotated[date, Query(alias="from")],
                            date_to: Annotated[date, Query(alias="to")]):
    from app.evaluation.parallel import filtered_out

    require(actor, ROLE_VIEWER)
    return filtered_out(session, date_from, date_to)


# ------------------------------------------------------------------------------------------------ uygulama


def create_app(settings: Settings | None = None, session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Mevzuat Takip Portal API", version="1.0",
                  description="İK-6 — Mevzuat ve Uyum Başkanlığı portalı için API (plan §6)")
    app.state.settings = settings
    app.state.session_factory = session_factory or make_sessionmaker(settings.database_url)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"],
                           expose_headers=["ETag"])

    @app.exception_handler(ApiError)
    def _api_error(request: Request, exc: ApiError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
                            status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    def _validation(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": {"code": "validation_error", "message": "geçersiz istek",
                                       "details": {"errors": _jsonable(exc.errors())}}}, status_code=422)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(request: Request):
        checks = {}
        try:
            with request.app.state.session_factory() as s:
                s.execute(text("select 1"))
            checks["db"] = "ok"
        except Exception:  # noqa: BLE001
            # kimlik doğrulamasız uç: istisna metni (sunucu adresi, kullanıcı adı) yanıta konmaz, yalnızca loga yazılır
            log.exception("readyz: veritabanına erişilemedi")
            checks["db"] = "hata"
        checks["llm"] = "yapılandırılmış" if settings.llm_provider not in ("", "none", None) else "kapalı"
        checks["ocr"] = "yapılandırılmış" if settings.ocr_provider not in ("", "none", None) else "kapalı"
        ok = checks["db"] == "ok"
        return JSONResponse({"status": "ok" if ok else "hata", "checks": checks}, status_code=200 if ok else 503)

    app.include_router(api)
    app.include_router(admin)
    app.include_router(evaluation)

    @app.get("/", include_in_schema=False)
    def portal():
        return FileResponse(settings.portal_dir / PORTAL_FILE, media_type="text/html; charset=utf-8")

    def _redirect(target: str):
        return lambda: RedirectResponse(target, status_code=307)

    for old, target in OLD_ROUTES.items():
        app.get(old, include_in_schema=False)(_redirect(target))

    @app.get("/eski", include_in_schema=False)
    @app.get("/eski/{page}", include_in_schema=False)
    def legacy_page(page: str = ""):
        """Yedekteki eski arayüz sayfaları (yalnızca bilinen dört sayfa)."""
        if page not in LEGACY_PAGES:
            raise ApiError(404, "not_found", "sayfa bulunamadı")
        return FileResponse(settings.portal_dir / LEGACY_DIR / LEGACY_PAGES[page], media_type="text/html; charset=utf-8")

    @app.get("/support.js", include_in_schema=False)
    def support_js():
        return FileResponse(settings.portal_dir / "support.js", media_type="application/javascript")

    @app.get("/vendor/{name}", include_in_schema=False)
    def vendor_file(name: str):
        """Portalın üçüncü taraf betikleri (React, lucide): kurum ağında dış CDN'e (unpkg) çıkılmaz.
        Dosya adları sürüm içerir ve sabittir; yalnızca düz dosya adı kabul edilir (dizin gezinmesi yok)."""
        path = settings.portal_dir / "vendor" / name
        if name != Path(name).name or path.suffix not in VENDOR_TYPES or not path.is_file():
            raise ApiError(404, "not_found", "dosya bulunamadı")
        return FileResponse(path, media_type=VENDOR_TYPES[path.suffix],
                            headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/assets/{name}", include_in_schema=False)
    def portal_asset(name: str):
        """Portalın kendi CSS/JS dosyaları (``portal/assets/``). Dosya adları sürümsüz olduğundan önbellek her seferinde
        doğrulanır (``no-cache``). Yalnızca düz dosya adı ve ``.css``/``.js`` kabul edilir (dizin gezinmesi yok)."""
        path = settings.portal_dir / "assets" / name
        if name != Path(name).name or path.suffix not in ASSET_TYPES or not path.is_file():
            raise ApiError(404, "not_found", "dosya bulunamadı")
        return FileResponse(path, media_type=ASSET_TYPES[path.suffix], headers={"Cache-Control": "no-cache"})

    return app


def _jsonable(errors: list) -> list:
    return [{k: (str(v) if k == "ctx" else v) for k, v in e.items() if k in ("loc", "msg", "type", "ctx")}
            for e in errors]


def __getattr__(name: str):
    # ``uvicorn app.api.main:app`` için tembel oluşturma (içe aktarma sırasında DB'ye bağlanmasın)
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
