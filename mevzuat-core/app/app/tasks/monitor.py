"""İK-7 izleme görevi (Celery, ``monitor`` kuyruğu; Beat 10 dakikada bir)."""
from __future__ import annotations

from functools import lru_cache

from app.db import make_sessionmaker
from app.monitoring.service import run_monitor
from app.settings import get_settings
from app.worker import app


@lru_cache
def _session_factory():
    return make_sessionmaker(get_settings().database_url)


@app.task(name="app.tasks.monitor.run_monitor")
def run_monitor_task() -> dict:
    s = get_settings()
    with _session_factory()() as session:
        res = run_monitor(session, s)
        session.commit()
    return {"statuses": {st.code: st.status for st in res.statuses}, "opened": len(res.sync.opened),
            "resolved": len(res.sync.resolved), "open": res.sync.still_open + len(res.sync.opened)}
