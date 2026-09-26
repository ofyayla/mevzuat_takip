"""Portal API (İK-6, plan §6). ``uvicorn app.api.main:app`` — portal ``/`` adresinden sunulur.

Genel kurallar: ``/api/v1`` öneki; hata biçimi ``{"error": {"code", "message", "details"}}``; sayfalama
``page``/``page_size`` (10/25/50, en fazla 100); yazma işlemleri denetim izi kaydıyla tek transaction'da.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.auth import ROLE_ADMIN, ROLE_EXPERT, ROLE_VIEWER, current_actor, require
from app.db import Unit, make_sessionmaker
from app.services.audit import Actor
from app.services.health import sources_health
from app.services.regulations import (
    REVIEW_STATUSES,
    ApiError,
    ListQuery,
    change_units,
    decide,
    get_visible,
    list_regulations,
    mark_viewed,
    serialize,
    stats,
)
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)
PORTAL_FILE = "Mevzuat Takip Portali.dc.html"


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


class UnitsIn(BaseModel):
    unit_codes: list[str] = Field(min_length=1, max_length=10)
    note: str | None = Field(default=None, max_length=2000)


class SettingsIn(BaseModel):
    values: dict[str, str]


class SplitIn(BaseModel):
    raw_document_id: int


# ------------------------------------------------------------------------------------------------ portal API

api = APIRouter(prefix="/api/v1")


@api.get("/regulations")
def regulations(session: SessionDep, settings: SettingsDep, actor: ActorDep,
                source: str | None = None, severity: str | None = None, status: str | None = None,
                unit: str | None = None, date_range: Literal["7", "30"] | None = None, q: str | None = None,
                metric: Literal["pending", "critical_high", "criticalHigh", "today", "upcoming"] | None = None,
                page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 10):
    require(actor, ROLE_VIEWER)
    if status and status not in REVIEW_STATUSES:
        raise ApiError(400, "invalid_status", f"status: {', '.join(REVIEW_STATUSES)}")
    return list_regulations(session, ListQuery(source, severity, status, unit, int(date_range) if date_range else None,
                                               q, metric, page, page_size), settings)


@api.get("/regulations/stats")
def regulation_stats(session: SessionDep, settings: SettingsDep, actor: ActorDep):
    require(actor, ROLE_VIEWER)
    return stats(session, settings)


@api.get("/regulations/{reg_id}")
def regulation_detail(reg_id: int, session: SessionDep, settings: SettingsDep, actor: ActorDep, response: Response):
    require(actor, ROLE_VIEWER)
    reg = get_visible(session, reg_id)
    response.headers["ETag"] = f'"{reg.row_version}"'
    return serialize(session, reg, settings, detail=True)


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
    reg = get_visible(session, reg_id)
    decide(session, reg, body.decision, body.note, actor, if_match)
    session.commit()
    response.headers["ETag"] = f'"{reg.row_version}"'
    return serialize(session, reg, settings, detail=True)


@api.put("/regulations/{reg_id}/units")
def regulation_units(reg_id: int, body: UnitsIn, session: SessionDep, settings: SettingsDep, actor: ActorDep,
                     response: Response, if_match: Annotated[str | None, Header()] = None):
    require(actor, ROLE_EXPERT)
    reg = get_visible(session, reg_id)
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


@api.get("/me")
def me(actor: ActorDep, settings: SettingsDep):
    return {"name": actor.name, "title": actor.title, "roles": sorted(actor.roles), "authMode": settings.auth_mode,
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
def admin_settings(session: SessionDep, settings: SettingsDep, actor: ActorDep):
    from app.ai.common import RUNTIME_KEYS, effective_settings

    require(actor, ROLE_ADMIN)
    eff = effective_settings(session, settings)
    return {k: getattr(eff, k) for k in sorted(RUNTIME_KEYS)}


@admin.put("/settings")
def admin_settings_put(body: SettingsIn, session: SessionDep, settings: SettingsDep, actor: ActorDep):
    from app.ai.common import set_runtime_setting

    require(actor, ROLE_ADMIN)
    try:
        for key, value in body.values.items():
            set_runtime_setting(session, key, value, by=actor.name)
    except (KeyError, ValueError) as e:
        raise ApiError(400, "invalid_setting", str(e)) from e
    session.commit()
    return admin_settings(session, settings, actor)


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
    require(actor, ROLE_ADMIN)
    get_visible(session, reg_id)
    background.add_task(_regenerate, request.app.state.session_factory, settings, reg_id, actor)
    return {"status": "queued"}


def _regenerate(sf: sessionmaker[Session], settings: Settings, reg_id: int, actor: Actor) -> None:
    from app.ai.llm_client import get_llm
    from app.ai.summary import summarize_pending
    from app.ai.unit_matching import match_pending
    from app.services.audit import add_event

    llm = get_llm(settings)
    if llm is None:
        log.warning("regenerate: LLM yapılandırılmamış")
        return
    rep = summarize_pending(sf, llm, settings, ids=[reg_id], force=True)
    match_pending(sf, llm, settings, ids=[reg_id], force=True)
    with sf() as s:
        add_event(s, reg_id, "summary_regenerated", actor=actor,
                  note="Özet ve birim önerileri yeniden üretildi." if rep.summarized else "Yeniden üretim başarısız.")
        s.commit()


@admin.post("/sources/{code}/run", status_code=202)
def admin_run_source(code: str, background: BackgroundTasks, settings: SettingsDep, actor: ActorDep):
    from app.collectors.config import load_sources

    require(actor, ROLE_ADMIN)
    try:
        load_sources(settings.sources_file).get(code)
    except KeyError as e:
        raise ApiError(404, "not_found", str(e)) from e
    if settings.redis_url:
        from app.tasks.collect import collect_source

        collect_source.delay(code)
        return {"status": "queued", "via": "celery"}
    background.add_task(_run_source_inline, settings, code)
    return {"status": "queued", "via": "inline"}


def _run_source_inline(settings: Settings, code: str) -> None:
    from app.collectors.cli import main as collect_main

    collect_main(["run", code])


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
        except Exception as e:  # noqa: BLE001
            checks["db"] = f"hata: {e}"
        checks["llm"] = "yapılandırılmış" if settings.llm_provider not in ("", "none", None) else "kapalı"
        checks["ocr"] = "yapılandırılmış" if settings.ocr_provider not in ("", "none", None) else "kapalı"
        ok = checks["db"] == "ok"
        return JSONResponse({"status": "ok" if ok else "hata", "checks": checks}, status_code=200 if ok else 503)

    app.include_router(api)
    app.include_router(admin)

    @app.get("/", include_in_schema=False)
    def portal():
        return FileResponse(settings.portal_dir / PORTAL_FILE, media_type="text/html; charset=utf-8")

    @app.get("/support.js", include_in_schema=False)
    def support_js():
        return FileResponse(settings.portal_dir / "support.js", media_type="application/javascript")

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
