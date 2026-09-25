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
    # İK-4: rep.relevant_ids özet kuyruğuna verilecek
    return {k: v for k, v in rep.__dict__.items() if k != "errors"} | {"errors": rep.errors[:20]}
