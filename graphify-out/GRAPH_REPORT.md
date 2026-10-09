# Graph Report - .  (2026-10-07)

## Corpus Check
- 192 files · ~328,542 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1140 nodes · 2752 edges · 52 communities (41 shown, 11 thin omitted)
- Extraction: 59% EXTRACTED · 41% INFERRED · 0% AMBIGUOUS · INFERRED: 1120 edges (avg confidence: 0.67)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 46|Community 46]]

## God Nodes (most connected - your core abstractions)
1. `Settings` - 76 edges
2. `RawDocument` - 47 edges
3. `get_settings()` - 43 edges
4. `Regulation` - 38 edges
5. `DocumentProcessor` - 38 edges
6. `ApiError` - 36 edges
7. `SourceConfig` - 34 edges
8. `FakeLLM` - 30 edges
9. `make_sessionmaker()` - 29 edges
10. `SourceCollector` - 29 edges

## Surprising Connections (you probably didn't know these)
- `api_reference()` --calls--> `make_sessionmaker()`  [INFERRED]
  kurum/export.py → backend/app/db.py
- `cmd_healthcheck()` --calls--> `get_settings()`  [INFERRED]
  backend/app/crawler/cli.py → backend/app/settings.py
- `api_reference()` --calls--> `create_app()`  [INFERRED]
  kurum/export.py → backend/app/api/main.py
- `test_host_allowlist()` --calls--> `DictFetcher`  [INFERRED]
  backend/tests/test_http.py → backend/tests/conftest.py
- `StoredDoc` --uses--> `Source`  [INFERRED]
  backend/app/collectors/store.py → backend/app/db.py

## Communities (52 total, 11 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.0
Nodes (82): ClassifyReport, _Ping, effective_settings(), Knowledge, _load_knowledge(), messages(), YZ adımlarının ortak parçaları: prompt şablonları, taksonomi/kılavuz, düzenleme, ``app_setting`` tablosundaki çalışma zamanı değerleriyle ezilmiş ayarlar. (+74 more)

### Community 1 - "Community 1"
Cohesion: 0.0
Nodes (76): classify_pending(), ``include_baseline``: ilk taramadaki eski kayıtları da geriye dönük sınıflandırı, FakeLLM, Test ve geliştirme için: ``handler(task, messages, n) -> list[dict | str]``., summarize_pending(), active_units(), current_summary(), example_text() (+68 more)

### Community 2 - "Community 2"
Cohesion: 0.0
Nodes (63): SourcesFile, decode_html(), extract(), extract_docx(), extract_html(), extract_image(), extract_listing(), extract_pdf() (+55 more)

### Community 3 - "Community 3"
Cohesion: 0.0
Nodes (64): cmd_calls(), cmd_classify(), cmd_eval(), cmd_llm_check(), cmd_match(), cmd_setting(), cmd_show(), cmd_summarize() (+56 more)

### Community 4 - "Community 4"
Cohesion: 0.0
Nodes (53): classify_regulation(), İK-3 sınıflandırma hattı: NEW düzenleme → ilgililik (+ güven) → ilgiliyse önem d, Düzenlemenin bağlı belgelerinden LLM girdisi: en dolu ana metin (tercihen RG) +, regulation_context(), EffectiveDateCheck, find_effective_sentence(), _find_part(), _hit() (+45 more)

