import pytest

from app.collectors.config import SourceConfig
from app.collectors.http import HostNotAllowed, Recorder, ReplayFetcher, Response
from tests.conftest import DictFetcher

SRC = SourceConfig.model_validate({
    "code": "X", "name": "X", "base_url": "https://www.ornek.gov.tr", "allowed_hosts": ["*.alt.gov.tr"],
    "channels": [{"name": "c", "strategy": "feed", "params": {"urls": []}}]})


def test_host_allowlist(settings):
    f = DictFetcher(SRC, settings, {"https://www.ornek.gov.tr/a": ("ok", "text/html"),
                                    "https://x.alt.gov.tr/b": ("ok", "text/html")})
    assert f.get("https://www.ornek.gov.tr/a").status == 200
    assert f.get("https://x.alt.gov.tr/b").status == 200   # joker
    with pytest.raises(HostNotAllowed):
        f.get("https://kotu.example.com/")


def test_windows_1254_decoding():
    body = "<meta charset=Windows-1254>Yönetmelik Değişikliği".encode("windows-1254")
    r = Response("u", "u", 200, {"content-type": "text/html"}, body)
    assert "Yönetmelik Değişikliği" in r.text


def test_recorder_replay_roundtrip(settings, tmp_path):
    rec = Recorder(tmp_path / "fx")
    f = DictFetcher(SRC, settings, {"https://www.ornek.gov.tr/a?x=1": ("<p>merhaba</p>", "text/html; charset=utf-8")})
    f.recorder = rec
    f.get("https://www.ornek.gov.tr/a", params={"x": 1})
    replay = ReplayFetcher(SRC, settings, tmp_path / "fx")
    assert replay.get("https://www.ornek.gov.tr/a", params={"x": 1}).text == "<p>merhaba</p>"


def test_build_ca_bundle_appends_intermediates(tmp_path):
    import certifi

    from app.collectors.http import build_ca_bundle
    from app.collectors.tls import is_chain_error

    extra = tmp_path / "ara.pem"
    extra.write_text("# yorum\n-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
    out = build_ca_bundle(True, [extra], tmp_path / "ca")
    data = open(out, "rb").read()
    assert data.startswith(open(certifi.where(), "rb").read().rstrip())   # temel depo korunur
    assert data.rstrip().endswith(b"-----END CERTIFICATE-----")
    assert build_ca_bundle(True, [extra], tmp_path / "ca") == out          # aynı girdi → aynı dosya
    base = tmp_path / "kurum.pem"
    base.write_text("KURUM\n")
    assert open(build_ca_bundle(str(base), [extra], tmp_path / "ca"), "rb").read().startswith(b"KURUM\n")
    assert is_chain_error("curl: (60) SSL certificate OpenSSL verify result: unable to get local issuer certificate")


def test_fetcher_uses_extra_ca_dir(settings, sources, tmp_path, monkeypatch):
    import shutil

    from app.collectors import http as http_mod
    from app.settings import BACKEND_DIR

    certs = tmp_path / "certs"
    certs.mkdir()
    shutil.copy(BACKEND_DIR / "config" / "certs" / "globalsign-rsa-ov-ssl-ca-2018.pem", certs)
    captured = {}
    monkeypatch.setattr(http_mod, "_HttpxBackend", lambda proxy, verify: captured.setdefault("verify", verify))
    s = settings.model_copy(update={"extra_ca_dir": certs})
    http_mod.Fetcher(sources.get("TCMB"), s)._get_backend()
    bundle = open(captured["verify"], "rb").read()
    assert b"GlobalSign RSA OV SSL CA 2018" in bundle and bundle.count(b"BEGIN CERTIFICATE") > 100
