# 06 — Geliştirme Noktaları ve Riskler

Bu liste kod okunarak çıkarıldı. Her madde için kanıt (dosya/fonksiyon), etki ve öneri verilmiştir. Bazı maddeler
çalışma ortamına bağlıdır. Onlar için "nasıl doğrulanır" adımı var; üretimde doğrulamadan iş kalemine
çevirmeyin. Öncelik, uyum riski (bir düzenlemenin kaçırılması veya gecikmesi) üzerinden verilmiştir.

Özet:

| # | Konu | Alan | Öncelik |
|---|---|---|---|
| 1 | CI'da testler çalışmıyor | Süreç | **Yüksek** |
| 2 | Yeniden başlatma sonrası sahipsiz talepler ve kilitler | Crawler | **Yüksek** (doğrulanmalı) |
| 3 | Uzun backfill sırasında aynı kaynağın zamanlanmış taramaları atlanır | Crawler | Orta |
| 4 | Durdurma süresi uzun taramalardan kısa | Crawler/dağıtım | Orta |
| 5 | Hafta sonu ve gece tarama boşlukları | İş kararı | Orta |
| 6 | İndirilemeyen detaylar ve BASELINE sınırı | Crawler | Orta |
| 7 | Sürekli hata veren detaylar her taramada yeniden deneniyor | Crawler | Düşük |
| 8 | Host başına hız sınırı süreç geneli değil | Crawler | Düşük |
| 9 | `browser` istemcisi imajda çalışmaz | Crawler | Düşük |
| 10 | Gözlemlenebilirlik: metrik yok | İşletim | Orta |
| 11 | Ara sertifika son kullanma tarihleri | İşletim | Orta (takvimli) |
| 12 | `latest` etiketiyle dağıtım | Dağıtım | Orta |
| 13 | `API_REFERENCE.md` güncel değil | Belge | Düşük |
| 14 | Ortak kodun kopyalanarak paylaşılması | Mimari | Düşük (uzun vade) |
| 15 | GridFS arşiv büyümesi | Kapasite | Düşük (izlenmeli) |

---

## 1. CI'da testler çalışmıyor — Yüksek

- **Kanıt:** `mevzuat-core/Jenkinsfile` ve `mevzuat-crawler/Jenkinsfile` aşamaları: Docker Build → Docker Deploy
  (Nexus'a push) → SonarQube. Test aşaması yok. `kurum-devreye-alma.md` §2 testi "isteğe bağlı" olarak tanımlıyor.
  `.dockerignore` testleri imaja almıyor.
- **Etki:** Kırık bir `sources.yaml` (ör. geçersiz cron, yanlış seçici) veya kod hatası doğrudan `latest` imajına,
  oradan DMZ'ye gidebilir. Replay testleri ağ gerektirmediği için CI'da rahatça çalışır.
- **Öneri:** Push'tan önce bir test aşaması ekleyin: `make test` eşdeğeri (`pytest -q` + `tools/shared.py check` +
  `ruff check`). Mongo testleri için pipeline'da `mongo:7` servis konteyneri.

## 2. Yeniden başlatma sonrası sahipsiz talepler ve kilitler — Yüksek (doğrulanmalı)

- **Kanıt:** Süreç kimliği host adıdır (`mongo.worker_id()` → `socket.gethostname()`). Açılışta yalnızca **aynı
  host adına** ait `running` talepler geri alınır (`requeue_orphaned_requests`: `claimed_by == owner`). Kilit
  devralma yalnızca TTL dolunca olur (`acquire_lock`, 3600 sn). Quadlet dosyasında `HostName=` yok
  (`deploy/dmz/mevzuat-crawler.container`). Podman'da varsayılan host adı konteyner kimliğidir ve Quadlet her
  başlatmada konteyneri yeniden oluşturur. Kubernetes'te yeni pod yeni ad alır.
