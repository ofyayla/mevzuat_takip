# 07 — Sunum Rehberi

Projeyi farklı dinleyicilere anlatmak için hazır akışlar, sayılar ve sık sorulan soruların cevapları. Mevcut proje
açılış sunumu depo kökünde: `Mevzuat_Takip_Otomasyonu_Proje_Acilis_Sunumu.pptx`.

## 1. Ezberlenecek sayılar (koddan doğrulanmış)

| Sayı | Değer | Kaynak |
|---|---|---|
| İzlenen kaynak / kanal | **12 kaynak, 36 kanal** | `config/sources.yaml`, `test_sources_replay.py` |
| Haftalık tarama | **≈210** (RG 49, BDDK 65, SPK 17, KVKK 17, MASAK 17, TCMB 15, Ticaret/Rekabet/TKBB 10'ar) | 03 §1 |
| Resmî Gazete | Her gün **7 pencere**, bugün + dün, asıl + 4'e kadar mükerrer | 03 §1, 02 §7 |
| BDDK | Hafta içi **saat başı 08–20** | 03 §1 |
| "Şimdi tara" gecikmesi | Kaynak meşgul değilse ve havuzda yer varsa **≤15 sn** içinde crawler alır | 02 §4 |
| Crawler → portal veritabanı | **≤1 dk** (ingest her dakika) | 04 §2 |
| Crawler durursa alarm | **30–40 dk** içinde `crawler_down` (sinyal eşiği 30 dk, izleme 10 dk'da bir) | 04 §6 |
| Kibar tarama | Host başına istekler arası **1,5 sn** (RG 3 sn), en fazla 3 kaynak paralel | 02 §6 |
| Tekilleştirme eşikleri | ≥0,90 otomatik, 0,70–0,90 LLM onayı | 04 §3.2 |
| İlgililik | 3 örnekli self-consistency, eşik 0,30, düşük güvenliler de portala düşer | 04 §7 |

Doğrulanmamış sayı söylemeyin. Tarama süreleri ve LLM gecikmeleri kurum ortamında ölçülmelidir (05 §4).
"Ölçüyoruz, şu sorguyla görülüyor" demek güven verir.

## 2. Dinleyiciye göre akış

### 2.1 Yönetim / iş birimi (10 dakika)

1. **Sorun:** 9 kurumun sitesi elle takip ediliyor; kaçırma riski ve emek. (1 dk)
2. **Çözüm tek cümle:** "Sistem bu siteleri gün içinde düzenli tarar, yeni düzenlemeyi bulur, bankayla ilgisini ve
   önemini değerlendirir, kaynağa dayalı özet ve ilgili birim önerisiyle uzmanın onayına sunar." (1 dk)
3. **Canlı demo:** portal listesi → bir kayıt detayı (özet cümlelerinin kaynak alıntıları, önem gerekçesi, birim
   önerisi) → onay. (4 dk)
4. **Güvence:** Hiçbir şey otomatik yayımlanmaz, uzman onaylar. Her karar denetim izine yazılır. Kaynak bozulursa
   sistem alarm verir. Ham belgeler silinmez. (2 dk)
5. **Ne kadar hızlı:** Resmî Gazete günde 7 kez, BDDK mesai saatlerinde saatlik. Hafta sonu kapsamı bir karar
   konusudur (06 §5). (1 dk)
6. **Sorular.** (1 dk)

### 2.2 Teknik ekip / mimari kurul (30 dakika)

| Dk | Bölüm | Gösterilecek |
|---|---|---|
| 3 | Kısıt ve topoloji: DMZ'de internet, LAN'a yalnızca Mongo | 01 §2 diyagramı |
| 7 | Crawler: tek süreç, cron, kilitler, talep posta kutusu, canlılık | 02 §1 sıra diyagramı |
| 6 | Değişiklik tespiti: listing_hash → sha256 → sürüm; BASELINE; refresh | 02 §5.3 karar tablosu |
| 3 | Stratejiler ve dayanıklılık: API önceliği, link_pattern, impersonate, TLS zinciri | 02 §6–7 |
| 5 | Core: ingest (rev/synced), işleme, tekilleştirme, YZ, durum makinesi | 04 §1, §4; canlı `/surec` |
| 3 | İzleme: sessiz bozulmaya karşı çok katmanlı kontrol | 04 §6 |
| 3 | Riskler ve yol haritası | 06 özet tablo |

### 2.3 Güvenlik / BT altyapı (15 dakika)

- İnternete çıkan tek bileşen crawler'dır. Yalnızca `allowed_hosts` listesindeki kurum sitelerine istek atar (kodda
  zorlanır; yönlendirmeler bu denetime tabi değildir, bkz. 06 §17).
- DMZ'de LLM/OCR/PostgreSQL/Redis ayarı veya anahtarı **yoktur** (`crawler.env.example`). Konteyner salt okunur,
  `NoNewPrivileges`, root olmayan kullanıcı (uid 10001).
- Mongo en az yetki: crawler kullanıcısı yalnızca `mevzuat_crawl`'a yazar, belge **silemez**. Core kullanıcısı
  belge silemez ve ham içeriği değiştiremez; meta veride yalnızca `synced` yazması kod sözleşmesidir
  (`deploy/mongo/init-users.js`).
- TLS doğrulaması kapatılmaz. Eksik ara sertifikalar AIA'dan indirilip köke karşı doğrulanarak eklenir.
- LLM kurum içi vLLM'dir, veri dışarı çıkmaz. Her çağrı `llm_call`'da izlenir.
- Denetim izi yalnızca eklemeye açıktır, hash zinciriyle korunur (`mevzuat-monitor audit-verify`).

## 3. Demo senaryosu (yerel ortamda prova edin)

Ön hazırlık: 05 §3'teki `docker compose` ortamı. LLM'li demo için `.env`'de LLM ayarlı olmalı. İlk taramayı
demodan önce yapın, çünkü ilk tarama BASELINE'dır ve portala bir şey düşürmez.

1. `http://localhost:8000/surec`: Hattın tamamı ve her durumdaki canlı sayılar. Buradan anlatmaya başlayın.
2. Portal üst şeridi / `GET /api/v1/sources/health`: 9 kaynağın durumu, son başarılı tarama, tolerans.
3. `/docs` üzerinden admin "şimdi tara" (`POST /api/v1/admin/sources/{code}/run`) → `docker compose exec mongo mongosh mevzuat_crawl --eval 'db.crawl_requests.find().sort({created_at:-1}).limit(1)'`
   ile talebin `pending → running → done` geçişini gösterin.
4. `db.fetch_runs.find().sort({started_at:-1}).limit(1)`: Kanal istatistikleri. `unchanged` sayısının yüksek, istek
   sayısının düşük olması "değişiklik yoksa siteleri yormuyoruz" mesajını verir.
5. Bir kayıt detayı: Özet cümlesi → kaynak alıntısı, önem gerekçesi, birim önerisi ve görev tanımı maddesi.
6. Onay → kayıt detayındaki denetim izi (`auditTrail`).

Yedek plan: Ağ veya LLM sorunu olursa §2'deki ağsız alıştırmayı (05 §2.1) canlı çalıştırıp crawler'ın
ayrıştırmasını gösterin.

## 4. Sık sorulan sorular ve kısa cevaplar

**"Ne sıklıkla tarıyorsunuz?"** Kaynağa göre değişir. RG günde 7 kez (gece yarısına yakın pencereler dahil),
BDDK hafta içi saatlik, diğerleri günde 2–3 kez. Takvim kurumların yayın alışkanlıklarına göre seçildi ve
`sources.yaml`'dan kod değişikliği olmadan değişir (03).

**"Bir düzenlemeyi kaçırırsak nasıl anlarız?"** Beş katman var: (1) erişim hatası, (2) kaynağa özgü
sessizlik toleransı (geçmişten öğrenilir, iş günü takvimli), (3) hacim anomalisi, (4) sayfa yapısı imzası ve boş
liste kontrolü, (5) RG ↔ kurum çapraz kontrolü. Paralel çalışma döneminde manuel tespitlerle karşılaştırma raporu
kaçırma oranını ölçer (`/api/v1/evaluation/report`).

**"Site tasarımı değişirse?"** İmza değişikliği erken uyarı verir, boş liste alarm üretir. Düzeltme genellikle
yalnızca `sources.yaml` seçicisidir. Kayıtlı sayfalarla (fixture) test edilip dağıtılır. Mümkün olan yerde HTML
yerine RSS/JSON API kullanıldığı için (TCMB basın duyuruları, SPK mevzuat, MASAK) kırılganlık azdır.

**"Aynı düzenleme hem RG'de hem BDDK'da çıkarsa iki kayıt mı olur?"** Hayır. Karar sayısı, mevzuat.gov.tr
kimliği, URL gibi kesin anahtarlarla, gerekirse başlık+metin benzerliği ve LLM onayıyla tek kayıtta birleşir (04
§3.2).

**"YZ uydurursa (halüsinasyon)?"** Özetteki her cümle kaynak metinden alıntıyla doğrulanır, doğrulanamayan ifade
çıkarılır. İlgililikte tereddüt eden kayıt elenmez, düşük güvenle uzmana gider. Son karar her zaman uzmandadır.

**"İlk gün yüzlerce eski karar portala dolar mı?"** Hayır. İlk taramada sitede duran içerik BASELINE olarak
arşivlenir, portala düşmez (02 §5.4).

**"Crawler çökerse veri kaybolur mu?"** Hayır. Yazılan sürümler kalır. Kaçan tetikleme bir sonraki taramada
toplanır, uzun kesintide backfill yapılır. Ham içerik hiç silinmez, Mongo ve PostgreSQL arasındaki aktarım
idempotenttir.

**"Neden Kafka/Celery değil de Mongo?"** DMZ'den LAN'a açık tek port Mongo'dur. Mongo hem teslim alanı (meta veri
+ GridFS ham içerik) hem talep kuyruğu olarak kullanılır. Yeni bir firewall kuralı gerektirmez.

**"Yeni bir kurum eklemek ne kadar sürer?"** Uygun bir strateji varsa çoğunlukla yalnızca yapılandırma + fixture
+ test (05 §5). Yeni strateji gerekiyorsa küçük bir sınıf.

## 5. Sunumdan önce kendinizi sınayın

Rehberin ana sayfasındaki 15 soruluk "Hakimiyet kontrol listesi"ni koda bakmadan cevaplayın. Takıldığınız soru
sunumda gelecek sorudur.
