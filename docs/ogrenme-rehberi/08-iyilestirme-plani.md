# 08 — Uçtan Uca İyileştirme Planı ve Uygulama Durumu

Bu belge iki kaynağı birleştirir:

- **[T]** Crawler ve ortak toplama altyapısının (`collectors/`, `crawler/`, `mongo.py`, `tools/shared.py`) kodla
  karşılaştırılarak doğrulanmış taraması (`T-x.y`).
- **[R]** [06 — Geliştirme Noktaları ve Riskler](06-gelistirme-noktalari.md) (`R-n`).

İlk sürümü bir plandı. Bu sürümde her kalemin **gerçekten gerekli olup olmadığı** kodla yeniden tartıldı ve gerekli
olanlar uygulandı (2026-10-13). Plandaki bazı kalemler ya gereksiz bulundu, ya daha basit bir tasarımla çözüldü, ya da
kodla çözülemeyen (işletim/karar) işler olarak bırakıldı. Gerekçeler aşağıda.

---

## 1. Gereklilik analizi ve karar

| İş | Konu | Karar | Gerekçe |
|---|---|---|---|
| 0.1 | `shared.py` Windows yol ayracı | **Uygulandı** | `sync` Windows'ta 18 ortak dosyayı siliyordu (doğrulandı). Diğer tüm değişiklikler `sync` gerektiriyor. |
| 0.2 | CI'da test aşaması | **Bekliyor (işletim)** | Gerekli, ama Jenkins ajanında Python/Docker ortamını bilmeden yazmak kırık pipeline riski taşır. Aşağıda §4. |
| 0.3 | Dağıtım ayarları | **Uygulandı** | Sabit host adı, `--stop-timeout`, bellek sınırı. `PodmanArgs` ile (bkz. §3). |
| 0.4 | Üretimde ölçüm | **Bekliyor (işletim)** | Üretim verisine buradan erişilemez. Sorgular §5'te. |
| 1.1 | Hata sarmalama, `finish_run` garantisi | **Uygulandı** | Zehirli öğe tüm kaynağı her saat kesiyordu. |
| 1.2 | Ek tekilleştirme, yazma sırası, eski ek `is_latest` | **Uygulandı** | Mongo yazımı atomik değildi: kesinti ekleri kalıcı kaybettiriyordu. |
| 1.3 | Baseline kapıları | **Uygulandı, tasarım basitleşti** | Hata defteri koleksiyonu gereksiz bulundu (aşağıda). |
| 1.4 | RG kısmi hata dayanıklılığı | **Uygulandı** | Tek ilan dizini hatası günün tüm maddelerini atıyordu. |
| 1.5 | Küçük harfli kaynak kodu | **Uygulandı** | API 202 dönüp talebi crawler'da başarısız ediyordu. |
| 2.1 | Kalıntı temizliği | **Uygulandı, tasarım değişti** | Kira yenileme ve `CRAWLER_ID` gereksiz bulundu; yerine sinyal tabanlı temizlik. |
| 2.2 | Mongo zaman aşımları, toplam süre | **Uygulandı** | Watchdog gereksiz bulundu. |
| 2.3 | Durdurma bayrağı | **Uygulandı** | Küçük değişiklik; SIGKILL penceresini daraltır. |
| 2.4 | Backfill'i taleplere bölme | **Ertelendi**; fetcher kapatma ve CLI sınırı **uygulandı** | Bölme karmaşık, nadir kullanım. |
| 2.5 | İzleme: takılı `running` | **Gereksiz** | 2.1 bu durumları kendiliğinden temizliyor. |
| 3.1 | Akış halinde indirme | **Uygulandı** | OOM, öğe kayıtlı olmadığı için her saat tekrarlanır. |
| 3.2 | Yönlendirme izin listesi | **Uygulandı, daha dar** | Son adım denetlenir; adım adım takip gereksiz. |
| 3.3 | Süreç geneli hız sınırı | **Gereksiz (şimdilik)** | Etki küçük, test kırılganlığı yaratır. |
| 3.4 | `browser` istemcisi | **Gereksiz** | Hiçbir kaynak kullanmıyor; kullanıldığında zaten açık hata verir. |
| 4.1 | Webhook, panel, metrik | **Bekliyor (işletim)** | Yapılandırma ve ürün işi. |
| 4.2 | Sertifika takvimi | **Bekliyor (işletim)** | Takvim kaydı; kodla yapılamaz. |
| 4.3 | Sürümlü imaj etiketi | **Bekliyor (işletim)** | Pipeline ve dağıtım kararı. |
| 4.4 | `API_REFERENCE.md` | **Bekliyor** | Üretici betik tüm ana servis bağımlılıklarını gerektirir; bu ortamda çalıştırılamadı. |
| 4.5 | Hafta sonu SLA, açılış telafisi | **SLA bekliyor; telafi gereksiz** | Telafi yokluğu bilinçli tasarım (aşağıda). |
| 4.6 | GridFS izleme | **Bekliyor (işletim)** | Sorgu §5'te. |
| 4.7 | Tükenmiş kayıtlar güvenlik ağını tıkıyor | **Doğrulanmadı** | Ana servis pipeline'ında; crawler kapsamı dışında. Önce `processing/pipeline.py` ve `ai/classify.py` okunmalı. |
| 4.8 | Ortak paket | **Ertelendi** | Uzun vade; 0.1 kısa vadeli sorunu çözdü. |
| 5.1 | Tarih ayrıştırma | **Gereksiz** | Kanıt yok; değişiklik regresyon riski taşır. |

