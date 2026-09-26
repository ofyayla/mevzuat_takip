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

    # İK-2: metin çıkarma / OCR (kurumdaki Azure Document Intelligence "Read" servisi)
    ocr_provider: str = "none"               # none | azure_di
    ocr_endpoint: str | None = None          # ör. https://docintel.kurum.local veya http://10.x.x.x:5000
    ocr_api_key: str | None = None           # Ocp-Apim-Subscription-Key (anahtar istemeyen kurulumda boş)
    ocr_api_version: str = "2024-11-30"      # v4.0 GA; v3 kurulumu için 2023-07-31 + ocr_path_prefix=formrecognizer
    ocr_path_prefix: str = "documentintelligence"
    ocr_model: str = "prebuilt-read"
    ocr_locale: str | None = "tr-TR"
    ocr_timeout_s: float = 180.0
    ocr_min_chars_per_page: int = 40         # metin katmanı bundan azsa sayfa taranmış kabul edilir
    ocr_baseline: bool = False               # ilk taramadaki eski belgeler de OCR'a gönderilsin mi (yük)
    ocr_verify_tls: bool = True

    # İK-2: tekilleştirme
    dedupe_auto_threshold: float = 0.90      # ≥ → otomatik birleştir
    dedupe_review_threshold: float = 0.70    # [0.70, 0.90) → onay bekliyor (LLM/insan)
    dedupe_date_window_days: int = 7
    process_max_attempts: int = 3

    # İK-3: LLM (plan §7). Yerelde OpenAI, kurumda vLLM (OpenAI uyumlu) — aynı istemci
    llm_provider: str = "none"               # none | openai | vllm
    llm_api_key: str | None = None
    llm_base_url: str | None = None          # vllm: http://10.144.100.204:8806/v1
    llm_model: str = "gpt-4.1-mini"          # vllm: Qwen/Qwen3.6-35B-A3B-FP8
    llm_timeout_s: float = 120.0
    llm_max_concurrency: int = 2
    llm_enable_thinking: bool = True         # yalnızca vllm (Qwen thinking)
    llm_thinking_tasks: str = "relevance,severity,summary,unit_match"
    llm_store_reasoning: bool = False
    llm_json_mode: str = "json_schema"       # json_schema | json_object (sunucu şema zorlamasını desteklemiyorsa)
    taxonomy_file: Path = BACKEND_DIR / "config" / "taxonomy.yaml"
    labeling_guide_file: Path = BACKEND_DIR / "config" / "labeling_guide.md"

    # İK-3: ilgililik / güven (başlangıç değerleri; çalışma zamanında app_setting tablosundan ezilir)
    relevance_threshold: float = 0.30
    relevance_samples: int = 3               # self-consistency örnek sayısı
    relevance_sample_temperature: float = 0.7
    relevance_weight_model: float = 0.6
    relevance_weight_votes: float = 0.3
    relevance_weight_rules: float = 0.1
    confidence_high: float = 0.75
    confidence_medium: float = 0.45
    relevance_max_input_chars: int = 18000   # ~6.000 token

    # İK-4: özet ve kaynağa dayandırma
    summary_max_input_chars: int = 36000     # tek çağrı sınırı; üstü map-reduce
    summary_chunk_chars: int = 14000         # map-reduce bölüm boyu
    grounding_fuzzy_threshold: int = 90      # rapidfuzz partial_ratio alt sınırı
    summary_max_regenerations: int = 1
    effective_soon_days: int = 30

    # İK-5: birim eşleştirme
    units_file: Path = BACKEND_DIR / "config" / "units.yaml"
    unit_min_score: float = 0.30
    unit_max_suggestions: int = 4
    unit_responsibility_threshold: int = 80   # matched_responsibility ↔ görev tanımı maddesi (rapidfuzz)
    unit_fewshot_count: int = 4

    # İK-6: Portal API ve kimlik doğrulama (plan §8)
    auth_mode: str = "disabled"               # disabled (demo) | keycloak
    demo_user_name: str = "Demo Kullanıcı"
    demo_user_title: str = "Uyum Uzmanı"
    keycloak_issuer: str | None = None
    keycloak_client_id: str = "mevzuat-portal"
    keycloak_public_keys_dir: Path = BACKEND_DIR / "config" / "keycloak_keys"
    keycloak_leeway_s: int = 60
    cors_origins: str = "http://localhost:8080"
    portal_dir: Path = BACKEND_DIR.parent     # Mevzuat Takip Portali.dc.html + support.js

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
