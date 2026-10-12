# 05 — Uygulamalı Çalışma ve İşletim

Okumak yetmez. Bu bölümdeki alıştırmalar kodu çalıştırarak öğrenmek içindir. Komutlar depo kökünden verilmiştir.
§1 ve §2 **ağ gerektirmez**.

## 1. Kurulum ve testler

```bash
# Crawler (ayrı sanal ortam önerilir)
cd mevzuat-crawler/app
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest respx && pip install --no-deps -e .
pytest -q                      # ~80 test; Mongo testleri Mongo yoksa atlanır

# Mongo testleri için (isteğe bağlı)
docker run -d --name mevzuat-mongo-test -p 27018:27017 mongo:7 && pytest -q tests/test_crawler_service.py

# Core
cd ../../mevzuat-core/app && python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[core,dev]" && pytest -q

# Depo kökünden hepsi + ortak dosya denetimi
make test
```

Crawler testlerinin haritası. Her birini okuyun, kodun **niyetini** en iyi testler anlatır:

| Test dosyası | Neyi kanıtlar |
|---|---|
| `test_runner.py` | Yeni/aynı/değişmiş döngüsü, dış host → liste anlık görüntüsü, bütçe/erteleme, boş liste → `structure_alert`, kanal hatası izolasyonu, `since`, **BASELINE**, `refresh_after_days`, mevzuat.gov.tr tam metin |
| `test_sources_replay.py` | 23 kanalın kayıtlı (25.09.2026) sayfalardan doğru ayrıştırılması: öğe sayısı, tekil kimlik, anlamlı başlık |
| `test_resmi_gazete.py` | Mükerrer takibi, İstanbul saatine göre gün sınırı |
| `test_http.py` | Host izin listesi, windows-1254, kayıt/oynatma, CA paketi |
| `test_crawler_service.py` | Mongo/GridFS yazımı, sürümler, talepler, kilit, canlılık, zamanlama (gerçek Mongo ister) |
| `test_sources_config.py` | 9 kaynağın tanımı, geçerli strateji ve cron, doğrulama tarihi, ara sertifikaların geçerliliği |
| `test_dates.py`, `test_resolvers.py` | Türkçe tarih biçimleri, mevzuat.gov.tr bağlantı kanonikleştirme |

## 2. Ağsız tarama denemeleri (fixture'larla)

`tests/fixtures/<kaynak>/` altında canlı sitelerden kaydedilmiş yanıtlar ve `manifest.json` var. `ReplayFetcher`
bunları ağ yerine oynatır.

**Alıştırma 2.1: Bir kanalı listeleyin.** `mevzuat-crawler/app` içinde:

```python
from datetime import datetime, timezone
from pathlib import Path
from app.collectors.config import load_sources
from app.collectors.http import ReplayFetcher
from app.collectors.runner import list_channel
from app.settings import Settings

settings = Settings()
src = load_sources(Path("config/sources.yaml")).get("TCMB")
fetcher = ReplayFetcher(src, settings, Path("tests/fixtures/tcmb"))
res = list_channel(src, src.channel("basin_duyurulari"), fetcher,
                   now=datetime(2026, 9, 25, 12, tzinfo=timezone.utc))
print(len(res.items), "öğe, imza:", res.signature)
for it in res.items[:3]:
    print(it.published_at, it.external_id, it.title[:60], it.listing_hash()[:10])
```

Beklenen çıktı (doğrulandı): `20 öğe, imza: f327c33cf86609d4` ve ilk satır
`2026-09-17 2026/duy2026-42 Para Politikası Kurulu Toplantı Özeti (2026-42) …`.

Kaynağı ve kanalı değiştirerek tekrarlayın: `("BDDK", "kurul_kararlari")` (`extra`'daki `karar_sayisi`'ye bakın),
`("RESMI_GAZETE", "fihrist")` (`category`, `extra.section`, ilan öğeleri), `("SPK", "mevzuat")` (json_api şablonları).