### Tasarımı değişen veya gereksiz bulunan kalemlerin gerekçesi

- **1.3 öğe hata defteri (yeni `item_failures` koleksiyonu) gereksiz.** İhtiyaç, kalıcı 404'lü bir öğenin baseline'ı
  sonsuza kadar açık tutmamasıydı. Bu, hatayı **geçici** ve **kalıcı** diye ayırarak çözülür (kalıcı: 404/410 gibi
  4xx, boyut, izin listesi; geçici: 5xx, ağ, beklenmeyen hata). Sayaçlar (`baseline_runs`, `empty_runs`) zaten
  `fetch_runs.channels` içinde taşınıyor, yeni koleksiyon gerekmiyor. Geçici hata ise `baseline_max_runs` sınırıyla
  sonsuz sürmez. Dead-link geri çekilmesi (R-7) tek başına ucuz kalıyor (saatte bir istek) ve ertelendi.
- **2.1 kilit kirası (periyodik yenileme) gereksiz.** Servis içinde `self.running` aynı kaynağın ikinci taramasını
  zaten engelliyor; kilit yalnızca elle başlatılan ikinci bir süreç için var. Sorun uzun taramada kilidin düşmesi
  değil, **ölü süreçten kalan kilit**ti, o da temizlikle çözülür. TTL 3600 olarak bırakıldı.
- **2.1 `CRAWLER_ID` ortam değişkeni gereksiz.** Sabit host adı dağıtım ayarıyla (0.3) sağlanıyor; ayrıca temizlik
  host adı değişse bile çalışıyor.
- **2.2 watchdog gereksiz.** Takılma kaynakları (Mongo soketi, yavaş damlayan HTTP) artık zaman aşımına bağlı; takılı
  iş parçacığı istisnaya dönüşüyor.
- **3.2 adım adım yönlendirme gereksiz.** DMZ'den yalnızca MongoDB açık ve ortam on-prem; asıl risk, izin listesi
  dışındaki içeriğin arşive ve LLM hattına girmesi. Bunu son adımın (`final_url`) denetimi tamamen kapatır.
  Fixture'larda çapraz-host yönlendirme yok.
- **4.5 açılış telafisi gereksiz.** `service.py` modül açıklamasına göre kaçan tetiklemelerin telafi edilmemesi
  bilinçlidir: içerik sitede durduğu için sonraki taramada toplanır. Günlük 7 RG taraması bu garantiyi güçlendirir.
