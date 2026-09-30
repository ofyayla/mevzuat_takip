"""OCR: kurumdaki Azure Document Intelligence "Read" (prebuilt-read) servisi.

REST akışı (v4.0 GA, api-version=2024-11-30):
  POST {endpoint}/documentintelligence/documentModels/prebuilt-read:analyze?api-version=…&pages=2,5-7&locale=tr-TR
       gövde: {"base64Source": "..."}                    → 202, başlıkta Operation-Location
  GET  Operation-Location                                → {"status": "running" | "succeeded" | "failed", ...}
  Sonuç: analyzeResult.content (tüm metin), pages[].pageNumber, pages[].spans[{offset,length}],
         pages[].words[].confidence

Yalnızca metin katmanı olmayan sayfalar gönderilir (``pages`` parametresi); böylece servis yükü, taranmış
sayfa sayısıyla sınırlı kalır. Uç nokta yolu ve API sürümü ayarlanabilir (v3 kurulumu: ``formrecognizer``).
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.settings import Settings

log = logging.getLogger(__name__)


class OcrError(Exception):
    """OCR servisi hatası (geçici olabilir; belge EXTRACT_FAILED olur ve yeniden denenir)."""


@dataclass
class OcrResult:
    pages: dict[int, str] = field(default_factory=dict)   # sayfa no (1'den) → metin
    confidence: float | None = None                        # kelime güvenlerinin ortalaması (0–1)
    word_count: int = 0


class OcrEngine(Protocol):
    name: str

    def analyze(self, content: bytes, content_type: str, pages: list[int] | None = None) -> OcrResult: ...


def page_ranges(pages: list[int]) -> str:
    """[1,2,3,7,9,10] → "1-3,7,9-10" (servisin ``pages`` parametresi)."""
    out: list[str] = []
    nums = sorted(set(pages))
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(out)


def parse_analyze_result(data: dict) -> OcrResult:
    ar = data.get("analyzeResult") or {}
    content = ar.get("content") or ""
    res = OcrResult()
    confs: list[float] = []
    for page in ar.get("pages") or []:
        n = page.get("pageNumber")
        spans = page.get("spans") or []
        if spans:
            text = "".join(content[s["offset"]:s["offset"] + s["length"]] for s in spans)
        else:  # bazı sürümlerde sayfa span'ı yok: kelimelerden kurulur
            text = " ".join(w.get("content", "") for w in page.get("words") or [])
        res.pages[n] = text
        confs.extend(w["confidence"] for w in page.get("words") or [] if w.get("confidence") is not None)
    res.word_count = len(confs)
    res.confidence = round(sum(confs) / len(confs), 4) if confs else None
    return res


class AzureDocumentIntelligenceOcr:
    name = "azure_di"

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None,
                 sleep=time.sleep, clock=time.monotonic):
        if not settings.ocr_endpoint:
            raise ValueError("OCR_PROVIDER=azure_di için OCR_ENDPOINT gerekli")
        self.s = settings
        headers = {"Ocp-Apim-Subscription-Key": settings.ocr_api_key} if settings.ocr_api_key else {}
        verify: object = settings.ocr_verify_tls
        if verify and isinstance(ca := settings.resolved_ca_bundle(), str):
            import ssl

            verify = ssl.create_default_context(cafile=ca)
        # Kurum içi servis: ortamdaki HTTPS_PROXY'ye gitmesin (trust_env=False)
        self.client = client or httpx.Client(headers=headers, verify=verify, trust_env=False,
                                             timeout=httpx.Timeout(60.0, connect=10.0))
        self._sleep, self._clock = sleep, clock

    @property
    def analyze_url(self) -> str:
        return (f"{self.s.ocr_endpoint.rstrip('/')}/{self.s.ocr_path_prefix.strip('/')}/documentModels/"
                f"{self.s.ocr_model}:analyze")

    def analyze(self, content: bytes, content_type: str, pages: list[int] | None = None) -> OcrResult:
        params = {"api-version": self.s.ocr_api_version}
        if pages:
            params["pages"] = page_ranges(pages)
        if self.s.ocr_locale:
            params["locale"] = self.s.ocr_locale
        body = {"base64Source": base64.b64encode(content).decode("ascii")}
        try:
            r = self.client.post(self.analyze_url, params=params, json=body)
        except httpx.HTTPError as e:
            raise OcrError(f"OCR servisine bağlanılamadı: {type(e).__name__}: {e}") from e
        if r.status_code != 202:
            raise OcrError(f"OCR analyze HTTP {r.status_code}: {r.text[:300]}")
        op_url = r.headers.get("operation-location")
        if not op_url:
            raise OcrError("OCR yanıtında Operation-Location yok")
        deadline = self._clock() + self.s.ocr_timeout_s
        delay = float(r.headers.get("retry-after", 1) or 1)
        while True:
            self._sleep(min(max(delay, 0.5), 5.0))
            try:
                pr = self.client.get(op_url)
            except httpx.HTTPError as e:
                raise OcrError(f"OCR sonucu alınamadı: {e}") from e
            if pr.status_code != 200:
                raise OcrError(f"OCR sonuç HTTP {pr.status_code}: {pr.text[:300]}")
            data = pr.json()
            status = (data.get("status") or "").lower()
            if status == "succeeded":
                return parse_analyze_result(data)
            if status in ("failed", "canceled"):
                raise OcrError(f"OCR işlemi başarısız: {data.get('error')}")
            if self._clock() > deadline:
                raise OcrError(f"OCR zaman aşımı ({self.s.ocr_timeout_s:.0f} sn)")
            delay = float(pr.headers.get("retry-after", delay) or delay)


def get_ocr_engine(settings: Settings) -> OcrEngine | None:
    if settings.ocr_provider in (None, "", "none"):
        return None
    if settings.ocr_provider == "azure_di":
        return AzureDocumentIntelligenceOcr(settings)
    raise ValueError(f"Bilinmeyen OCR_PROVIDER: {settings.ocr_provider}")
