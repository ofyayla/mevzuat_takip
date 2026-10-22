from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import FetchRun, HealthSample, LlmCall
from app.services.admin_metrics import collect, record_health_sample
from tests.test_processing import env  # noqa: F401


def test_admin_metrics_include_chart_series(settings, env):  # noqa: F811
    sf, _ = env
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    with sf() as session:
        session.add(LlmCall(
            task="summary", provider="fake", model="m", prompt_version="p", input_hash="ok",
            latency_ms=1250, status="ok", created_at=now - timedelta(minutes=20),
        ))
        session.add(LlmCall(
            task="summary", provider="fake", model="m", prompt_version="p", input_hash="failed",
            latency_ms=None, status="error", created_at=now - timedelta(minutes=10),
        ))
        for status, finished in (
            ("success", now - timedelta(hours=2)),
            ("partial", now - timedelta(hours=1)),
            ("failed", now - timedelta(days=9)),
        ):
            session.add(FetchRun(
                source_code="BDDK", started_at=finished - timedelta(seconds=20), finished_at=finished, status=status,
            ))
        session.add_all([
            HealthSample(captured_at=now - timedelta(hours=2), ai_queue_count=12, pending_ingest=3),
            HealthSample(captured_at=now - timedelta(hours=1), ai_queue_count=0, pending_ingest=0),
        ])
        session.commit()

        metrics = collect(session, settings, now)

    assert len(metrics["llm"]["series"]) == 24
    assert metrics["llm"]["series"][-1]["value"] == 1250
    assert metrics["llm"]["calls"] == 2 and metrics["llm"]["failed"] == 1
    assert metrics["llm"]["targetMs"] == 60_000

    assert len(metrics["scans"]["series"]) == 7
    assert metrics["scans"]["series"][-1]["value"] == 50
    assert metrics["scans"]["total"] == 2 and metrics["scans"]["failed"] == 1
    assert metrics["scans"]["successRate"] == 50

    assert len(metrics["aiQueue"]["series"]) == 14 * 24
    assert [point["value"] for point in metrics["aiQueue"]["series"][-2:]] == [12, 0]
    assert len(metrics["ingest"]["series"]) == 24
    assert [point["value"] for point in metrics["ingest"]["series"][-2:]] == [3, 0]
    assert metrics["aiQueue"]["series"][-3]["value"] is None


def test_record_health_sample_retains_two_weeks(settings, env, monkeypatch):  # noqa: F811
    import app.services.admin_metrics as metrics_service

    sf, _ = env
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(metrics_service, "_crawler_state", lambda _: ({}, 7))
    with sf() as session:
        session.add(HealthSample(
            captured_at=now - timedelta(days=15), ai_queue_count=99, pending_ingest=99,
        ))
        session.flush()
        record_health_sample(session, settings, {"preparing": 5}, now)
        session.commit()
        samples = session.scalars(select(HealthSample).order_by(HealthSample.captured_at)).all()

    assert len(samples) == 1
    assert samples[0].ai_queue_count == 5 and samples[0].pending_ingest == 7
    assert samples[0].captured_at.replace(tzinfo=timezone.utc) == now