- **2.5 izleme gereksiz.** Yeni temizlik `running` talep/tarama kalıntılarını birkaç dakikada kendi giderir; crawler
  tamamen durursa mevcut `crawler_down` alarmı zaten çalışır.

---

## 2. Uygulananlar

Tüm değişiklikler `mevzuat-core`'da yapıldı ve `python tools/shared.py sync` ile crawler'a eşitlendi.

### 0.1 `tools/shared.py`
- `files()` artık `as_posix()` döndürüyor; `dependencies()` UTF-8 okuyor.
- Doğrulama: Windows'ta `check` 18 fark yerine "eşit", `sync` simülasyonu 18 yerine 0 silme.
- Test: `tools/test_shared.py`.

### 1.1 Hata sarmalama ([http.py](../../mevzuat-core/app/app/collectors/http.py), [runner.py](../../mevzuat-core/app/app/collectors/runner.py))
- httpx backend: `TooManyRedirects`, `DecodingError`, `UnsupportedProtocol`, `InvalidURL` → `FetchError`
  (tekrar denenmez); `RemoteProtocolError` ve diğer tüm `httpx.HTTPError` → `TransientFetchError`.
- `_store_items`: indirme aşamasında `FetchError` dışındaki hatalar öğe başına uyarıya dönüşür, döngü sürer.
- Yazma/Mongo hatası kanalı `rep.error` ile durdurur, sonraki kanallar taranır.
- `run()` `try/finally`: istisna (KeyboardInterrupt dahil) yükselse bile `finish_run` çağrılır.
- Davranış: geçici öğe hatası, durum **`partial`** yapar (eskiden `success`). `last_success` yalnızca `success`
  koşularına bakar; artımlı API kaynaklarında `since` bu durumda biraz daha eski kalabilir, zararsızdır.

### 1.2 Ek tekilleştirme ve yazma sırası ([mongo_store.py](../../mevzuat-core/app/app/crawler/mongo_store.py), [store.py](../../mevzuat-core/app/app/collectors/store.py))
- Ekler içerik hash'ine göre tekilleştirilir.
- Mongo `add_version` sırası: **ekler (upsert) → ana belge → eski sürümü kapat**. Ana belge işleme noktasıdır;
  yarım kalan yazımda ana belge yoktur, sonraki tarama yeniden yazar ve kalıntı eki aynı anahtarla değiştirir.
  `ingest.py` ebeveyni henüz olmayan eki zaten erteler.
- Eski sürümün ekleri de `is_latest=False` olur (Mongo ve SQL).
- Mevcut hasar (daha önce ekleri kaybolmuş belgeler) bu değişiklikle **onarılmaz**: içerik aynı olduğundan
  `touch_unchanged` yoluna girer. Boyutu §5'teki sorguyla ölçülmeli; sıfırdan büyükse ayrı bir onarım komutu gerekir.

### 1.3 Baseline kapıları ([runner.py](../../mevzuat-core/app/app/collectors/runner.py))
Kanal baseline'ı yalnızca şu koşulların **hepsi** sağlanınca kapanır:
- ertelenen öğe yok, geçici hatayla indirilemeyen öğe yok (kalıcı 404/410 engellemez),
- liste eksik değil (`ChannelResult.incomplete`) ve koşu durdurulmamış,
- liste "şüpheli biçimde boş" değil: `expected.min_items_per_run` altında kalan kanal, `BASELINE_EMPTY_RUNS`
  (varsayılan 3) ardışık koşudan sonra "gerçekten az" sayılır. `min_items_per_run: 0` olan kaynaklar (MASAK) eskisi gibi
  hemen kapanır.

Geçici hata `BASELINE_MAX_RUNS` (varsayılan 48) koşudan uzun baseline'ı açık tutamaz; kapanırken uyarı yazılır.
Bu, sonradan gelen gerçek düzenlemelerin sessizce BASELINE sayılmasını sınırlar.

