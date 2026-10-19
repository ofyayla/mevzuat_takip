# 06 — Geliştirme Noktaları ve Riskler

Bu liste kod okunarak çıkarıldı. Her madde için kanıt (dosya/fonksiyon), etki ve öneri verilmiştir. Bazı maddeler
çalışma ortamına bağlıdır. Onlar için "nasıl doğrulanır" adımı var; üretimde doğrulamadan iş kalemine
çevirmeyin. Öncelik, uyum riski (bir düzenlemenin kaçırılması veya gecikmesi) üzerinden verilmiştir.

Özet:

| # | Konu | Alan | Öncelik |
|---|---|---|---|
| 1 | CI'da testler çalışmıyor | Süreç | **Yüksek** |
| 2 | Yeniden başlatma sonrası bayat kilitler ve sahipsiz talepler | Crawler | **Yüksek** |
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
| 16 | Küçük harfli kaynak koduyla tarama talebi başarısız olur | API/Crawler | Düşük |
| 17 | Yönlendirmeler host izin listesine tabi değil | Güvenlik | Orta |
| 18 | Aynı içerikli iki ek taramayı yarıda keser | Crawler | Orta |
| 19 | Yalnızca `FetchError` yakalanıyor; diğer hatalar taramayı keser | Crawler | Orta |
| 20 | Tükenmiş hatalı kayıtlar güvenlik ağı görevlerini tıkayabilir | Core | Düşük |
| 21 | OCR kesintisi belgeyi kalıcı başarısız yapıyor | Core | Orta |
| 22 | LLM kesintisi deneme hakkını tüketiyor | Core | Orta |
| 23 | Aynı kayıt iki işçi tarafından eşzamanlı işlenebiliyor | Core | Orta |
| 24 | Önem kuralları kırpılmış metne uygulanıyor | Core | Orta |
| 25 | Birim kararı few-shot havuzunu kirletiyor; süreç haritası yanlış anlatıyor | Core | Düşük |

---

## 1. CI'da testler çalışmıyor — Yüksek

- **Kanıt:** `mevzuat-core/Jenkinsfile` ve `mevzuat-crawler/Jenkinsfile` aşamaları: Docker Build → Docker Deploy
  (Nexus'a push) → SonarQube. Test aşaması yok. `kurum-devreye-alma.md` §2 testi "isteğe bağlı" olarak tanımlıyor.
  `.dockerignore` testleri imaja almıyor.
- **Etki:** Kırık bir `sources.yaml` (ör. geçersiz cron, yanlış seçici) veya kod hatası doğrudan `latest` imajına,
  oradan DMZ'ye gidebilir. Replay testleri ağ gerektirmediği için CI'da rahatça çalışır.
- **Öneri:** Push'tan önce bir test aşaması ekleyin: `make test` eşdeğeri (`pytest -q` + `tools/shared.py check` +
  `ruff check`). Mongo testleri için pipeline'da `mongo:7` servis konteyneri.

## 2. Yeniden başlatma sonrası bayat kilitler ve sahipsiz talepler — Yüksek

İki ayrı sorun var:

**a) Bayat kilit (host adından bağımsız, kesin):**

- **Kanıt:** `mongo.acquire_lock` kilidi yalnızca `{_id, expires_at < şimdi}` filtresiyle alır, sahibine
  bakmaz. TTL 3600 sn ve tarama boyunca yenilenmez.
- **Etki:** Tarama sırasında süreç ölürse (güncelleme, healthcheck `kill`, sunucu yeniden başlatma) kilit
  bırakılmaz. Yeni süreç, host adı aynı olsa bile, o kaynağı kilit süresi dolana kadar (≤1 sa) "başka bir tarama
  sürüyor" diye atlar. BDDK gibi saatlik bir kaynakta bir tur kaçar. Kesilen `fetch_runs` kaydı `running`'de kalır.

**b) Sahipsiz talep (host adı değişiyorsa):**

- **Kanıt:** Süreç kimliği host adıdır (`mongo.worker_id()` → `socket.gethostname()`). Açılışta yalnızca **aynı
  host adına** ait `running` talepler geri alınır (`requeue_orphaned_requests`: `claimed_by == owner`). Quadlet
  dosyasında `HostName=` yok. Podman'da varsayılan host adı konteyner kimliğidir ve Quadlet her başlatmada
  konteyneri yeniden oluşturur. Kubernetes'te yeni pod yeni ad alır.