- **Etki:** Tarama sırasında konteyner yeniden başlarsa (güncelleme, healthcheck `kill`, sunucu yeniden başlatma):
  - O an işlenen "şimdi tara"/backfill talebi sonsuza dek `running` kalır. İzleme yalnızca `pending > 30 dk` ve
    `failed` talepleri raporladığı için (`monitoring/crawler.py`) bu durum alarm üretmez.
  - Kesilen taramanın kaynağı, kilit süresi dolana kadar (≤1 sa) "başka bir tarama sürüyor" diye atlanır. BDDK
    gibi saatlik bir kaynakta bir tur kaçar.
  - Kesilen `fetch_runs` kaydı `running`'de kalır.
- **Nasıl doğrulanır:** DMZ'de `podman exec mevzuat-crawler hostname` → `systemctl restart mevzuat-crawler` →
  tekrar `hostname`. Değer değişiyorsa sorun geçerlidir. Mongo'da 05 §4.1'deki "takılı kalmış" sorguları.
- **Öneri (düşük maliyetli):** Quadlet'e `HostName=mevzuat-crawler` ekleyin. Chart'ta da sabit bir kimlik verin.
  Daha sağlamı: Crawler tek örnek olduğu için açılışta **tüm** `running` talepleri geri al, tüm `collect:*`
  kilitlerini bırak, eski `running` taramaları `failed (interrupted)` işaretle. İzlemeye "1 saatten uzun `running`
  talep" kontrolü ekleyin.

## 3. Uzun backfill sırasında aynı kaynağın zamanlanmış taramaları atlanır — Orta

- **Kanıt:** Backfill talebi `running[kod]`'a yazılır. Bu sürede o kaynağın tüm zamanlanmış tetiklemeleri
  "önceki tarama sürüyor" diye atlanır (`CrawlerService.tick`). RG backfill'i gün gün, her gün için detaylar
  dahil tam tarama yapar (`monitoring/reprocess.py:backfill`). Sayfalar arası 3 sn beklenir. API 62 güne kadar
  aralık kabul eder.
- **Etki (tahmin):** Uzun bir RG backfill'i saatler sürebilir. Bu sürede **günün Resmî Gazetesi taranmaz.**
  Ayrıca 1 saatlik kilit TTL'i backfill sırasında dolar. Aynı süreçte koruma `running` ile sürse de süreçler
  arası koruma kalkar.
- **Öneri:** Backfill'i gün başına ayrı taleplere bölün (zamanlanmış taramalar araya girebilsin) veya backfill'i
  ayrı bir kilit/iş türü olarak ele alın. Uzun işlerde kilidi periyodik yenileyin.

## 4. Durdurma süresi uzun taramalardan kısa — Orta

- **Kanıt:** SIGTERM'de süren taramalar beklenir (`pool.shutdown(wait=True)`), ama Quadlet `TimeoutStopSec=150`,
  compose `stop_grace_period: 2m`. İlk kurulumda (200 detay × 1,5–3 sn) veya yeni RG sayısında tarama bu süreyi
  aşabilir. Tarama döngüsü durdurma bayrağını kontrol etmiyor.
- **Etki:** Güncelleme anında tarama yarıda kesilir ve madde 2'deki durumlar oluşur. Veri tutarlılığı korunur:
  yazılan sürümler kalır, sonraki tarama devam eder.