### 1.4 Resmî Gazete ([resmi_gazete.py](../../mevzuat-core/app/app/collectors/strategies/resmi_gazete.py))
Yalnızca bugünün asıl sayısı zorunludur. Dünün/mükerrerin fihristi ya da ilan dizini alınamazsa uyarı yazılır,
`ChannelResult.incomplete=True` olur; toplanan maddeler atılmaz, koşu `partial` raporlanır.

### 1.5 Kaynak kodu
API (`admin_run_source`, `admin_backfill`) ve `mevzuat-monitor backfill` kodu kanonik hale getirir.

### 2.1 Kalıntı temizliği ([mongo.py](../../mevzuat-core/app/app/mongo.py), [service.py](../../mevzuat-core/app/app/crawler/service.py))
Süreç SIGKILL/OOM ile ölünce kilit, `running` talep ve `running` tarama kaydı kalır; host adı da değişebilir.
- **Açılışta:** kendi sahibinin kilitleri bırakılır, kendi `running` talepleri `pending`'e alınır.
- **Her `CRAWLER_SWEEP_S` (60 sn) aralığında:** sinyali (`crawler_status`) `max(4×poll, 120)` sn'den bayat sahiplerin
  kilitleri bırakılır, `running` talepleri `pending`'e alınır; kilidi tutulmayan kaynağın `running` taraması
  `failed (interrupted)` işaretlenir.
- **Dokunulmayanlar:** sinyal belgesi hiç olmayan sahipler (elle `mevzuat-crawler run`) bilinmeyen sayılır; kilitleri
  yalnızca TTL ile düşer. Böylece ikinci bir örnek çalışan taramanın kilidini kırmaz.
- `crawler_status.at` üzerinde 7 gün TTL indeksi: eski host adı belgeleri birikmez.
- Beklenen kurtarma süresi: ölümden sonra en çok ~3–4 dakika (sinyal eşiği + temizlik aralığı).

### 2.2 Zaman aşımları
- Mongo: `socketTimeoutMS=60000` (`MONGO_SOCKET_TIMEOUT_MS`), `connectTimeoutMS=10000`.
- HTTP: `COLLECT_TOTAL_TIMEOUT_S` (120 sn) toplam indirme süresi (httpx zaman aşımı okuma başınaydı, yavaş damlayan
  sunucu sınırsız tutabilirdi).

### 2.3 Durdurma
`SourceCollector(should_stop=...)` öğe aralarında bayrağı kontrol eder; durdurulan koşu `partial` kapanır, kalan
öğeler sonraki koşuya kalır, baseline kapanmaz. `pool.shutdown(cancel_futures=True)`. Yarıda kalan RG backfill talebi
`pending`'e alınır ve kaldığı günden sürdürülür.

### 2.4 Backfill (kısmi)
`backfill()` her gün için `fetcher.close()` çağırır; `mevzuat-monitor backfill` API ile aynı 62 gün sınırına tabidir.

### 3.1 Akış halinde indirme ([http.py](../../mevzuat-core/app/app/collectors/http.py))
httpx ve curl_cffi backend'leri gövdeyi parça parça okur: `Content-Length` sınırı aşıyorsa gövde hiç okunmaz,
aksi halde **açılmış** (gzip sonrası) boyut `COLLECT_MAX_BYTES`'ı aşınca kesilir.

### 3.2 Yönlendirme
`Fetcher.get` yanıtın vardığı son adresi (`final_url`) izin listesine karşı denetler; dışına çıkarsa `HostNotAllowed`.
`COLLECT_ENFORCE_REDIRECT_HOSTS=false` yalnızca loglar (geçiş için).

