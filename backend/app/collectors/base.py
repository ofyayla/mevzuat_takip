"""Toplama katmanının ortak veri tipleri."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from app.collectors.config import ChannelConfig, SourceConfig
from app.collectors.http import Fetcher


@dataclass
class ItemRef:
    """Bir kaynağın liste/indeks sayfasında görülen tek bir öğe."""

    source: str
    channel: str
    external_id: str                 # kaynak içinde kararlı kimlik (URL'den/ID'den türetilir)
    url: str
    title: str
    published_at: date | None = None
    category: str | None = None
    summary: str | None = None
    version_key: str | None = None   # değişince içerik yeniden indirilir (ör. WP 'modified', TCMB CACHEID)
    inline_content: bytes | None = None   # API/feed içeriği zaten getirdiyse detay isteği atılmaz
    inline_content_type: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def listing_hash(self) -> str:
        """Liste satırındaki görünür bilgilerin özeti — başlık/tarih/version değişikliğini yakalar."""
        raw = "|".join([self.title, str(self.published_at or ""), self.version_key or "", self.url])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass
class FetchedDocument:
    url: str
    final_url: str
    content: bytes
    content_type: str
    fetched_at: datetime
    role: str = "main"              # main | attachment | listing
    title: str | None = None


@dataclass
class ChannelResult:
    items: list[ItemRef]
    pages_fetched: int = 0
    signature: str = ""             # sayfa yapısı parmak izi (İK-7 yapı değişikliği uyarısı)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ChannelContext:
    source: SourceConfig
    channel: ChannelConfig
    fetcher: Fetcher
    now: datetime
    since: datetime | None = None   # son başarılı taramanın zamanı (artımlı API'ler için)


class Strategy(Protocol):
    """Bir kanalın öğe listesini çıkaran yöntem (RSS, CSS liste, link deseni, WordPress API, ...)."""

    name: str

    def list_items(self, ctx: ChannelContext) -> ChannelResult: ...
