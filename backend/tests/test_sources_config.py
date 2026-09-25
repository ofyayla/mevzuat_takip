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


def test_all_sources_verified(sources):
    # BDDK 25.09.2026'da Türkiye'den canlı doğrulandı (yurt dışı IP'lerinden erişilemiyor)
    assert all(s.verified for s in sources.sources)


def test_intermediate_certificates_valid():
    """config/certs altındaki ara sertifikalar okunabilir, CA ve süresi geçmemiş olmalı."""
    from datetime import datetime, timezone

    from cryptography import x509

    from app.settings import BACKEND_DIR

    files = sorted((BACKEND_DIR / "config" / "certs").glob("*.pem"))
    assert {f.name for f in files} >= {"globalsign-rsa-ov-ssl-ca-2018.pem", "geotrust-tls-rsa-ca-g1.pem"}
    for f in files:
        cert = x509.load_pem_x509_certificate(f.read_bytes())
        assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca, f.name
        assert cert.not_valid_after_utc > datetime.now(timezone.utc), f"{f.name} süresi dolmuş: ca-fetch çalıştırın"