- **Öneri:** `_store_items` döngüsünde durdurma bayrağına bakıp temiz çıkış (taramayı `partial` kapat, kilidi
  bırak) yapın. Güncellemeleri tarama pencereleri dışına planlayın (05 §4.1'deki `next_due`).

## 5. Hafta sonu ve gece tarama boşlukları — Orta (iş kararı)

- **Kanıt:** 03 §3. BDDK, TCMB, Ticaret, Rekabet ve TKBB hafta sonu hiç taranmıyor (Cuma akşamı → Pazartesi
  sabahı 60–66 sa). Crawler kapalıyken kaçan tetiklemeler telafi edilmiyor (02 §2).
- **Etki:** Hafta sonu yayımlanan acil bir kurum duyurusu Pazartesi sabahına kadar görülmez. RG'de yayımlananlar
  RG kaynağından yakalanır.
- **Öneri:** Başkanlıkla kabul edilebilir gecikmeyi (SLA) yazılı hale getirin. Gerekirse kritik kurumlara
  hafta sonu günde 1–2 pencere ekleyin. Açılışta "son başarılı tarama, takvimdeki son tetiklemeden eskiyse hemen
  tara" telafisini ekleyin (düşük maliyetli).

## 6. İndirilemeyen detaylar ve BASELINE sınırı — Orta

- **Kanıt:** `runner.SourceCollector.run`: `baseline_complete = baseline_complete or deferred == 0`. Detay
  indirme hatası (`FetchError`) `deferred`'ı artırmaz, öğe yalnızca atlanır (`_store_items`).
- **Etki:** İlk taramada detayı geçici olarak indirilemeyen eski bir öğe, baseline kapandıktan sonra
  indirildiğinde `is_baseline=False` olur. YZ hattına ve portala "yeni düzenleme" olarak düşer (yanlış pozitif).
- **Öneri:** Baseline modunda detay hatalarını da "ertelenen" sayın. Ya da öğenin liste tarihini kanalın ilk
  tarama tarihiyle karşılaştırarak eski içeriği baseline'a alın.

## 7. Sürekli hata veren detaylar her taramada yeniden deneniyor — Düşük

- **Kanıt:** Detayı 404 veren bir öğe hiç kaydedilmediği için (`prev is None`) her taramada yeniden indirilmeye
  çalışılır. Hem istek hem detay bütçesi harcar, `warnings` büyür.
- **Öneri:** Hata sayısını öğe bazında tutun (ör. Mongo'da küçük bir koleksiyon) ve üstel geri çekilme uygulayın.
  N denemeden sonra liste anlık görüntüsü (`role=listing`) olarak kaydedin.

## 8. Host başına hız sınırı süreç geneli değil — Düşük

- **Kanıt:** `Fetcher._throttle` sayaçları `Fetcher` nesnesindedir, yani kaynak taraması başına. BDDK ve Ticaret
  ikisi de mevzuat.gov.tr'ye gider ve hafta içi 10:00 ile 16:00'da aynı anda tetiklenir.
- **Etki:** Aynı host'a iki iş parçacığından paralel istek gidebilir (kibar tarama ilkesi zayıflar, engellenme
  riski).
- **Öneri:** Host bazlı bekleme durumunu süreç genelinde paylaşılan bir yapıya (kilitli sözlük) taşıyın.

## 9. `browser` istemcisi imajda çalışmaz — Düşük

- **Kanıt:** `http._BrowserBackend` Playwright'ı içe aktarır. Crawler `pyproject.toml`/`requirements.txt`
  Playwright içermez, imajda Chromium yok. Hiçbir kaynak şu an `client: browser` kullanmıyor.
- **Etki:** İleride bir kaynağa `browser` yazılırsa kanal her taramada `ImportError` ile hata verir.
- **Öneri:** Ya imaja isteğe bağlı olarak ekleyin (boyut ve DMZ güvenlik değerlendirmesiyle), ya da seçeneği
  şemadan kaldırıp yalnızca API/JSON keşfini zorunlu kılın. `test_sources_config` ile engelleyin.

## 10. Gözlemlenebilirlik: metrik yok — Orta

- **Kanıt:** Durum yalnızca log, Mongo `fetch_runs`/`crawler_status` ve PostgreSQL `alert` tablolarında.
  Prometheus vb. metrik ucu yok. Alarm iletimi isteğe bağlı webhook (`ALERT_WEBHOOK_URL`).
- **Etki:** Tarama süresi, istek sayısı, LLM gecikmesi gibi eğilimler ancak elle sorguyla görülür (05 §4).
  Webhook tanımlanmazsa alarmlar yalnızca portalda/logda kalır.
- **Öneri:** `ALERT_WEBHOOK_URL`'i kurum izleme sistemine bağlayın (öncelikli). Ardından 05 §4'teki sorgulardan
  bir yönetim paneli üretin (portalda admin sayfası). Kaynak başına son tarama süresi, istek sayısı, `next_due`,
  bekleyen aktarım ve LLM p95 gösterilsin. "Şimdi tara" ve backfill şu an yalnızca admin API'sinde var
  (portal arayüzü bu uçları çağırmıyor), aynı panele düğme olarak eklenebilir.

## 11. Ara sertifika son kullanma tarihleri — Orta (takvimli)

- **Kanıt:** `config/certs/geotrust-tls-rsa-ca-g1.pem` (Resmî Gazete) **2027-11-02**,
  `globalsign-rsa-ov-ssl-ca-2018.pem` (BDDK) **2028-11-21**. `test_intermediate_certificates_valid` süresi geçen
  sertifikada test kırar. Siteler yaprak sertifikalarını yenilerken ara sertifikayı daha erken değiştirebilir.
- **Öneri:** Takvime hatırlatma koyun (bitişten 60 gün önce). `check-access` başarısız olursa `ca-fetch` ile
  yenileyin (runbook §4.2).

## 12. `latest` etiketiyle dağıtım — Orta

- **Kanıt:** Chart values ve Quadlet `:latest` kullanıyor (`pullPolicy: Always`). Pipeline sürüm etiketi
  güncellemesi `kurum-devreye-alma.md`'de yalnızca "önerilen".
- **Etki:** Hangi sürümün çalıştığı ve geri dönüş belirsizleşir. DMZ'de `podman pull` zamanına göre farklı imaj
  çalışabilir.
- **Öneri:** Git SHA/semver etiketleri. Chart'ta `image.tag` pipeline tarafından güncellensin, DMZ'de Quadlet
  `Image=` sabit etiket gösterilsin.

## 13. `API_REFERENCE.md` güncel değil — Düşük

- **Kanıt:** `mevzuat-core/app/app/api/main.py`'de `PATCH /api/v1/regulations/{reg_id}/content` var,
  `mevzuat-core/API_REFERENCE.md`'de yok.
- **Öneri:** `make api-reference` ile yeniden üretin. CI'da üretilen dosyayla depodaki farkı denetleyin.

## 14. Ortak kodun kopyalanarak paylaşılması — Düşük (uzun vade)

- **Kanıt:** 30 ortak yol `tools/shared.py` ile core'dan crawler'a kopyalanıyor (`check` eşitliği denetliyor).
- **Etki:** Ayrı kurum repolarında bir tarafın elle düzeltilmesi sürüklenmeye yol açar.
- **Öneri:** Ortak katmanı (`collectors`, `crawler`, `mongo`, `storage`, `settings`) iç Nexus PyPI'da sürümlü bir
  paket yapın. Repolar sürüm sabitlesin.

## 15. GridFS arşiv büyümesi — Düşük (izlenmeli)

- **Kanıt:** Ham içerik bilinçli olarak hiç silinmez. İçerik adresli olduğu için tekrarlar saklanmaz, ama her
  yeni sürüm ve ek eklenir. `refresh_after_days` kanalları yerinde güncellenen metinlerin her değişikliğini yeni
  sürüm olarak saklar.
- **Öneri:** Aylık `db.raw.files.aggregate([{$group: {_id: null, gb: {$sum: {$divide: ["$length", 1073741824]}}}}])`
  ile büyümeyi izleyin. Yedekleme süresini (runbook §8) buna göre planlayın.

---

## Önerilen ilk adımlar (küçükten büyüğe)

1. CI'a test aşaması (madde 1). Bir günlük iş, en yüksek getiri.
2. DMZ'de host adı doğrulaması. Gerekirse `HostName=` ve açılış temizliği (madde 2). Küçük kod değişikliği +
   test.
3. `ALERT_WEBHOOK_URL` bağlantısı ve sertifika hatırlatmaları (madde 10, 11).
4. Başkanlıkla tarama SLA'sı görüşmesi (madde 5).
5. Baseline/detay hatası düzeltmesi ve detay hata geri çekilmesi (madde 6, 7).