- **Etki:** O an işlenen "şimdi tara"/backfill talebi sonsuza dek `running` kalır. İzleme yalnızca
  `pending > 30 dk` ve `failed` talepleri raporladığı için (`monitoring/crawler.py`) alarm da üretilmez. Host adı
  sabit olsa bile talep geri alınır ama (a) yüzünden hemen `SourceBusy` ile `failed` olur.
- **Nasıl doğrulanır:** DMZ'de `podman exec mevzuat-crawler hostname` → `systemctl restart mevzuat-crawler` →
  tekrar `hostname`. Mongo'da 05 §4.1'deki "takılı kalmış" sorguları.

**Öneri:** Crawler tek örnek olduğu için açılışta (1) tüm `collect:*` kilitlerini bırakın, (2) tüm `running`
talepleri `pending`'e geri alın, (3) eski `running` taramaları `failed (interrupted)` işaretleyin. Ek olarak
Quadlet'e `HostName=mevzuat-crawler` ekleyin, uzun işlerde kilidi periyodik yenileyin ve izlemeye "1 saatten uzun
`running` talep" kontrolü ekleyin.

## 3. Uzun backfill sırasında aynı kaynağın zamanlanmış taramaları atlanır — Orta

- **Kanıt:** Backfill talebi `running[kod]`'a yazılır. Bu sürede o kaynağın tüm zamanlanmış tetiklemeleri
  "önceki tarama sürüyor" diye atlanır (`CrawlerService.tick`). RG backfill'i gün gün, her gün için detaylar
  dahil tam tarama yapar (`monitoring/reprocess.py:backfill`). Sayfalar arası 3 sn beklenir. API 62 gün farka kadar
  aralık kabul eder, `mevzuat-monitor backfill`'de sınır yoktur.
- **Etki (tahmin):** Uzun bir RG backfill'i saatler sürebilir. Bu sürede **günün Resmî Gazetesi taranmaz.**
  Ayrıca 1 saatlik kilit TTL'i backfill sırasında dolar. Aynı süreçte koruma `running` ile sürse de süreçler
  arası koruma kalkar.
- **Öneri:** Backfill'i gün başına ayrı taleplere bölün (zamanlanmış taramalar araya girebilsin) veya backfill'i
  ayrı bir kilit/iş türü olarak ele alın. Uzun işlerde kilidi periyodik yenileyin.

## 4. Durdurma süresi uzun taramalardan kısa — Orta

- **Kanıt:** SIGTERM'de süren taramalar **ve havuz kuyruğunda bekleyen işler** beklenir
  (`pool.shutdown(wait=True)`, `cancel_futures` yok). K8s `terminationGracePeriodSeconds: 150`, compose
  `stop_grace_period: 2m`. Quadlet'te `TimeoutStopSec=150` yalnızca systemd'nin sınırıdır; konteyner podman
  tarafından durdurulur ve unit'te `StopTimeout=` olmadığı için podman'ın varsayılan durdurma süresi (10 sn)
  geçerli olabilir. İlk kurulumda (200 detay × 1,5–3 sn) veya yeni RG sayısında tarama bu süreleri aşar. Tarama
  döngüsü durdurma bayrağını kontrol etmiyor.
- **Nasıl doğrulanır:** `systemctl cat mevzuat-crawler` (üretilen `ExecStop`) ve
  `podman inspect mevzuat-crawler --format '{{.Config.StopTimeout}}'`.
- **Etki:** Güncelleme anında tarama yarıda kesilir ve madde 2'deki durumlar oluşur. Veri tutarlılığı korunur:
  yazılan sürümler kalır, sonraki tarama devam eder.
