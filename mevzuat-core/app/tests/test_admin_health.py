from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db import FetchRun, HealthSample, LlmCall, Regulation
from app.monitoring.service import run_monitor
from app.services import admin_metrics
from tests.test_api import client  # noqa: F401
from tests.test_processing import env  # noqa: F401

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def test_llm_series_uses_hourly_p95_and_keeps_empty_hours_null(settings, env):  # noqa: F811
    sf, _ = env
    with sf() as s:
        for i, (hours, latency, status) in enumerate([
            (1.5, 100, "ok"), (1.4, 300, "ok"), (1.3, 9999, "error"),
            (0, 60000, "ok"), (25, 90000, "ok"), (-1, 90000, "ok"),
        ]):
            s.add(LlmCall(task="summary", provider="fake", model="m", prompt_version="p",
                          input_hash=str(i), latency_ms=latency, status=status,
                          created_at=NOW - timedelta(hours=hours)))
        s.commit()
        result = admin_metrics.llm_latency(s, NOW)
    assert result["calls"] == 4 and result["failed"] == 1
    assert result["p95Ms"] == 54030 and result["p50Ms"] == 300
    assert result["targetMs"] == 60000
    values = [p["value"] for p in result["series"]]
    assert len(values) == 24 and values[:-2] == [None] * 22
    assert values[-2:] == [290, 60000]
    assert result["series"][-1]["at"] == NOW.isoformat()


def test_scan_success_counts_only_completed_runs_in_window(settings, env):  # noqa: F811
    sf, _ = env
    with sf() as s:
        for days, status in [(1, "success"), (1, "partial"), (1, "failed"),
                             (2, "success"), (8, "failed"), (-1, "failed")]:
            at = NOW - timedelta(days=days)
            s.add(FetchRun(source_code="BDDK", started_at=at, finished_at=at, status=status))
        s.add(FetchRun(source_code="BDDK", started_at=NOW, status="running"))
        s.commit()
        result = admin_metrics.scan_success(s, NOW)
    assert result["total"] == 4 and result["failed"] == 2
    assert result["successRate"] == 50 and result["targetRate"] == 98
    assert [p["value"] for p in result["series"]] == [None] * 5 + [100, 33.3]


def test_scan_success_matches_reference_percentage(settings, env):  # noqa: F811
    sf, _ = env
    with sf() as s:
        s.add_all(FetchRun(source_code="BDDK", started_at=NOW, finished_at=NOW,
                           status="failed" if i < 43 else "success") for i in range(578))
        s.commit()
        result = admin_metrics.scan_success(s, NOW)
    assert result["successRate"] == 92.6 and result["failed"] == 43 and result["total"] == 578


def test_history_uses_last_sample_per_hour_including_unknown_measurements(settings, env):  # noqa: F811
    sf, _ = env
    remote = settings.model_copy(update={"crawl_mode": "remote"})
    with sf() as s:
        s.add_all([
            HealthSample(captured_at=NOW - timedelta(minutes=150), ai_queue=4,
                         pending_ingest=8, crawl_mode="remote"),
            HealthSample(captured_at=NOW - timedelta(minutes=140), ai_queue=5,
                         pending_ingest=None, crawl_mode="remote"),
        ])
        s.commit()
        result = admin_metrics.queue_history(s, remote, NOW, 0)
    assert result["aiQueue"]["series"][-3]["value"] == 5
    assert result["ingest"]["series"][-3]["value"] is None
    assert result["ingest"]["series"][-1]["value"] == 0


def test_monitor_persists_samples_prunes_old_history_and_matches_portal_queue(settings, env, monkeypatch):  # noqa: F811
    sf, _ = env
    monkeypatch.setattr(admin_metrics, "_crawler_state", lambda _: ({}, 7))
    remote = settings.model_copy(update={"crawl_mode": "remote"})
    with sf() as s:
        for i, (status, relevant) in enumerate([
            ("RELEVANT", True), ("SUMMARIZED", True), ("AI_FAILED", True),
            ("READY", True), ("NEW", None), ("AI_FAILED", False),
        ]):
            s.add(Regulation(title=str(i), primary_source_code="BDDK", canonical_key=str(i),
                             processing_status=status, is_relevant=relevant))
        s.add_all([
            HealthSample(captured_at=NOW - timedelta(days=15), ai_queue=99, pending_ingest=99, crawl_mode="remote"),
            HealthSample(captured_at=NOW - timedelta(hours=3), ai_queue=1, pending_ingest=2, crawl_mode="remote"),
        ])
        s.commit()
        run_monitor(s, remote, NOW)
        s.commit()
    with sf() as s:
        rows = s.scalars(select(HealthSample).order_by(HealthSample.captured_at)).all()
        assert len(rows) == 2 and rows[-1].ai_queue == 3 and rows[-1].pending_ingest == 7
        result = admin_metrics.collect(s, remote, NOW)
        assert result["aiQueue"]["count"] == 3
        assert result["aiQueue"]["series"][-1]["value"] == 3
        assert result["ingest"]["series"][-1]["value"] == 7
        assert [p["value"] for p in result["ingest"]["series"]][-3:] == [2, None, 7]


def test_missing_ingest_and_mode_changes_do_not_become_zero(settings, env, monkeypatch, caplog):  # noqa: F811
    sf, _ = env
    remote = settings.model_copy(update={"crawl_mode": "remote", "mongo_url": ""})
    with sf() as s:
        s.add(HealthSample(captured_at=NOW - timedelta(hours=3), ai_queue=0,
                           pending_ingest=10, crawl_mode="remote"))
        s.commit()
        local = admin_metrics.collect(s, settings, NOW)
        assert local["pendingIngest"] is None
        assert all(p["value"] is None for p in local["ingest"]["series"])
        admin_metrics.record_health_sample(s, remote, NOW)
        s.commit()
        sample = s.scalar(select(HealthSample).order_by(HealthSample.captured_at.desc()).limit(1))
        assert sample.pending_ingest is None and sample.ai_queue == 0
    assert "Mongo" in caplog.text


def test_metrics_api_contract_is_read_only_and_zero_is_real_data(settings, env, client):  # noqa: F811
    c, sf = client
    for _ in range(2):
        response = c.get("/api/v1/admin/metrics")
        assert response.status_code == 200
        body = response.json()
        assert len(body["llm"]["series"]) == 24 and len(body["scans"]["series"]) == 7
        assert len(body["aiQueue"]["series"]) == 336 and len(body["ingest"]["series"]) == 24
        assert body["aiQueue"]["series"][-1]["value"] == 0
        assert body["scans"]["successRate"] is None and body["llm"]["p95Ms"] is None
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(HealthSample)) == 0