**Alıştırma 2.2: Değişiklik tespitini gözlemleyin.** `tests/test_runner.py::test_new_unchanged_changed_cycle` ve
`test_first_run_is_baseline_until_backlog_done` testlerini `pytest -k … -s` ile çalıştırın. Testteki sözlük
tabanlı sahte siteyi (`DictFetcher`) değiştirerek şunları deneyin: başlığı değiştirin (listing_hash), içeriği
değiştirin (sha256), ikisini birden değiştirin. Sayaçları (`new/changed/unchanged/deferred`) tahmin edin, sonra
doğrulayın.

**Alıştırma 2.3: Yapı değişikliğini taklit edin.** Bir fixture HTML'inde öğe kapsayıcısının sınıf adını
değiştirip `test_sources_replay.py`'yi çalıştırın. `signature`'ın ve öğe sayısının nasıl değiştiğine bakın.
Canlı sistemde bu `structure_change` alarmına dönüşür (04 §6). Sonra değişikliği geri alın.

## 3. Yerel uçtan uca ortam

```bash
cp mevzuat-core/app/.env.example mevzuat-core/app/.env
#   LLM'siz deneme için .env'de LLM_PROVIDER=none yapın (YZ adımları "skipped" döner); AUTH_MODE=disabled kalsın
docker compose up -d --build             # crawler + mongo + migrate + api + worker + beat + redis + postgres
docker compose exec crawler mevzuat-collect check-access      # kaynaklara erişim (BDDK yurt dışından erişilemez)
docker compose exec crawler mevzuat-crawler run TCMB KVKK     # elle tarama (ilk tarama → BASELINE)
docker compose exec crawler mevzuat-crawler check             # Mongo, bekleyen aktarım, son sinyal
docker compose logs -f worker                                 # ingest_crawl → process_pending akışı
# Portal: http://localhost:8000   Süreç haritası: http://localhost:8000/surec   API şeması: /docs
```

Gözlemleyin:

1. `run` çıktısındaki `new`, `changed`, `requests`.
2. Bir dakika içinde worker logunda `ingest: …` satırı.
3. Portalda yeni kayıt **görünmemeli**, çünkü ilk tarama BASELINE'dır. `/surec` sayfasında `reg:BASELINE` sayısı
   artar.
4. Aynı komutu tekrar çalıştırın. Ertelenen öğe kalmadıysa `new=0` olmalı, istek sayısı yalnızca liste sayfaları
   kadar olmalı. TCMB mevzuat listeleri 200'den fazla öğe içerdiği için ilk kurulumda iki tarama gerekebilir
   (ikincide ertelenenler hâlâ BASELINE olarak gelir). `new=0` üçüncü taramada görülür.

Mongo olmadan tek süreçte geliştirme: `mevzuat-core/app` içinde `CRAWL_MODE=local`, `mevzuat-collect run TCMB`
(SQLite + dosya arşivi). Ayrıntı `mevzuat-core/docs/runbook.md` §2.

## 4. Gözlem ve ölçüm sorguları

Sistemin gerçek davranışını tahminle değil veriyle konuşun. Aşağıdaki sorgular okuma yetkisi olan bir kullanıcıyla
çalıştırılır.

### 4.1 MongoDB (`mongosh` → `use mevzuat_crawl`)

```js
// Son 7 günde kaynak başına tarama sayısı, ortalama/en uzun süre, istek sayısı, hata ve yeni öğe
db.fetch_runs.aggregate([
  {$match: {finished_at: {$ne: null}, started_at: {$gte: new Date(Date.now() - 7 * 864e5)}}},
  {$project: {source_code: 1, status: 1, requests: 1, items_new: 1,
              sec: {$divide: [{$subtract: ["$finished_at", "$started_at"]}, 1000]}}},
  {$group: {_id: "$source_code", runs: {$sum: 1}, avg_s: {$avg: "$sec"}, max_s: {$max: "$sec"},
            avg_requests: {$avg: "$requests"}, new: {$sum: "$items_new"},
            failed: {$sum: {$cond: [{$eq: ["$status", "failed"]}, 1, 0]}},
            partial: {$sum: {$cond: [{$eq: ["$status", "partial"]}, 1, 0]}}}},
  {$sort: {_id: 1}}])

// Crawler canlı mı, şu an ne tarıyor, her kaynağın bir sonraki tarama zamanı
db.crawler_status.find().sort({at: -1}).limit(1)

// Bir kaynağın son taramasının kanal ayrıntısı (uyarılar, ertelenen, imza, baseline)
db.fetch_runs.find({source_code: "BDDK"}).sort({started_at: -1}).limit(1).pretty()

// Aktarılmayı bekleyenler ve talepler
db.raw_documents.countDocuments({synced: false})
db.crawl_requests.find().sort({created_at: -1}).limit(10)

// Takılı kalmış olabilecekler (06 §1)
db.crawl_requests.find({status: "running", started_at: {$lt: new Date(Date.now() - 3600e3)}})
db.fetch_runs.find({status: "running", started_at: {$lt: new Date(Date.now() - 3 * 3600e3)}})
db.locks.find()
```

