import pytest

from app.collectors.config import SourceConfig
from app.collectors.http import (
    Fetcher,
    FetchError,
    HostNotAllowed,
    Recorder,
    ReplayFetcher,
    Response,
    json_body,
    request_key,
)
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


def test_request_key():
    assert request_key("GET", "https://www.ornek.gov.tr/a?x=1") == "https://www.ornek.gov.tr/a?x=1"  # eski fixture'lar
    a = request_key("POST", "https://www.ornek.gov.tr/api", json_body({"b": 1, "a": [1, "ş"]}))
    assert a == request_key("POST", "https://www.ornek.gov.tr/api", json_body({"a": [1, "ş"], "b": 1}))  # kanonik
    assert a.startswith("POST https://www.ornek.gov.tr/api #")
    assert a != request_key("POST", "https://www.ornek.gov.tr/api", json_body({"a": [1, "ş"], "b": 2}))


def test_post_json_roundtrip(settings, tmp_path):
    url = "https://www.ornek.gov.tr/api/list?page=0"
    body = {"status": 2, "kanunId": 434}
    f = DictFetcher(SRC, settings, {request_key("POST", url, json_body(body)): ('{"ok": 1}', "application/json")})
    f.recorder = Recorder(tmp_path / "fx")
    assert f.post_json(url, body).json() == {"ok": 1}
    with pytest.raises(FetchError):            # farklı gövde → farklı kayıt
        f.post_json(url, {"status": 2, "kanunId": 433})
    with pytest.raises(HostNotAllowed):
        f.post_json("https://kotu.example.com/api", body)
    replay = ReplayFetcher(SRC, settings, tmp_path / "fx")
    assert replay.post_json(url, {"kanunId": 434, "status": 2}).json() == {"ok": 1}


def test_post_json_sends_body_and_retries(settings, monkeypatch):
    from tenacity import wait_none

    calls = []

    class Backend:
        def request(self, method, url, headers, timeout, body=None, max_bytes=None, deadline_s=None):
            calls.append((method, headers.get("Content-Type"), body))
            status = 503 if len(calls) == 1 else 200
            return Response(url, url, status, {"content-type": "application/json"}, b"[]")

    f = Fetcher(SRC, settings, sleep=lambda _: None)
    f._backend = Backend()
    monkeypatch.setattr(Fetcher._request_with_retry.retry, "wait", wait_none())   # tenacity beklemesini atla
    assert f.post_json("https://www.ornek.gov.tr/api", {"a": 1}).json() == []
    assert calls == [("POST", "application/json", b'{"a":1}')] * 2   # 503 → yeniden deneme, aynı gövde


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


# ------------------------------------------------------------------------------------------------ gerçek istemciler
# Yerel bir HTTP sunucusuna karşı: hem httpx hem curl_cffi arka ucu aynı sözleşmeyi sağlamalı.


@pytest.fixture(scope="module")
def local_server():
    import gzip
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/ok":
                body = "merhaba".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
            elif self.path == "/big":      # Content-Length başlığı sınırı aşıyor
                body = b"x" * 4096
                self.send_response(200)
            elif self.path == "/bomb":     # sıkıştırılmış küçük, açılmış büyük
                body = gzip.compress(b"\0" * (4 << 20))
                self.send_response(200)
                self.send_header("Content-Encoding", "gzip")
            elif self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", "/ok")
                body = b""
            elif self.path == "/loop":
                self.send_response(302)
                self.send_header("Location", "/loop")
                body = b""
            else:
                body = b"yok"
                self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _backends():
    from app.collectors import http as h

    return [("httpx", lambda: h._HttpxBackend(None, True)), ("curl", lambda: h._ImpersonateBackend(None, True))]


@pytest.mark.parametrize("name", ["httpx", "curl"])
def test_backend_reads_follows_redirect_and_maps_errors(local_server, name):
    from app.collectors.http import FetchError

    backend = dict(_backends())[name]()
    try:
        r = backend.request("GET", f"{local_server}/ok", {}, 5)
        assert (r.status, r.content, r.final_url) == (200, b"merhaba", f"{local_server}/ok")
        assert backend.request("GET", f"{local_server}/redir", {}, 5).final_url.endswith("/ok")
        # 4xx yanıtı hata değil, Fetcher karar verir
        assert backend.request("GET", f"{local_server}/yok", {}, 5).status == 404
        with pytest.raises(FetchError):                                         # sonsuz yönlendirme döngüsü sızmaz
            backend.request("GET", f"{local_server}/loop", {}, 5)
    finally:
        backend.close()


@pytest.mark.parametrize("name", ["httpx", "curl"])
def test_backend_rejects_oversized_bodies_before_loading_them(local_server, name):
    from app.collectors.http import FetchError

    backend = dict(_backends())[name]()
    try:
        with pytest.raises(FetchError, match="çok büyük"):                    # Content-Length ile erken ret
            backend.request("GET", f"{local_server}/big", {}, 5, max_bytes=1024)
        with pytest.raises(FetchError, match="çok büyük"):                    # açılmış boyutla ret (gzip bombası)
            backend.request("GET", f"{local_server}/bomb", {}, 5, max_bytes=1 << 20)
        assert len(backend.request("GET", f"{local_server}/big", {}, 5, max_bytes=1 << 20).content) == 4096
    finally:
        backend.close()


def test_read_capped_enforces_total_deadline():
    from app.collectors.http import TransientFetchError, _read_capped

    def slow():
        import time

        for _ in range(5):
            time.sleep(0.05)
            yield b"x"

    with pytest.raises(TransientFetchError, match="toplam"):
        _read_capped("u", slow(), None, 0.1)
    assert _read_capped("u", iter([b"a", b"b"]), 10, 5) == b"ab"


def test_httpx_protocol_errors_become_fetch_errors(monkeypatch):
    """Sarmalanmayan httpx hataları taramayı kesiyordu (RemoteProtocolError, TooManyRedirects)."""
    import httpx

    from app.collectors.http import FetchError, TransientFetchError, _HttpxBackend

    backend = _HttpxBackend(None, True)
    try:
        for exc, expected in ((httpx.RemoteProtocolError("kesildi"), TransientFetchError),
                              (httpx.TooManyRedirects("döngü"), FetchError), (httpx.DecodingError("bozuk"), FetchError)):
            def boom(*a, _exc=exc, **k):
                raise _exc

            monkeypatch.setattr(backend.client, "stream", boom)
            with pytest.raises(expected) as e:
                backend.request("GET", "http://x.test/", {}, 1)
            assert type(e.value) is expected
    finally:
        backend.close()


def test_redirect_outside_allowlist_is_rejected(settings):
    f = DictFetcher(SRC, settings, {"https://www.ornek.gov.tr/a": ("ok", "text/html", 200, "https://kotu.example.com/x"),
                                    "https://www.ornek.gov.tr/b": ("ok", "text/html", 200, "https://x.alt.gov.tr/y")})
    with pytest.raises(HostNotAllowed, match="yönlendirme"):
        f.get("https://www.ornek.gov.tr/a")
    assert f.get("https://www.ornek.gov.tr/b").final_url == "https://x.alt.gov.tr/y"      # izinli host'a yönlendirme serbest
    relaxed = DictFetcher(SRC, settings.model_copy(update={"collect_enforce_redirect_hosts": False}),
                          {"https://www.ornek.gov.tr/a": ("ok", "text/html", 200, "https://kotu.example.com/x")})
    assert relaxed.get("https://www.ornek.gov.tr/a").status == 200                      # yalnızca log
