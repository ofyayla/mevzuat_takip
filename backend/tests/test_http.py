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