### 0.3 Dağıtım ([mevzuat-crawler.container](../../mevzuat-crawler/deploy/dmz/mevzuat-crawler.container), [docker-compose.crawler.yml](../../mevzuat-crawler/deploy/dmz/docker-compose.crawler.yml))
`PodmanArgs=--hostname=mevzuat-crawler --stop-timeout=140 --memory=1g`; compose'da `hostname` ve `mem_limit`.
Quadlet'in `HostName=`/`StopTimeout=`/`Memory=` anahtarları yerine `PodmanArgs` seçildi: desteklenmeyen bir anahtar
unit'in hiç üretilmemesine yol açar ve RHEL 9'daki Podman sürümü doğrulanmadı.

---

## 3. Doğrulama

| Kontrol | Sonuç |
|---|---|
| `python tools/shared.py check` (Windows) | eşit (30 yol) |
| `ruff check` (core ve crawler) | temiz |
| crawler testleri, gerçek MongoDB olmadan | 95 geçti, Mongo testleri atlandı |
| core ortak test seti (`http`, `runner`, `dates`, `resmi_gazete`, `sources_*`, `resolvers`) | 95 geçti |
| crawler testleri, **mongomock** ile (Mongo testleri dahil) | 103 geçti |
| yeni testler: gerçek HTTP sunucusuna karşı her iki backend | boyut, gzip bombası, yönlendirme döngüsü, hata eşlemesi |

Sınırlar (dürüst not):
- **Gerçek MongoDB'ye karşı çalıştırılmadı.** Mongo testleri bu makinede atlanır; yerel doğrulama `mongomock` ve
  bellek içi bir GridFS yerine koymayla yapıldı. Bir kez gerçek MongoDB ile çalıştırılmalı: `docker run -d --name mevzuat-mongo-test -p 27018:27017 mongo:7`
  ardından `make test-crawler`.
- Ana servise ait testler (`test_ingest`, `test_api`, …) fastapi/celery gerektirir; bu ortamda çalıştırılmadı. API
  değişikliği (`code = ….get(code).code`) iki satırlık ve `test_ingest.py`'ye bir doğrulama eklendi ama koşmadı.
- Podman davranışı (hostname, stop-timeout, `--memory` değeri) DMZ'de denenmedi.
- Testlerde bir mutasyon denemesi yapıldı: eski `add_version` ile yeni "kesinti sonrası" testi başarısız oluyor.

### Dağıtımda dikkat
- Yeni ayarlar (hepsi ortam değişkeni olarak geçersiz kılınabilir): `MONGO_SOCKET_TIMEOUT_MS`,
  `MONGO_CONNECT_TIMEOUT_MS`, `CRAWLER_SWEEP_S`, `COLLECT_TOTAL_TIMEOUT_S`, `COLLECT_ENFORCE_REDIRECT_HOSTS`,
  `BASELINE_EMPTY_RUNS`, `BASELINE_MAX_RUNS`.
- `socketTimeoutMS` ana servisin ingest istemcisi için de geçerlidir (`crawl_db` ortak). Çok büyük toplu okuma
  60 sn'yi aşarsa `MONGO_SOCKET_TIMEOUT_MS` artırılmalı.
- Yönlendirme denetimi varsayılan açık. İlk gün loglarda `HostNotAllowed … yönlendirme` aranmalı; meşru bir
  yönlendirme çıkarsa kaynağın `allowed_hosts` listesine eklenmeli (ya da geçici olarak `false`).
- `partial` durum artık geçici öğe hatalarında da görülür; panelde/izlemede `partial` sayısının artması beklenen bir
  değişikliktir, sorun göstergesi değildir.
- Bellek sınırı (`--memory=1g`) tahmini bir değerdir; ilk haftada `podman stats` ile izlenmeli.
- Dağıtım bir tarama penceresi dışında yapılmalı; yeni imaj açılışta eski sürecin kalıntılarını temizler.

---

## 4. Kalan işler (kodla çözülemeyenler ve ertelenenler)

