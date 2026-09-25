"""Kaynak sitelerine HTTP erişim katmanı.

- Üç istemci türü: ``httpx`` (varsayılan), ``impersonate`` (curl_cffi ile tarayıcı TLS parmak izi — Resmî Gazete
  ve Ticaret Bakanlığı tarayıcı olmayan istemcileri TLS imzasından tanıyıp engelliyor) ve ``browser``
  (Playwright/Chromium; yalnızca JS ile render edilen ve API'si bulunmayan sayfalar için son çare).
- Kurum proxy'si (HTTPS_PROXY) ve CA paketi ayarlardan alınır.
- Host izin listesi (sources.yaml → allowed_hosts) uygulama seviyesinde zorlanır.
- Host başına istek aralığı (kibar tarama) ve geçici hatalarda üstel geri çekilmeli yeniden deneme.
- ``Recorder`` ile tüm yanıtlar diske yazılabilir; ``ReplayFetcher`` bu kayıtları testlerde ağ olmadan oynatır.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlencode, urlparse

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from app.collectors.config import SourceConfig
    from app.settings import Settings

log = logging.getLogger(__name__)

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", re.I)
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class FetchError(Exception):
    def __init__(self, url: str, message: str, status: int | None = None):
        super().__init__(f"{url}: {message}")
        self.url, self.status = url, status


class HostNotAllowed(FetchError):
    pass


class TransientFetchError(FetchError):
    """Yeniden denenebilir hata (bağlantı, zaman aşımı, 429/5xx)."""


@dataclass
class Response:
    url: str
    final_url: str
    status: int
    headers: dict[str, str]
    content: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "application/octet-stream").split(";")[0].strip().lower()

    @property
    def encoding(self) -> str:
        ct = self.headers.get("content-type", "")
        if m := re.search(r"charset=([\w-]+)", ct, re.I):
            return m[1]
        if m := _META_CHARSET.search(self.content[:4096]):
            return m[1].decode("ascii", "ignore")
        return "utf-8"

    @property
    def text(self) -> str:
        enc = self.encoding
        try:
            return self.content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            try:
                return self.content.decode("utf-8")
            except UnicodeDecodeError:
                return self.content.decode("windows-1254", errors="replace")  # eski Türkçe sayfalar

    def json(self) -> Any:
        return json.loads(self.content)


def build_url(url: str, params: dict[str, Any] | None) -> str:
    if not params:
        return url
    return url + ("&" if "?" in url else "?") + urlencode(params)


# --------------------------------------------------------------------------------------------- backends

class _Backend:
    def get(self, url: str, headers: dict[str, str], timeout: float) -> Response:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        pass


class _HttpxBackend(_Backend):
    def __init__(self, proxy: str | None, verify: str | bool):
        import httpx

        self._httpx = httpx
        self.client = httpx.Client(proxy=proxy, verify=verify, follow_redirects=True, trust_env=proxy is None)

    def get(self, url, headers, timeout):
        try:
            r = self.client.get(url, headers=headers, timeout=timeout)
        except (self._httpx.TimeoutException, self._httpx.NetworkError, self._httpx.ProxyError) as e:
            raise TransientFetchError(url, f"{type(e).__name__}: {e}") from e
        return Response(url, str(r.url), r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content)

    def close(self):
        self.client.close()


class _ImpersonateBackend(_Backend):
    def __init__(self, proxy: str | None, verify: str | bool, profile: str = "chrome"):
        from curl_cffi import requests as cffi_requests

        self._errors = (cffi_requests.errors.RequestsError,)
        self.session = cffi_requests.Session(impersonate=profile, verify=verify,
                                             proxies={"https": proxy, "http": proxy} if proxy else None)

    def get(self, url, headers, timeout):
        # curl_cffi tarayıcı başlıklarını kendisi üretir; yalnızca UA dışındakileri geçiriyoruz ki parmak izi bozulmasın
        extra = {k: v for k, v in headers.items() if k.lower() != "user-agent"}
        try:
            r = self.session.get(url, headers=extra, timeout=timeout, allow_redirects=True)
        except self._errors as e:
            raise TransientFetchError(url, f"{type(e).__name__}: {e}") from e
        return Response(url, str(r.url), r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content)

    def close(self):
        self.session.close()


class _BrowserBackend(_Backend):
    """Playwright ile render edilmiş HTML. Yalnızca 'browser' istemcisi seçilen kaynaklarda yüklenir."""

    def __init__(self, proxy: str | None):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        launch: dict[str, Any] = {"headless": True}
        if proxy:
            launch["proxy"] = {"server": proxy}
        self.browser = self._pw.chromium.launch(**launch)

    def get(self, url, headers, timeout):
        page = self.browser.new_page()
        try:
            resp = page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            html = page.content().encode("utf-8")
            status = resp.status if resp else 0
            return Response(url, page.url, status, {"content-type": "text/html; charset=utf-8"}, html)
        except Exception as e:  # noqa: BLE001 — Playwright hata tipleri çok çeşitli
            raise TransientFetchError(url, f"{type(e).__name__}: {e}") from e
        finally:
            page.close()

    def close(self):
        self.browser.close()
        self._pw.stop()


# --------------------------------------------------------------------------------------------- recorder

@dataclass
class Recorder:
    """Yanıtları <dir>/<kaynak>/ altına kaydeder; manifest.json URL → dosya eşlemesini tutar (fixture üretimi)."""

    directory: Path
    manifest: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        mf = self.directory / "manifest.json"
        if mf.exists():
            self.manifest = json.loads(mf.read_text(encoding="utf-8"))

    def save(self, resp: Response) -> None:
        ext = {"text/html": ".html", "application/pdf": ".pdf", "application/json": ".json",
               "application/xml": ".xml", "text/xml": ".xml", "application/atom+xml": ".xml",
               "application/rss+xml": ".xml"}.get(resp.content_type, ".bin")
        name = hashlib.sha1(resp.url.encode()).hexdigest()[:16] + ext
        (self.directory / name).write_bytes(resp.content)
        self.manifest[resp.url] = {"file": name, "status": resp.status, "final_url": resp.final_url,
                                   "content_type": resp.headers.get("content-type", "")}
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest, ensure_ascii=False, indent=1),
                                                      encoding="utf-8")


# --------------------------------------------------------------------------------------------- fetcher

class Fetcher:
    """Bir kaynak için yapılandırılmış HTTP istemcisi."""

    def __init__(self, source: "SourceConfig", settings: "Settings", *, recorder: Recorder | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.source = source
        self.settings = settings
        self.recorder = recorder
        self.delay = source.request_delay_s if source.request_delay_s is not None else settings.collect_request_delay_s
        self.timeout = settings.collect_timeout_s
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()
        self._backend: _Backend | None = None
        self.request_count = 0

    def _get_backend(self) -> _Backend:
        if self._backend is None:
            proxy, verify = self.settings.resolved_proxy(), self.settings.resolved_ca_bundle()
            kind = self.source.client
            if kind == "impersonate":
                self._backend = _ImpersonateBackend(proxy, verify)
            elif kind == "browser":
                self._backend = _BrowserBackend(proxy)
            else:
                self._backend = _HttpxBackend(proxy if self.settings.https_proxy else None, verify)
        return self._backend

    def _throttle(self, host: str) -> None:
        with self._lock:
            last = self._last_request.get(host)
            if last is not None:
                wait = self.delay - (time.monotonic() - last)
                if wait > 0:
                    self._sleep(wait)
            self._last_request[host] = time.monotonic()

    def get(self, url: str, *, params: dict[str, Any] | None = None, accept: str | None = None,
            allow_status: tuple[int, ...] = ()) -> Response:
        url = build_url(url, params)
        if not self.source.host_allowed(url):
            raise HostNotAllowed(url, f"host izin listesinde değil ({self.source.code}.allowed_hosts)")
        headers = {"User-Agent": self.settings.http_user_agent, "Accept-Language": "tr-TR,tr;q=0.9"}
        if accept:
            headers["Accept"] = accept
        resp = self._get_with_retry(url, headers)
        if self.recorder:
            self.recorder.save(resp)
        if resp.status >= 400 and resp.status not in allow_status:
            raise FetchError(url, f"HTTP {resp.status}", resp.status)
        if len(resp.content) > self.settings.collect_max_bytes:
            raise FetchError(url, f"yanıt çok büyük ({len(resp.content)} bayt)")
        return resp

    @retry(retry=retry_if_exception(lambda e: isinstance(e, TransientFetchError)),
           stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30), reraise=True)
    def _get_with_retry(self, url: str, headers: dict[str, str]) -> Response:
        self._throttle(urlparse(url).hostname or "")
        self.request_count += 1
        log.debug("GET %s", url)
        resp = self._get_backend().get(url, headers, self.timeout)
        if resp.status in _RETRY_STATUSES:
            raise TransientFetchError(url, f"HTTP {resp.status}", resp.status)
        return resp

    def close(self) -> None:
        if self._backend:
            self._backend.close()
            self._backend = None


class ReplayFetcher(Fetcher):
    """Recorder ile kaydedilmiş yanıtları ağa çıkmadan döndürür (testler için)."""

    def __init__(self, source: "SourceConfig", settings: "Settings", directory: Path):
        super().__init__(source, settings, sleep=lambda _: None)
        self.directory = directory
        self.manifest: dict[str, dict[str, Any]] = json.loads((directory / "manifest.json").read_text("utf-8"))

    def _get_with_retry(self, url: str, headers: dict[str, str]) -> Response:
        entry = self.manifest.get(url)
        if entry is None:
            raise FetchError(url, "kayıtlı yanıt yok (fixture eksik)", 404)
        self.request_count += 1
        return Response(url, entry["final_url"], entry["status"], {"content-type": entry["content_type"]},
                        (self.directory / entry["file"]).read_bytes())
