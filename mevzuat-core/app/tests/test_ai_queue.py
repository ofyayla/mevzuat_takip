"""Worker kuyruk teşhisi: sıfır işlenen görev ile gerçekten boş kuyruk ayrılır."""
from __future__ import annotations

import logging

import pytest

from app.db import Regulation
from app.services.scans import ai_queue, log_ai_queue
from tests.test_api import client  # noqa: F401
from tests.test_processing import env  # noqa: F401


def _backlog(sf):
    with sf() as s:
        for status, count, relevant, extra in (
            ("NEW", 7, None, {}),
            ("RELEVANT", 40, True, {"summary_blocked": "metin_yok"}),
            ("RELEVANT", 5, True, {}),
            ("SUMMARIZED", 2, True, {"unit_match": {"status": "önerilemedi"}}),
            ("AI_FAILED", 5, True, {"ai_stage": "summary", "ai_attempts": 3}),
            ("AI_FAILED", 3, True, {"ai_stage": "unit_match", "ai_attempts": 1}),
            ("READY", 4, True, {}),
            ("BASELINE", 2, None, {}),
            ("MERGED", 1, True, {}),
            ("IRRELEVANT", 3, False, {}),
        ):
            for i in range(count):
                key = f"{status}:{relevant}:{extra}:{i}"
                s.add(Regulation(title="private document", primary_source_code="BDDK", canonical_key=key,
                                 processing_status=status, is_relevant=relevant, extra=extra))
        s.commit()


def test_queue_separates_55_preparing_records_from_new_and_completed(env):  # noqa: F811
    sf, _ = env
    _backlog(sf)
    with sf() as s:
        queue = ai_queue(s)
    assert {s["status"]: s["count"] for s in queue["stages"]} == {
        "NEW": 7, "RELEVANT": 45, "SUMMARIZED": 2, "AI_FAILED": 8}
    assert queue["waiting"] == 54 and queue["total"] == 62
    assert queue["preparing"] == 55 and queue["summaryBlocked"] == 40
    assert queue["failedRetryable"] == 3 and queue["failedExhausted"] == 5
    assert queue["failedByStage"] == {"summary": 5, "unit_match": 3}


def test_empty_queue_and_legacy_failure_without_extra(env):  # noqa: F811
    sf, _ = env
    with sf() as s:
        queue = ai_queue(s)
        assert queue["total"] == queue["preparing"] == queue["summaryBlocked"] == 0
        assert queue["failedRetryable"] == queue["failedExhausted"] == 0
        assert queue["failedByStage"] == {}
        s.add(Regulation(title="legacy", primary_source_code="BDDK", canonical_key="legacy",
                         processing_status="AI_FAILED"))
        s.commit()
        queue = ai_queue(s)
    assert queue["total"] == queue["failedRetryable"] == 1 and queue["preparing"] == 0
    assert queue["failedByStage"] == {"relevance": 1}


def test_queue_log_explains_blockers_without_document_contents(env, caplog):  # noqa: F811
    sf, _ = env
    _backlog(sf)
    with sf() as s, caplog.at_level(logging.INFO, logger="app.services.scans"):
        assert log_ai_queue(s)["preparing"] == 55
    assert "preparing=55 summary_blocked=40 retryable=3 exhausted=5" in caplog.text
    assert "NEW=7 RELEVANT=45 SUMMARIZED=2 AI_FAILED=8" in caplog.text
    assert "private document" not in caplog.text


def test_scans_api_exposes_the_same_backlog_as_worker_logs(env, client):  # noqa: F811
    sf, _ = env
    _backlog(sf)
    response = client[0].get("/api/v1/admin/scans")
    assert response.status_code == 200
    queue = response.json()["aiQueue"]
    assert queue["preparing"] == 55 and queue["summaryBlocked"] == 40
    assert queue["failedRetryable"] == 3 and queue["failedExhausted"] == 5


@pytest.mark.parametrize("task_name", ["classify_regulations", "summarize_regulations", "match_units"])
def test_zero_processed_ai_task_still_reports_blocked_backlog(settings, env, monkeypatch, task_name):  # noqa: F811
    from app.ai.llm_client import FakeLLM
    from app.tasks import ai

    sf, _ = env
    _backlog(sf)
    llm = FakeLLM(settings, lambda *a: pytest.fail("No document should be sent to the LLM"))
    monkeypatch.setattr(ai, "_session_factory", lambda: sf)
    monkeypatch.setattr(ai, "get_settings", lambda: settings)
    monkeypatch.setattr(ai, "get_llm", lambda _: llm)
    result = getattr(ai, task_name)(ids=[999999])
    assert result["processed"] == 0 and result["aiQueue"]["preparing"] == 55
    assert result["aiQueue"]["failedExhausted"] == 5
    assert llm.calls == []


def test_monitor_reports_backlog_even_when_no_source_alert_is_opened(settings, env, monkeypatch, caplog):  # noqa: F811
    from types import SimpleNamespace

    from app.tasks import monitor

    sf, _ = env
    _backlog(sf)
    monkeypatch.setattr(monitor, "_session_factory", lambda: sf)
    monkeypatch.setattr(monitor, "get_settings", lambda: settings)
    monkeypatch.setattr(monitor, "run_monitor", lambda *a: SimpleNamespace(
        statuses=[], sync=SimpleNamespace(opened=[], resolved=[], still_open=0)))
    with caplog.at_level(logging.INFO, logger="app.services.scans"):
        result = monitor.run_monitor_task()
    assert result["opened"] == 0 and result["aiQueue"]["preparing"] == 55
    assert "summary_blocked=40 retryable=3 exhausted=5" in caplog.text