| İş | Ne yapılmalı | Kim/Ne zaman |
|---|---|---|
| 0.2 CI | Pipeline'a `make test` + `make lint` (Linux ajan). `.dockerignore` testleri imaja almadığından test, imajdan önce ajanda ya da `python:3.12` konteynerinde koşmalı; Mongo testleri için `mongo:7`. Linux ajan `shared.py` düzeltmesinin ikinci güvencesi. | Jenkins sahibi; ajan ortamı belirlenince |
| 0.4 / §5 | Üretim ölçümleri | İşletim, dağıtımdan önce ve sonra |
| 1.2 onarım | ~~Eksik ekleri yeniden getiren komut~~ **Uygulandı** (§7.3): `mevzuat-crawler repair-attachments`. Çalıştırmak işletim işi | Firewall düzeldikten sonra |
| 2.4 bölme | Uzun (saatler süren) RG backfill'inde zamanlanmış RG taramaları atlanır (`tick`). Backfill rutin ve uzun hale gelirse gün başına ayrı taleplere bölünmeli. Şimdilik ≤62 gün sınırı ve süren backfill'in kendisi bugünü de kapsıyorsa risk düşük. | Rutinleşirse |
| 4.1 | `ALERT_WEBHOOK_URL`'i kurum izlemesine bağlama; panel/metrik | İşletim + ürün |
| 4.2 | Sertifika hatırlatmaları: `geotrust-tls-rsa-ca-g1.pem` (2027-11-02) için 2027-09-03; `globalsign-rsa-ov-ssl-ca-2018.pem` (2028-11-21) için 2028-09-22 | Sorumlu kişinin takvimi |
| 4.3 | `latest` yerine sürümlü etiket; Quadlet `Image=` sabit etiket | Pipeline sahibi |
| 4.4 | `make api-reference` (ana servis ortamında); `PATCH /api/v1/regulations/{id}/content` eksik | Core geliştiricisi |
| 4.5 | Hafta sonu/gece için kabul edilebilir gecikme (SLA) yazılı hale getirilmeli | Başkanlık kararı |
| 4.6 | Aylık GridFS büyüklüğü sorgusu | İşletim |
| 4.7 | Önce doğrula: tükenmiş `EXTRACT_FAILED`/`AI_FAILED` kayıtlar güvenlik ağı turunu gerçekten tıkıyor mu | Core geliştiricisi |
| 4.8 | Ortak kodu sürümlü pakete taşıma | Uzun vade |
| R-7 | Sürekli hatalı detaylar için öğe başına üstel geri çekilme (şimdi saatte 1 istek) | Gerekirse |

---

## 5. Ölçüm sorguları (mongosh, üretim)

```js
// Yarım kalmış taramalar (2.1)
db.fetch_runs.countDocuments({status:"running", started_at:{$lt:new Date(Date.now()-3600e3)}})

// Sahipsiz talepler (2.1)
db.crawl_requests.find({status:"running", started_at:{$lt:new Date(Date.now()-2*3600e3)}})

// Boş listeyle baseline'ı kapanan koşular (1.3): eski davranışın bıraktığı hasar
db.fetch_runs.aggregate([{$project:{source_code:1,started_at:1,c:{$objectToArray:"$channels"}}},
  {$unwind:"$c"},{$match:{"c.v.items":0,"c.v.baseline":true,"c.v.baseline_complete":true}},{$limit:20}])

// Eki kaybolmuş olabilecek BDDK ana belgeleri; üst sınır, bazı duyuruların eki hiç yoktur (1.2)
db.raw_documents.aggregate([
  {$match:{source_code:"BDDK",role:{$ne:"attachment"},is_latest:true,
           channel:{$in:["duyurular","mevzuat_duyurulari","kurulus_duyurulari"]}}},
  {$lookup:{from:"raw_documents",localField:"_id",foreignField:"parent_id",as:"ch"}},
  {$match:{ch:{$size:0}}},{$count:"eksik_ek_olasi"}])

// GridFS büyüklüğü (4.6)
db.raw.files.aggregate([{$group:{_id:null, gb:{$sum:{$divide:["$length",1073741824]}}}}])
```