- **Öneri:** Quadlet'e `StopTimeout=140` ekleyin. `_store_items` döngüsünde durdurma bayrağına bakıp temiz çıkış
  yapın (taramayı `partial` kapat, kilidi bırak). Kapanışta `shutdown(cancel_futures=True)` kullanın. Güncellemeleri
  tarama pencereleri dışına planlayın (05 §4.1'deki `next_due`).

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
- **İkinci, daha güçlü yol:** Bir kanalın **ilk** taraması hatasız ama boş liste dönerse (`deferred=0`)
  `baseline_complete=True` olur ve hiçbir şey arşivlenmemiştir. Sonraki taramada sitedeki tüm birikim
  baseline olmayan "yeni" öğe olarak YZ hattına girer (ör. yeni eklenen bir kanalın seçicisi ilk gün hatalıysa).
- **Öneri:** Baseline modunda detay hatalarını da "ertelenen" sayın. Boş listeyle baseline'ı kapatmayın. Ya da öğenin liste tarihini kanalın ilk
  tarama tarihiyle karşılaştırarak eski içeriği baseline'a alın.

## 7. Sürekli hata veren detaylar her taramada yeniden deneniyor — Düşük

- **Kanıt:** Detayı 404 veren bir öğe hiç kaydedilmediği için (`prev is None`) her taramada yeniden indirilmeye
  çalışılır. Hem istek hem detay bütçesi harcar, aynı uyarı her taramada tekrar yazılır.
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
  bekleyen aktarım ve LLM p95 gösterilsin. "Şimdi tara" ve backfill aynı panele düğme olarak eklensin.
- **Durum:** Yönetim sayfasına eklendi. Üstüne **Taramalar ve işler** bölümü geldi: etkin taramalar, crawler kuyruğu,
  tarama geçmişi ve arka plan işleri (`GET /api/v1/admin/scans`, `job_run`). Yerel kurulumda `running` kalan tarama
  kayıtları artık `services/jobs.sweep_stale` ile kapatılır (§19'daki yerel mod boşluğu; uzak modu crawler zaten temizler).
  **Kalan:** Resmî Gazete backfill'i `fetch_run.started_at`'a gerçek başlangıcı değil tarama gününü yazar; gerçek süre
  için ayrı bir `scan_date` alanı (ortak runner/store ve Mongo sözleşmesi) gerekir.
- **Durum:** Portaldaki "Yönetim" sayfası ve `GET /api/v1/admin/metrics` bunu karşılıyor (08 §8); istek sayısı dâhil.
  Prometheus ucu ve webhook bağlantısı hâlâ açık.

## 11. Ara sertifika son kullanma tarihleri — Orta (takvimli)

- **Kanıt:** `config/certs/geotrust-tls-rsa-ca-g1.pem` (Resmî Gazete ve mevzuat.gov.tr; ortak sertifika) **2027-11-02**,
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

## 16. Küçük harfli kaynak koduyla tarama talebi başarısız olur — Düşük

- **Kanıt:** API kaynağı büyük/küçük harf duyarsız doğrular (`SourcesFile.get`), ama talebi yazıldığı gibi
  Mongo'ya bırakır (`api/main.py:admin_run_source`, `admin_backfill`). Crawler `source_code`'u duyarlı karşılaştırır
  (`CrawlerService.tick`: `req["source_code"] not in self.sources`).
- **Etki:** `POST /api/v1/admin/sources/bddk/run` 202 döner, crawler talebi "kaynak tanımlı değil" diye `failed`
  yapar.
- **Öneri:** API'de kodu kanonik hale getirin (`load_sources(...).get(code).code`).

## 17. Yönlendirmeler host izin listesine tabi değil — Orta

- **Kanıt:** İzin listesi yalnızca istenen URL'de denetlenir (`Fetcher.get`). httpx (`follow_redirects=True`) ve
  curl_cffi (`allow_redirects=True`) yönlendirmeleri denetimsiz izler.
- **Etki:** İzinli bir sitedeki açık yönlendirme veya ele geçirilmiş bir sayfa, crawler'ı DMZ'den başka bir
  host'a götürebilir. İzin listesinin güvenlik vaadi zayıflar.
- **Öneri:** Yönlendirmeleri elle izleyin (`follow_redirects=False` + döngü) ve her adımda `host_allowed`
  denetleyin, ya da en azından `final_url` host'unu denetleyip reddedin.

## 18. Aynı içerikli iki ek taramayı yarıda keser — Orta

- **Kanıt:** Ek kimliği `"<ana_id>#<sha256[:12]>"`, sürümü ana belgeninkiyle aynıdır (`runner._store_items`).
  Detay sayfasında farklı URL'lerden aynı dosyaya iki bağlantı varsa iki ek aynı `(source_code, external_id,
  version)` üçlüsünü alır. Bu üçlü tekil indekslidir, `insert_many` `BulkWriteError` verir
  (`MongoCrawlStore.add_version`). Ana sürüm o noktada zaten yazılmıştır.
- **Etki:** Tarama yarıda kesilir (madde 19). Sonraki taramada ana belge "aynı" görüldüğü için o sürümün ekleri
  kalıcı olarak kaybolur. Ayrıca yeni sürüm yazıldığında eski sürümün ekleri `is_latest=True` kalır.
- **Öneri:** Ekleri içerik hash'ine göre tekilleştirin. Yeni sürümde eski eklerin `is_latest`'ini de düşürün.

## 19. Yalnızca `FetchError` yakalanıyor; diğer hatalar taramayı keser — Orta

- **Kanıt:** `SourceCollector.run` yalnızca liste aşamasını try içine alır. Detayda yalnızca `FetchError`
  yakalanır. Sarmalanmayan httpx hataları (`RemoteProtocolError`, `TooManyRedirects` …) ve Mongo hataları
  `_store_items`'tan yükselir.
- **Etki:** Kalan kanallar taranmaz, `finish_run` çağrılmaz, `fetch_runs` kaydı `running` kalır. İzleme
  `running` taramaları dikkate almadığı için `fetch_error` alarmı da oluşmaz.
- **Öneri:** Backend'lerde tüm `httpx.HTTPError`'ları `FetchError`'a sarmalayın. `run()`'da kanal başına
  `_store_items`'ı da try içine alın ve `finish_run`'ı `finally`'de çağırın.

## 20. Tükenmiş hatalı kayıtlar güvenlik ağı görevlerini tıkayabilir — Düşük

- **Kanıt:** `process_pending` ve `classify_pending` adayları id sırasıyla ve limitle seçer. Deneme hakkı tükenmiş
  `EXTRACT_FAILED`/`AI_FAILED` kayıtlar sorguya dahildir, ancak döngüde atlanır (`processing/pipeline.py`,
  `ai/classify.py`).
- **Etki:** Bu kayıtlar limit kadar birikirse Beat'in güvenlik ağı turu daha yeni kayıtlara hiç ulaşmaz. Normal
  akışta `.delay()` zinciri id listesiyle çalıştığı için etkisi yalnızca kaçan tetiklemelerde görülür.
- **Öneri:** Deneme sayısını sorgu filtresine taşıyın (`extra->>'extract_attempts'` veya ayrı sütun).
- **Durum: giderildi.** Deneme sayısı `process_pending`, `classify_pending` ve `summarize_pending` sorgularında
  SQL'de süzülür (`extra["…_attempts"].as_integer()`). Özette metin bekleyen (`summary_blocked`) kayıtlar sona
  alınır. Kayıt başına beklenmeyen istisna toplu işi kesmez: geri alınır, deneme sayılır, loglanır.

## 21. OCR kesintisi belgeyi kalıcı başarısız yapıyor — Orta

- **Kanıt:** `extract_pdf`/`extract_image` `OcrError`'u yakalamıyordu; `extract_document` belgeyi `EXTRACT_FAILED` yapıp
  `extract_attempts`'ı artırıyordu. OCR POST'u ayrıca 429'u hiç yeniden denemiyordu (`processing/ocr.py`).
- **Etki:** Servis birkaç saat kapalı kalırsa taranmış belgeler 3 denemede tükenir ve hiçbir telafi turu onları
  almazdı (`catch_up_ocr` yalnızca `ocr_pending_pages` taşıyan belgeleri seçer).
- **Durum: giderildi.** `OcrError` artık `extract_pdf`/`extract_image` içinde yakalanır: taranmış sayfalar OCR kapalıyken
  kullanılan yolla `ocr_pending_pages`'e yazılır, `ExtractResult.ocr_error` hatayı taşır. Belge metin katmanıyla bağlanır,
  `catch_up_ocr` onu sonraki koşuda yeniden dener. Her OCR hatası `extract_attempts`'ı artırır; `process_max_attempts`
  dolunca telafi turları belgeyi bırakır (sonsuz döngü yok; elle `reprocess` yine çalışır). POST isteği 429'da
  `Retry-After` kadar (en çok 30 sn) bekleyip en çok 3 kez denenir.

## 22. LLM kesintisi deneme hakkını tüketiyor — Orta

- **Kanıt:** `classify_pending`, `summarize_pending` ve `match_pending` döngüleri her `LLMError`'u kayda yazıp
  `ai_attempts`'ı artırıyordu. Bağlantı, zaman aşımı, 5xx ve 429 hataları da `LLMError`'a çevriliyordu.
- **Etki:** 30–60 dakikalık bir vLLM kesintisi, o koşudaki tüm kayıtların 3 deneme hakkını tüketebilir (`ai_failure`
  alarmı + elle `reprocess` gerektirir).
- **Durum: giderildi.** `ai/llm_client.py`'de `LLMUnavailable(LLMError)`: bağlantı, zaman aşımı, 5xx ve 429 hataları
  tenacity tükenince bu sınıfla yükseltilir. Üç döngü bu hatada kaydı geri alır (deneme sayılmaz, durum değişmez),
  `rapor.unavailable=True` yazar ve toplu işi keser; kalanlar sonraki Beat turuna kalır. Geçersiz istek (400) gibi kayda
  ait hatalar eskisi gibi sayılır.

## 23. Aynı kayıt iki işçi tarafından eşzamanlı işlenebiliyor — Orta

- **Kanıt:** Zincir görevi (`.delay(ids)`) ile Beat güvenlik ağı aynı kaydı seçebilir; döngüler kaydı kilitsiz yüklüyordu.
- **Etki:** Aynı kayıt için LLM iki kez çağrılır; özet adımında `uq_summary_version` çakışması kaydı `AI_FAILED` yapardı.
- **Durum: giderildi.** Üç döngüde kayıt `session.get(Regulation, id, with_for_update={"skip_locked": True})` ile
  yüklenir (PostgreSQL; SQLite'ta etkisiz). `None` (başka işçi kilitli) ya da SQL uygunluk koşulunu artık sağlamayan
  kayıt atlanır (`rapor.skipped`). `uq_summary_version` `IntegrityError`'ı "zaten yapıldı" sayılır, hata sayılmaz.
  Not: PostgreSQL'de iki bağlantıyla elle denenmeli (testler kilidi `Session.get` yerine koyarak taklit eder).

## 24. Önem kuralları kırpılmış metne uygulanıyor — Orta

- **Kanıt:** `classify_regulation`, bağlamı `relevance_max_input_chars` (18.000) ile kırpıp `severity.apply_rules`'a da bunu
  veriyordu.
- **Etki:** "Yayımı tarihinde yürürlüğe girer" cümlesi ve kritik kalıplar uzun metnin sonunda kalınca taban kuralı
  tetiklenmiyordu; önem "Orta/Düşük" kalabiliyordu.
- **Durum: giderildi.** Kurallar `regulation_context(session, reg, 10**9)` ile kurulan tam metne uygulanır
  (`severity.assess(..., rules_text=…)`). LLM'e giden kırpılmış bağlam değişmedi. Değerlendirme aracı (`run_eval`) da
  aynı biçimde tam metni kullanır.

## 25. Birim kararı few-shot havuzunu kirletiyor; süreç haritası yanlış anlatıyor — Düşük

- **Kanıt:** `record_unit_decision` her çağrıda yeni `FewshotExample` ekliyordu (önce onay, sonra düzeltme: iki satır
  etkin). `services/pipeline.py` süreç haritası "reddedilenler few-shot olarak geri beslenir" diyordu; gerçekte
  reddetmede yalnızca denetim olayı ve outbox yazılır.
- **Durum: giderildi.** Yeni satırdan önce aynı `regulation_id` ve `task='unit_match'` satırları `active=False` yapılır
  (başka kayıtların örneklerine dokunulmaz). Süreç haritası metni düzeltildi.

---

## Önerilen ilk adımlar (küçükten büyüğe)

1. CI'a test aşaması (madde 1). Bir günlük iş, en yüksek getiri.
2. Açılışta kilit/talep/tarama temizliği, `HostName=` ve `StopTimeout=` (madde 2, 4). Küçük kod değişikliği +
   test.
3. Tarama sağlamlığı: hata sarmalama ve `finish_run` garantisi, ek tekilleştirme (madde 18, 19).
4. `ALERT_WEBHOOK_URL` bağlantısı ve sertifika hatırlatmaları (madde 10, 11).
5. Başkanlıkla tarama SLA'sı görüşmesi (madde 5).
6. Baseline/detay hatası düzeltmesi, detay hata geri çekilmesi ve yönlendirme denetimi (madde 6, 7, 17).
