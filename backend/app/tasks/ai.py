"""İK-3 YZ görevleri (Celery, ``ai`` kuyruğu — GPU'ya gittiği için eşzamanlılık düşük tutulur: -c 2)."""
from __future__ import annotations

from functools import lru_cache

from app.ai.classify import classify_pending
from app.ai.llm_client import get_llm
from app.db import make_sessionmaker
from app.settings import get_settings
from app.worker import app


@lru_cache
def _session_factory():
    return make_sessionmaker(get_settings().database_url)


@app.task(name="app.tasks.ai.classify_regulations")
def classify_regulations(ids: list[int] | None = None, limit: int = 100) -> dict:
    s = get_settings()
    llm = get_llm(s)
    if llm is None:
        return {"status": "skipped", "reason": "LLM_PROVIDER=none"}
    rep = classify_pending(_session_factory(), llm, s, ids=ids, limit=limit)
    if rep.relevant_ids:
        summarize_regulations.delay(ids=rep.relevant_ids)
    return {k: v for k, v in rep.__dict__.items() if k != "errors"} | {"errors": rep.errors[:20]}


@app.task(name="app.tasks.ai.summarize_regulations")
def summarize_regulations(ids: list[int] | None = None, limit: int = 50) -> dict:
    from app.ai.summary import summarize_pending

    s = get_settings()
    llm = get_llm(s)
    if llm is None:
        return {"status": "skipped", "reason": "LLM_PROVIDER=none"}
    rep = summarize_pending(_session_factory(), llm, s, ids=ids, limit=limit)
    if rep.ids or rep.awaiting_text:
        match_units.delay(ids=rep.ids or None)
    return {k: v for k, v in rep.__dict__.items() if k != "errors"} | {"errors": rep.errors[:20]}


@app.task(name="app.tasks.ai.match_units")
def match_units(ids: list[int] | None = None, limit: int = 100) -> dict:
    from app.ai.unit_matching import match_pending

    s = get_settings()
    llm = get_llm(s)
    if llm is None:
        return {"status": "skipped", "reason": "LLM_PROVIDER=none"}
    rep = match_pending(_session_factory(), llm, s, ids=ids, limit=limit)
    return {k: v for k, v in rep.__dict__.items() if k != "errors"} | {"errors": rep.errors[:20]}