Dağıtımdan sonra temizliğin çalıştığını görmek için crawler logunda `ölü süreç kalıntıları temizlendi` ve
`yarıda kalan … talep` satırları aranır.

### Ölçüm kaydı

| Ölçüm | Tarih | Sonuç | Karar |
|---|---|---|---|
| `running` fetch_run > 1 sa | | | |
| `running` talep > 2 sa | | | |
| Boş listeyle kapanan baseline | | | |
| Çocuksuz BDDK ana belge | | | |
| `raw.files` toplam GB | | | |

## 6. Açık sorular (DMZ'de doğrulanacak)

| # | Soru | Nasıl doğrulanır |
|---|---|---|
| 1 | Podman hostname'i `--hostname` ile sabitleniyor mu? | `podman exec mevzuat-crawler hostname` → `systemctl restart` → tekrar |
| 2 | `--stop-timeout=140` uygulanıyor mu? | `podman inspect mevzuat-crawler --format '{{.Config.StopTimeout}}'`, `systemctl cat mevzuat-crawler` |
| 3 | `--memory=1g` yeterli mi? | `podman stats` ilk haftada |
| 4 | Gerçek MongoDB'de yeni testler geçiyor mu? | `mongo:7` ile `make test-crawler` |
| 5 | Meşru yönlendirme izin listesi dışına çıkıyor mu? | Dağıtım sonrası log: `yönlendirme izin listesi dışına çıktı` |

## 7. 13–14 Ekim 2026 crawler.log analizi sonrası iyileştirmeler

Kaynak: üretim logu (GİB 14:33 ve 15:02 koşuları, BDDK saatlik `changed=200`). Bulgular ve yapılanlar:

### 7.1 Teşhis
- **GİB**: `cdn.gib.gov.tr`'ye logda hiç başarılı istek yok; `Connection reset by peer` 107 ek indirmesini düşürdü.
  Büyük olasılıkla firewall/allowlist (`kurum-devreye-alma.md` satır 34). Kod bunu "geçici" sayıp 3 deneme (2+4 sn
  bekleme) yapıyor, hatayı **yutuyor** ve sonraki tur öğeyi `unchanged` sayıp atlıyordu: **ekler kalıcı kayboluyordu**.
- **GİB liste API'si**: `json_api` yalnız sayfa 0'ı çekiyordu; uzun kesintide sayfa 0'dan kayan öğeler kaçıyordu.
- **`partial` + `errors: []`**: nedenler yalnız `channels.*.warnings` içindeydi; log özetinde ve `errors` alanında yoktu.
- **BDDK**: `id_pattern` yalnız sayıyı alıyor. `/Duyuru/Detay/951` ile `/Mevzuat/DokumanGetir/951` aynı
  `external_id`'ye (`951`) düşüyor (canlı sayım 13.10.2026: 1195 kimlikten **147'si** birden çok kanalda). Biri
  diğerinin geçmişinin üstüne yazıyor; her koşuda `max_details` (200) kadar sahte "değişen" sürüm üretiliyordu.

