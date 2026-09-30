"""config/sources.yaml şeması ve yükleyicisi."""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, model_validator

ClientKind = Literal["httpx", "impersonate", "browser"]


class ChannelConfig(BaseModel):
    name: str
    strategy: str
    category: str | None = None
    enabled: bool = True
    fetch_detail: bool = True        # False: yalnızca liste satırı kaydedilir (ör. dış siteye giden bağlantılar)
    attachment_pattern: str | None = None  # detay sayfasındaki eklerin (PDF vb.) href deseni
    content_selector: str | None = None    # İK-2: detay sayfasında ana metnin CSS seçicisi (trafilatura yerine)
    # Liste satırı değişmese de içerik bu kadar gün sonra yeniden indirilip hash'le karşılaştırılır. Değişiklik
    # tarihi vermeyen ve metni yerinde güncellenen kaynaklar için (konsolide metinler: mevzuat.gov.tr, SPK mevzuat).
    refresh_after_days: int | None = None
    refresh_max_per_run: int = 20     # bir çalıştırmada en fazla bu kadar öğe yeniden kontrol edilir (yük yayma)
    params: dict[str, Any] = Field(default_factory=dict)


class ExpectedBehavior(BaseModel):
    silence_tolerance_hours: int = 72
    business_days_only: bool = True
    min_items_per_run: int = 1       # 200 dönen ama bu kadar öğe bulunamayan liste = yapı değişikliği şüphesi


class SourceConfig(BaseModel):
    code: str
    name: str
    base_url: str
    enabled: bool = True
    client: ClientKind = "httpx"
    request_delay_s: float | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    schedule: list[str] = Field(default_factory=list)   # cron ifadeleri (Europe/Istanbul)
    verified: str | None = None      # canlı sitede en son doğrulandığı tarih; None = doğrulanmadı
    notes: str | None = None
    expected: ExpectedBehavior = Field(default_factory=ExpectedBehavior)
    channels: list[ChannelConfig]

    @model_validator(mode="after")
    def _default_hosts(self) -> "SourceConfig":
        host = urlparse(self.base_url).hostname
        if host and host not in self.allowed_hosts:
            self.allowed_hosts.append(host)
        return self

    def host_allowed(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        return any(fnmatch.fnmatch(host, pattern) for pattern in self.allowed_hosts)

    def channel(self, name: str) -> ChannelConfig:
        for ch in self.channels:
            if ch.name == name:
                return ch
        raise KeyError(f"{self.code}: '{name}' adlı kanal yok")


class SourcesFile(BaseModel):
    sources: list[SourceConfig]

    def get(self, code: str) -> SourceConfig:
        for s in self.sources:
            if s.code.upper() == code.upper():
                return s
        raise KeyError(f"'{code}' adlı kaynak sources.yaml içinde yok")

    def enabled(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


def load_sources(path: Path) -> SourcesFile:
    with open(path, encoding="utf-8") as f:
        return SourcesFile.model_validate(yaml.safe_load(f))