### 4.2 PostgreSQL

```sql
-- Taramadan düzenleme kaydına ve sınıflandırmaya geçen süre (dakika, medyan), kaynak başına
SELECT r.primary_source_code,
       count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM r.detected_at - d.fetched_at) / 60)
         AS tarama_to_kayit_dk,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM r.classified_at - r.detected_at) / 60)
         AS kayit_to_siniflandirma_dk
FROM regulation r
JOIN raw_document d ON d.regulation_id = r.id AND d.source_code = r.primary_source_code
                   AND d.role = 'main' AND d.version = 1
WHERE r.classified_at IS NOT NULL AND r.detected_at > now() - interval '30 days'
GROUP BY 1 ORDER BY 1;

-- LLM çağrı süreleri (görev bazında)
SELECT task, count(*),
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms
FROM llm_call WHERE status = 'ok' AND created_at > now() - interval '7 days'
GROUP BY task ORDER BY task;

-- Hattın anlık durumu
SELECT processing_status, count(*) FROM raw_document WHERE is_latest GROUP BY 1;
SELECT processing_status, count(*) FROM regulation GROUP BY 1;

-- Açık alarmlar
SELECT alert_type, severity, source_code, message, opened_at FROM alert WHERE resolved_at IS NULL;
```

`detected_at` core'un, `fetched_at` DMZ sunucusunun saatidir. Saatler arasında kayma varsa ilk sütunu buna göre
yorumlayın.

### 4.3 Takvimi yeniden hesaplamak

