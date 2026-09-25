"""Uygulama konfigürasyonu — tek nokta. Değerler ortam değişkenlerinden veya backend/.env dosyasından okunur."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore",
                                      env_ignore_empty=True)

    app_env: str = "local"
    log_level: str = "INFO"
    timezone: str = "Europe/Istanbul"

    # Veritabanı / kuyruk
    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'mevzuat.db'}"
    redis_url: str | None = None

    # Depolama
    raw_storage_dir: Path = BACKEND_DIR / "data" / "raw"
    lock_dir: Path = BACKEND_DIR / "data" / "locks"

    # Toplama
    sources_file: Path = BACKEND_DIR / "config" / "sources.yaml"
    https_proxy: str | None = None           # boşsa ortamdaki HTTPS_PROXY kullanılır
    ca_bundle: str | None = None             # boşsa REQUESTS_CA_BUNDLE / SSL_CERT_FILE / sistem deposu
    # Zincirini eksik gönderen sunucular (BDDK, mevzuat.gov.tr, Resmî Gazete) için ara sertifikalar. Buradaki
    # *.pem dosyaları kök deposuna eklenir; güven yine kök sertifikaya dayanır. `mevzuat-collect ca-fetch` ile güncellenir.
    extra_ca_dir: Path = BACKEND_DIR / "config" / "certs"
    http_user_agent: str = "MevzuatTakipBot/1.0 (+Mevzuat ve Uyum Baskanligi)"
    collect_request_delay_s: float = 1.5
    collect_timeout_s: float = 30.0
    collect_max_attachments: int = 5
    collect_max_bytes: int = 50 * 1024 * 1024
    collect_default_lookback_days: int = 30

    def resolved_ca_bundle(self) -> str | bool:
        for candidate in (self.ca_bundle, os.environ.get("REQUESTS_CA_BUNDLE"), os.environ.get("SSL_CERT_FILE"),
                          os.environ.get("CURL_CA_BUNDLE")):
            if candidate and Path(candidate).exists():
                return candidate
        return True

    def resolved_proxy(self) -> str | None:
        return self.https_proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")


@lru_cache
def get_settings() -> Settings:
    return Settings()