### Community 5 - "Community 5"
Cohesion: 0.0
Nodes (45): get_crawl_request(), FileSystemStorage, get_storage(), GridFsStorage, Ham içerik arşivi — içerik adresli (sha256). Aynı içerik iki kez yazılmaz.  -, merge_channel_stats(), Her kanal için en son kaydedilen istatistik (yalnızca bazı kanalların tarandığı, _date_to_bson() (+37 more)

### Community 6 - "Community 6"
Cohesion: 0.0
Nodes (49): boot(), cdnScriptFor(), collectProps(), compileAttr(), compileTemplate(), contentKey(), createComponentFactory(), createExternalModules() (+41 more)

### Community 7 - "Community 7"
Cohesion: 0.0
Nodes (37): acquire_lock(), claim_crawl_request(), _client(), crawl_db(), ensure_indexes(), finish_crawl_request(), last_heartbeat(), DMZ ↔ LAN teslim alanı: MongoDB (``mevzuat_crawl`` veritabanı).  DMZ'deki craw (+29 more)

### Community 8 - "Community 8"
Cohesion: 0.0
Nodes (43): _emit(), Alarm yaşam döngüsü (İK-7): bulgular → alert tablosu. Aynı ``key`` açıksa güncel, sync_alerts(), SyncResult, business_hours_between(), is_business_day(), Türkiye iş günü takvimi: hafta sonu, sabit resmî tatiller ve config/holidays.yam, İş günlerine düşen saatler (tam gün 24 saat sayılır; mesai saati değil, takvim g (+35 more)

### Community 9 - "Community 9"
Cohesion: 0.0
Nodes (24): _Backend, _BrowserBackend, build_ca_bundle(), build_url(), content_type(), encoding(), FetchError, _get_with_retry() (+16 more)

### Community 10 - "Community 10"
Cohesion: 0.0
Nodes (26): FetchRun, Source, _bson_date(), CrawlIngestor, _ids(), IngestReport, DMZ crawler çıktısının ana servise aktarımı (CRAWL_MODE=remote): MongoDB → Postg, aware() (+18 more)

### Community 11 - "Community 11"
Cohesion: 0.0
Nodes (33): Yetki hiyerarşisi: admin ⊇ expert ⊇ viewer., require(), admin_alerts(), admin_backfill(), admin_monitor_run(), admin_regenerate(), admin_reprocess(), admin_run_source() (+25 more)

### Community 12 - "Community 12"
Cohesion: 0.0
Nodes (23): ChannelContext, FetchedDocument, Toplama katmanının ortak veri tipleri., Bir kanalın öğe listesini çıkaran yöntem (RSS, CSS liste, link deseni, WordPress, Strategy, ChannelConfig, ExpectedBehavior, config/sources.yaml şeması ve yükleyicisi. (+15 more)

### Community 13 - "Community 13"
Cohesion: 0.0
Nodes (23): fetch_documents(), absolutize(), clean(), external_id(), link_title(), node_signature(), parse_html(), HTML stratejilerinin ortak yardımcıları. (+15 more)

### Community 14 - "Community 14"
Cohesion: 0.0
Nodes (25): set_runtime_setting(), BackfillIn, DecisionIn, ManualDetectionIn, ReprocessIn, SettingsIn, SplitIn, UnitsIn (+17 more)

### Community 15 - "Community 15"
Cohesion: 0.0
Nodes (23): create_app(), __getattr__(), text(), api_reference(), copy(), crawler_pyproject(), export_core(), export_crawler() (+15 more)

### Community 16 - "Community 16"
Cohesion: 0.0
Nodes (15): _Base, input_hash(), LLMResult, OpenAICompatibleLLM, LLM katmanı (plan §7): OpenAI (yerel geliştirme) ve vLLM/Qwen (kurum) için tek i, OpenAI ve vLLM (OpenAI uyumlu) — aynı SDK., Pydantic şemasını OpenAI strict JSON schema biçimine getirir (tüm alanlar zorunl, ``<think>…</think>`` bloğunu içerikten ayırır; kod çitlerini ve baştaki/sondaki (+7 more)

### Community 17 - "Community 17"
Cohesion: 0.0
Nodes (18): make_confirmer(), İK-2 tekilleştirmesinin belirsiz bandı (0.70–0.90) için LLM onayı (thinking kapa, ``DocumentProcessor(confirmer=...)`` için: True/False, LLM hatasında None (insan, cmd_merge(), cmd_ocr_check(), cmd_reextract(), cmd_review(), cmd_run() (+10 more)

### Community 18 - "Community 18"
Cohesion: 0.0
Nodes (10): ChannelResult, ItemRef, Bir kaynağın liste/indeks sayfasında görülen tek bir öğe., Liste satırındaki görünür bilgilerin özeti — başlık/tarih/version değişikliğini, CssListStrategy, CSS seçicili liste sayfası stratejisi.  params:   urls: [liste sayfaları; {ye, FeedStrategy, RSS/Atom beslemesi stratejisi (ör. TCMB Basın Duyuruları Atom beslemesi).  par (+2 more)

### Community 19 - "Community 19"
Cohesion: 0.0
Nodes (16): Recorder ile kaydedilmiş yanıtları ağa çıkmadan döndürür (testler için)., ReplayFetcher, Canlı sitelerden kaydedilmiş yanıtlar (tests/fixtures/<kaynak>/) üzerinde kanal, run_channel(), test_bddk(), test_bddk_documents(), test_bddk_duzenlemeler_mevzuat_gov_links(), test_channel_parses_recorded_page() (+8 more)

### Community 20 - "Community 20"
Cohesion: 0.0
Nodes (15): regulation_stats(), analysis_status(), _day_bounds_utc(), _filters(), _iso(), list_regulations(), _metric_cond(), _primary_url() (+7 more)

### Community 21 - "Community 21"
Cohesion: 0.0
Nodes (11): admin_audit_verify(), ensure_detected(), event_hash(), _iso(), Denetim izi olayları ve portal gösterimi (etiket + ikon; plan §5.2)., Saat dilimine bağımsız (DB oturumu +03:00 döndürse de aynı) hash girdisi., Kurcalama kontrolü: hash'i tutmayan ya da zinciri kopan olaylar (boş liste = büt, İlgili bulunan düzenleme için bir kez "YZ tarafından tespit edildi" olayı. (+3 more)

### Community 22 - "Community 22"
Cohesion: 0.0
Nodes (8): build_beat_schedule(), _crontab(), Celery uygulaması ve Beat zamanlaması (ana servis).  İki kurulum biçimi (``CRA, config/sources.yaml tutarlılık testleri., config/certs altındaki ara sertifikalar okunabilir, CA ve süresi geçmemiş olmalı, test_beat_schedule_built(), test_intermediate_certificates_valid(), test_schedules_are_valid_celery_crontab()

### Community 23 - "Community 23"
Cohesion: 0.0
Nodes (10): Outbox, Düzenleme → birim önerisi. ``origin``: ai | manual. Başkanlık değiştirirse eski, Domain olayları (record.approved / record.rejected). Faz 1'de dinleyen yok; Faz, UnitSuggestion, add_event(), ApiError, change_units(), _check_version() (+2 more)

### Community 24 - "Community 24"
Cohesion: 0.0
Nodes (8): _aia_issuer_urls(), fetch_intermediate(), IntermediateResult, leaf_certificate(), _load_any(), _load_store(), Eksik TLS zinciri tamamlama.  Bazı kamu siteleri (BDDK; Türkiye'den bağlanıldı, Sunucunun eksik ara sertifikasını AIA'dan indirir ve zinciri doğrular. Doğrulana

### Community 26 - "Community 26"
Cohesion: 0.0
Nodes (6): parse_iso_datetime(), _date(), _dig(), _fill(), JsonApiStrategy, Genel JSON liste API'si stratejisi (ör. SPK Mevzuat Sistemi: mevzuat.spk.gov.tr/

### Community 27 - "Community 27"
Cohesion: 0.0
Nodes (7): current_actor(), _load_keys(), public_keys(), Kimlik doğrulama (plan §8). Backend'in Keycloak'a ağ erişimi yok: token çevrimdı, roles_from_claims(), verify_token(), get_actor()

### Community 28 - "Community 28"
Cohesion: 0.0
Nodes (6): cmd_probe(), list_channel(), _normalize_external(), Bilinen dış belge sitelerine giden bağlantılara kanonik kimlik/adres verir; aynı, get_strategy(), Strateji kayıt defteri: sources.yaml'daki ``strategy`` adı → sınıf.

### Community 29 - "Community 29"
Cohesion: 0.0
Nodes (3): _mevzuat_gov(), Dış belge siteleri için bağlantı çözümleyiciler.  Kaynak listeleri çoğu zaman, ResolvedLink

## Knowledge Gaps
- **192 isolated node(s):** `Veritabanı modelleri: toplama (plan §5.1: source, fetch_run, raw_document) ve te`, `Arşivlenmiş ham içerik. Asla silinmez; içerik değişirse yeni sürüm eklenir.`, `Tekil düzenleme kaydı (portaldaki "kayıt"). Aynı düzenlemenin farklı kaynaklarda`, `Kesin eşleştirme anahtarları (ör. ``url:…``, ``karar:BDDK:11572``, ``title:BDDK:`, `İK-4 yapılandırılmış özet (plan §5.2). Sürümlüdür: yeniden üretimde eski sürüm i` (+187 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.