### 7.2 Uygulananlar
| Konu | Değişiklik |
|---|---|
| Görünürlük | `RunReport.errors`; `fetch_run.errors` ve log özeti artık öğe/ek/liste sorunlarını içerir. Bağlantı hatalarında traceback yerine tek satırlık uyarı |
| Devre kesici | `Fetcher`: bir host'a koşu içinde art arda `COLLECT_HOST_BREAKER_THRESHOLD` (3) bağlantı hatası → o koşuda kalan istekler beklemeden `HostUnreachable` (107 ek × ~8 sn boşa beklemez). Yeni koşu kapalı başlar, kendiliğinden iyileşir |
| Ek borcu | İndirilemeyen ek `extra.missing_attachments`'a yazılır: `url, attempts, next_try_at (1,2,4,8,16,24 sa), last_error, permanent`. İçeriği değişmeyen öğenin yalnız ekleri yeniden denenir (ana belge yeniden inmez, yeni sürüm açılmaz). `COLLECT_ATTACHMENT_MAX_ATTEMPTS` (10) sonra "vazgeçildi". Ek sınırı (`COLLECT_MAX_ATTACHMENTS`) aşıldığında da artık sessizce düşmez, kayıt tutulur. Bekleyen ek koşuyu `partial` yapar |
| Telafi sayfalaması | `json_api` `max_pages` (GİB kanallarında 10): baseline dışında ve sayfadaki hiçbir öğe arşivde yokken sonraki sayfa çekilir; bilinen öğeye çarpınca durur. Normal işleyişte ek istek yok. Sınıra örtüşmesiz ulaşılırsa liste `incomplete` |
| Kimlik çakışması koruması | Aynı `external_id` başka kanalda **farklı URL** ile bulunursa öğe atlanır, `id_conflicts` sayılır, koşu `partial` olur. Sahte sürüm üretimi durur. `id_prefix` parametresi eklendi (`css_list`, `feed`, `link_pattern`) |

### 7.3 Onarım komutu
```
mevzuat-crawler repair-attachments GIB --dry-run            # DMZ (Mongo); yalnız eksikleri sayar
mevzuat-crawler repair-attachments GIB                      # eksikleri indirir (bekleme süresini beklemez)
mevzuat-crawler repair-attachments GIB --reopen             # vazgeçilmişleri de yeniden dener
mevzuat-crawler repair-attachments GIB --max-attachments 20 # ek sınırı yüzünden atlananları da alır
```
Arşivdeki ana belge içeriğinden ek bağlantılarını yeniden çıkarır; arşivde olmayanları indirir. Yeni `fetch_run`
açmaz (`last_success` kaymasın). `CRAWL_MODE=local` için `mevzuat-collect repair-attachments` (Linux; `fcntl`).
Çıkış kodu: hâlâ bekleyen/engelli ek varsa 1.

### 7.4 Bekleyen karar: BDDK kimlik ayrımı (üretim verisini etkiler)
Koruma sahte sürümleri durdurur ama çakışan ~147 belgenin **diğer** kanaldaki kopyası toplanmaz. Kalıcı çözüm
Mevzuat ailesi kanallarına `id_prefix` vermektir; bu, ~1100 mevcut belgeyi "yeni kimlik" yapar. Göz önünde tutulacaklar:
1. Yeni kimlikler baseline sayılmazsa YZ hattına yeni düzenleme olarak girer. Önce o kanalların baseline'ı yeniden
   açılmalı ya da eski kayıtlardan taşıma betiği yazılmalı.
2. Daha önce üretilmiş sahte sürümler (BDDK, çakışan kimlikler, `version > 1`) için temizlik/işaretleme kararı gerekir.
Ölçüm sorgusu (mongosh): çakışan kimlikte kanalı değişen sürümler
`db.raw_documents.aggregate([{$match:{source_code:"BDDK"}},{$group:{_id:"$external_id",ch:{$addToSet:"$channel"},v:{$max:"$version"}}},{$match:{"ch.1":{$exists:true}}},{$sort:{v:-1}},{$limit:20}])`

### 7.5 Yeni ayarlar ve doğrulama
`COLLECT_HOST_BREAKER_THRESHOLD`, `COLLECT_ATTACHMENT_MAX_ATTEMPTS`, `COLLECT_ATTACHMENT_RETRY_ITEMS` (koşu başına
eki yeniden denenecek en çok öğe, 25). Core 228, crawler 141 test geçti; ruff temiz. Gerçek MongoDB ve LAN ingest
testleri (`test_ingest.py`: geç gelen ek, Mongo onarımı) bu makinede atlandı; `mongo:7` ile bir kez koşulmalı.
Ingest kodu okunarak geç gelen ekin mevcut ana belgeye bağlanacağı doğrulandı (`DOC_MUTABLE` içinde `extra`).
