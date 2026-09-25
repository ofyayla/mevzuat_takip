"""config/sources.yaml tutarlılık testleri."""
import re

from app.collectors.strategies import STRATEGIES

SCOPE_SOURCES = {"RESMI_GAZETE", "BDDK", "SPK", "TCMB", "KVKK", "MASAK", "TICARET", "REKABET", "TKBB"}


def test_scope_sources_defined(sources):
    assert {s.code for s in sources.sources} == SCOPE_SOURCES  # Kapsam Formu v3, KEP ayrı adaptör


def test_channels_valid(sources):
    for s in sources.sources:
        assert s.channels, s.code
        for ch in s.channels:
            assert ch.strategy in STRATEGIES, f"{s.code}/{ch.name}"
            for key in ("href_pattern", "exclude_pattern", "id_pattern", "version_pattern", "date_regex"):
                if ch.params.get(key):
                    re.compile(ch.params[key])
            if ch.attachment_pattern:
                re.compile(ch.attachment_pattern)
            for url in ch.params.get("urls", []):
                assert s.host_allowed(url.format(year=2026)), f"{s.code}/{ch.name}: {url}"


def test_schedules_are_valid_cron(sources):
    from app.worker import _crontab

    for s in sources.sources:
        assert s.schedule, s.code
        for expr in s.schedule:
            _crontab(expr)


def test_beat_schedule_built():
    from app.worker import build_beat_schedule

    schedule = build_beat_schedule()
    assert "collect-RESMI_GAZETE-0" in schedule
    assert all(v["task"] == "app.tasks.collect.collect_source" for v in schedule.values())


def test_unverified_sources_are_flagged(sources):
    # BDDK geliştirme ortamından erişilemedi; kurum ağında probe ile doğrulanana kadar işaretli kalmalı
    assert sources.get("BDDK").verified is None
    assert all(s.verified for s in sources.sources if s.code != "BDDK")