`mevzuat-crawler/app` içinde (03 §1'deki tablo bu betikle üretildi):

```python
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from croniter import croniter
from app.collectors.config import load_sources

tz = ZoneInfo("Europe/Istanbul")
start = datetime(2026, 10, 12, tzinfo=tz)          # bir pazartesi
end = start + timedelta(days=7)
for s in load_sources(Path("config/sources.yaml")).sources:
    times = set()
    for expr in s.schedule:
        it = croniter(expr, start - timedelta(seconds=1))
        while (t := it.get_next(datetime)) < end:
            times.add(t)
    monday = sorted(t.strftime("%H:%M") for t in times if t.weekday() == 0)
    print(f"{s.code:13} {len(times):3}/hafta  pazartesi: {monday}")
```

## 5. Yeni kaynak veya kanal eklemek (adım adım)

1. **Keşif:** Sitede RSS/Atom var mı? Ön yüz bir JSON API kullanıyor mu (tarayıcı geliştirici araçları → Ağ
   sekmesi)? Varsa HTML kazımadan önce onu tercih edin (`feed`, `json_api`, `wordpress_api`). Yoksa liste
   sayfasının yapısına göre `link_pattern` (şablona dayanıklı) veya `css_list` seçin. Yayın alışkanlığını
   (gün/saat) not edin.
2. **Tanım:** `mevzuat-core/app/config/sources.yaml`'a kaynağı ekleyin: `code`, `name`, `base_url`, `client`,
   `allowed_hosts`, `schedule`, `expected`, `channels[]`. Mevcut bir kanalı şablon alın. Parametre açıklamaları
   her stratejinin dosya başı yorumundadır.
3. **Canlı deneme (kurum ağından/DMZ'den):** `mevzuat-collect probe KOD` çalıştırın. Öğe sayısı, başlıklar,
   tarihler ve kimlikler doğru mu? `uyarı` satırlarını okuyun. TLS hatası alırsanız `mevzuat-collect ca-fetch HOST`.
4. **Fixture kaydı:** `mevzuat-collect probe KOD --record tests/fixtures/<kod> --with-details`
5. **Testler:** `tests/test_sources_replay.py` parametre listesine `(KOD, kanal, asgari_öğe)` ekleyin. Yeni kaynakta
   `tests/test_sources_config.py::SCOPE_SOURCES` kümesini güncelleyin (test kapsamdaki kaynak listesini birebir
   denetler). `verified` alanına doğrulama tarihini yazın.
6. **Yeni strateji gerekiyorsa:** `collectors/strategies/` altında `name` ve `list_items(ctx) -> ChannelResult`
   olan bir sınıf yazın, `strategies/__init__.py`'deki kayda ekleyin. `signature` üretmeyi unutmayın, yapı
   değişikliği uyarısı ona dayanır.
7. **Core tarafı (yeni kurum için):**
   - `app/processing/normalize.py:ISSUERS`: kurum kodu, adı, RG'deki adının regex'i. Tekilleştirme ve çapraz
     kontrol için gereklidir.
   - `config/taxonomy.yaml` → `relevance_rules.source_priority` / `relevance_rules.issuer_priority`: ilgililik
     skorundaki kaynak önceliği.
   - İsteğe bağlı: RG `ilan_issuers` listesi, `CROSS_CHECK_ISSUERS`.
8. **Eşitle ve doğrula:** `python tools/shared.py sync && make test && make lint`
9. **Dağıtım:** Crawler ve core imajları. İlk taramada mevcut içerik BASELINE olur, portala düşmez. Bunu iş
   birimine önceden söyleyin.

## 6. Sık senaryolar

| Senaryo | Komut / yer |
|---|---|
| Kaynak "down" | DMZ'de `podman exec mevzuat-crawler mevzuat-collect check-access KOD`, crawler logu (`journalctl -u mevzuat-crawler`) |
| Site yapısı değişti (`structure_change`) | `mevzuat-collect probe KOD` → `sources.yaml` seçicilerini düzelt → fixture'ı yeniden kaydet → replay testi |
| Kesinti sonrası kaçan dönem | Admin API `POST /api/v1/admin/sources/{code}/backfill` (fark ≤62 gün) veya `mevzuat-monitor backfill KOD --from … --to …` (sınırsız); DMZ'de doğrudan `mevzuat-crawler backfill …`. RG gün gün taranır; diğer kaynaklarda backfill = normal tarama (liste sayfası ne gösteriyorsa) |
| Hemen tarama | Admin API "şimdi tara" (`POST /api/v1/admin/sources/{code}/run`; portal arayüzünde düğme yok; kaynak kodunu büyük harfle yazın, bkz. 06 §16) → kaynak meşgul değilse ≤15 sn içinde crawler alır |
| Bir aşamayı yeniden çalıştırma | `POST /api/v1/admin/reprocess` (`extract \| classify \| summarize \| match`, tarih aralığı) |
| TLS zincir hatası | `mevzuat-collect ca-fetch HOST` → `config/certs`'e ekle (core'da) → sync → imaj |

Ayrıntılı triyaj tablosu: `mevzuat-core/docs/runbook.md` §4.

## 7. Kod okuma soruları

Cevapları kodda bulun, ekip içinde tartışın:

1. `CrawlerService.tick()` bir kaynağı hem zamanlamadan hem talepten aynı turda almaya çalışırsa ne olur?
2. `MongoCrawlStore.add_version` neden önce `insert`, sonra eski kaydı `update` ediyor? Sıra tersine olsa hangi
   çökme senaryosunda belge kaybolurdu?
3. `ItemRef.listing_hash()` neden URL'yi de içeriyor? İçermeseydi ne kaçardı?
4. `WordPressApiStrategy` neden `since`'ten 6 saat geriye gidiyor ve saate yuvarlıyor?
5. `ResmiGazeteStrategy._parse` içindeki `key_rx` kontrolü olmasaydı, olmayan bir mükerrer istendiğinde ne olurdu?
6. Bir ekin `external_id`'si neden `ana_id#sha[:12]`? Ek içeriği değişirse ne olur?
7. `CrawlIngestor._mark_synced` neden `rev`'i filtreye koyuyor?
8. `Linker._key_match` zayıf anahtarlarda neden "farklı kaynak" şartı arıyor?
