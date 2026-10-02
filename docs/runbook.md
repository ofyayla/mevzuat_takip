# Mevzuat Takip — İşletim Kılavuzu (Runbook)

Hedef kitle: sistemi kuran ve işleten ekip (BT operasyon + Mevzuat ve Uyum Başkanlığı sistem sorumlusu).
Komutlar `backend/` dizininden, ilgili sanal ortam veya konteyner içinde çalıştırılır
(`docker compose … exec api <komut>`).

## 1. Bileşenler

Kurum topolojisi: internete çıkan tek bileşen DMZ'deki crawler'dır ve LAN'a yalnızca MongoDB (27017) üzerinden
erişir. Ana servis LAN'daki Kubernetes cluster'ındadır. İki ayrı repo: **mevzuat-core** (bu belgeler, `deploy/`) ve
**mevzuat-crawler** (`deploy/dmz`); chart'lar kurum chart reposunda `ai-uat-charts/mevzuat-{core,crawler}`.

| Bileşen | Yer | Görev | Ölçek |
|---|---|---|---|
| `mevzuat-crawler` | DMZ, 192.168.18.35 (podman) | Kaynak tarama; zamanlama `sources.yaml` cron; LAN tarama talepleri | Tek örnek, paralel 3 kaynak |
| MongoDB `mevzuat_crawl` | LAN, 10.155.7.195 | DMZ ↔ LAN teslim alanı: meta veri + ham içerik (GridFS) | Kurum Mongo'su |
| `mevzuat-core` | K8s (Helm) | Portal (GUI) + Portal API; kullanıcılar ve Albatros 443 ile gelir | 2 replika |
| `mevzuat-core-worker` | K8s (Helm) | Celery: ingest (Mongo → PostgreSQL), metin çıkarma/OCR, tekilleştirme, YZ, izleme | 1 replika, tüm kuyruklar |
| `mevzuat-core-beat` | K8s (Helm) | Celery zamanlayıcısı (ingest her dakika, işleme/YZ/izleme) | **Tek** örnek (Recreate) |
| `mevzuat-core-migrate` | K8s Job (ArgoCD PreSync) | `alembic upgrade head`, her senkronda pod'lardan önce | — |
| Redis | K8s (`redis-master.artint.svc`) | Celery tetikleyicisi; paylaşımlı olduğundan ayrı DB numarası. Kalıcılık/yedek gerekmez | — |
| PostgreSQL 16 | LAN | Tüm iş durumu: kayıtlar, özetler, denetim izi, alarmlar | Tek örnek + günlük yedek |

Ham içerik GridFS'te tutulur ve **silinmez** (yeniden işleme ve denetim bunun üzerinden yapılır); crawler'ın Mongo
kullanıcısının silme yetkisi yoktur.

**Ölçekleme (ör. KEP, yeni kaynaklar):** yük hangi kuyrukta birikiyorsa values dosyasında `extraWorkers` ile
o kuyruğa ayrılmış worker eklenir (ör. `{name: mevzuat-core-worker-ai, queues: ai, …}`) ve kuyruk `worker.queues`
listesinden çıkarılır; kod değişmez. YZ kuyruğunda darboğaz vLLM eşzamanlılığıdır (`LLM_MAX_CONCURRENCY`); worker sayısını artırmak tek başına
hızlandırmaz. Yeni site yalnızca `config/sources.yaml` + crawler imajı güncellemesiyle eklenir.

## 2. Kurulum

İlk kez kurum ağına taşıma ve devreye alma (repolar, Azure DevOps, ArgoCD, DMZ, sıra ve kontroller): `kurum-devreye-alma.md`.

**MongoDB (bir kez, Mongo yöneticisi):** `deploy/mongo/init-users.js` iki en az yetkili kullanıcı oluşturur:
`mevzuat_crawler` (yalnızca `mevzuat_crawl`'a yazma, silme yok) ve `mevzuat_core` (okuma, senkron işareti,
tarama talebi). Parolalar kasaya konur.

**LAN / Kubernetes (ana servis — chart reposu + ArgoCD):**

1. İmajlar Azure DevOps pipeline'larında derlenip Nexus'a gönderilir: `com.albaraka.ai/mevzuat-core` ve
   `com.albaraka.ai/mevzuat-crawler`; `main` → `latest` (UAT), diğer dallar → `dev`.
2. Gizli değerler: `deploy/secret.example.yaml` → `secret.yaml` (`DATABASE_URL`, `MONGO_URL` = `mevzuat_core`,
   `LLM_API_KEY`, `OCR_API_KEY`); repoya **girmez**, `kubectl -n artint apply -f secret.yaml` veya Vault'tan.
   Gizli değer values dosyalarına yazılmaz.
3. Chart kurum chart reposunda `ai-uat-charts/mevzuat-core/`; ortam değerleri `values-albaraka-{dev,uat}.yaml`.
   ArgoCD senkronunda önce migrate Job'ı (PreSync) şemayı günceller, ardından api/worker/beat yenilenir. ArgoCD dışı
   acil kurulum (chart reposunda): `helm upgrade --install mevzuat-core mevzuat-core -n artint -f mevzuat-core/values-albaraka-uat.yaml`.
4. Başlangıç verisi: `kubectl -n artint exec deploy/mevzuat-core -- mevzuat-ai units-load`.
5. LLM ve OCR testi: `… exec deploy/mevzuat-core -- mevzuat-ai llm-check` ve `mevzuat-process ocr-check`.

**DMZ (crawler):**

6. İmaj: Nexus'taki `com.albaraka.ai/mevzuat-crawler`. DMZ'den Nexus'a (9099) erişim yoksa (ağ şemasında
   yalnızca 27017 açık) imaj LAN'da `podman save` ile dosyaya alınıp DMZ'ye kopyalanır ve `podman load` ile yüklenir.
7. mevzuat-crawler reposundan: `deploy/dmz/crawler.env.example` → `/etc/mevzuat/crawler.env` (600; `MONGO_URL` =
   `mevzuat_crawler`, gerekiyorsa `HTTPS_PROXY`/`CA_BUNDLE`). `deploy/dmz/mevzuat-crawler.container` → `/etc/containers/systemd/`,
   `systemctl daemon-reload && systemctl start mevzuat-crawler`.
8. Erişim testi (DMZ'de): `podman exec mevzuat-crawler mevzuat-collect check-access` → 9 kaynak `ok` (hata → §4.2);
   `podman exec mevzuat-crawler mevzuat-crawler check` → Mongo erişimi ve indeksler.
9. İlk tarama: `podman exec mevzuat-crawler mevzuat-crawler run all`. İlk taramada bulunan eski içerik **taban
   çizgisi** (baseline) sayılır, portala düşmez. Sonrasında crawler zamanlamayla kendiliğinden tarar.
10. Duman testi: portal açılır, `GET /api/v1/sources/health` tüm kaynakları `ok` gösterir (ingest bir dakika içinde
    taramaları aktarır), `mevzuat-monitor audit-verify` `ok` döner.

Yerel deneme (kurum topolojisinin küçük kopyası: crawler + mongo + api/worker/beat + redis + postgres):
`docker compose -f backend/docker/docker-compose.yml up -d --build`. Tek süreçte, Mongo olmadan geliştirme için
`CRAWL_MODE=local` (varsayılan) ile `mevzuat-collect run …` kullanılır.

## 3. Günlük işletim

- **Sağlık:** portal durum çubuğu veya `GET /api/v1/sources/health`. Beat izlemeyi 10 dakikada bir çalıştırır;
  alarmlar `mevzuat-monitor alerts` / `GET /api/v1/admin/alerts`.
- **Kalite:** `mevzuat-process review` (tekilleştirmede belirsiz kalan çiftler),
  `GET /api/v1/evaluation/filtered-out?from&to` (YZ'nin ilgisiz bulduğu kayıtlar — örneklem kontrolü).
- **Denetim izi bütünlüğü:** haftalık `mevzuat-monitor audit-verify` (veya `GET /api/v1/admin/audit/verify`).
  `ok: false` → §4.7.

## 4. Olay müdahalesi

### 4.1 Alarm triyajı

| Alarm | İlk bakılacak | Olası neden → aksiyon |
|---|---|---|
| Crawler çalışmıyor (`crawler_down`) | DMZ'de `systemctl status mevzuat-crawler`, `journalctl -u mevzuat-crawler`; `mevzuat-crawler check` | Konteyner durmuş → başlat; Mongo'ya erişilemiyor (27017 kuralı, parola) → BT ağ / Mongo yöneticisi. Tüm kaynaklar etkilenir; düzelince kaçan dönem §4.4 |
| Aktarım gecikmesi (`ingest_lag`) | `core-worker` ve `core-beat` logları (`ingest_crawl`) | Beat/worker durmuş, PostgreSQL veya Redis erişimi yok → pod'u yeniden başlat. Veri Mongo'da bekler, kaybolmaz |
| Tarama talebi (`crawl_request`) | Mongo `crawl_requests` (`status`, `error`) | Uzun süre `pending` → crawler çalışmıyor; `failed` → hata metnine göre §4.2/§4.3 |
| Erişim hatası (down) | DMZ'de `mevzuat-collect check-access`, crawler logları | Proxy/firewall değişikliği → BT ağ; TLS hatası → §4.2; site bakımda → bekle, sonra §4.4 |
| Sessizlik (delayed) | Kaynağın sitesine tarayıcıyla bak | Gerçekten yayın yok (bayram, tatil) → alarm kendiliğinden kapanır; site yayınlıyor ama sistem görmüyor → §4.3 |
| Hacim düşük/yüksek | Son taramaların `items_listed` değerleri | Liste yapısı değişti → §4.3; toplu yayın (yıl sonu) → bilgi |
| Yapı (boş liste / imza değişti) | `mevzuat-collect probe <KOD>` | Adaptör kırıldı → §4.3 |
| Çapraz kontrol | Alarm ayrıntısındaki RG/kurum kaydı | Kurum henüz yayınlamadı (48 saat) → bekle; kurum kanalı değişti → §4.3 |
| YZ hattı (`AI_FAILED`) | `mevzuat-ai calls` (son LLM çağrıları) | LLM erişilemedi/zaman aşımı → §4.6; şema hatası → prompt sürümü, geliştirici |

Alarm koşul kalktığında otomatik kapanır; elle kapatma yoktur (kök neden giderilmeden kapanmaması için).

### 4.2 TLS: sertifika zinciri hatası

`check-access` çıktısında `unable to get local issuer certificate` ve `ca-fetch` önerisi görülür.

```bash
mevzuat-collect ca-fetch www.bddk.org.tr
```

Komut yeni ara sertifikayı sunucunun AIA adresinden indirir, köke karşı doğrular ve `config/certs/` altına yazar.
Değişikliği depoya işleyip imajı yeniden oluşturun. **TLS doğrulaması hiçbir durumda kapatılmaz.**
Kurum proxy'si SSL denetimi yapıyorsa proxy kök sertifikası `CA_BUNDLE` ile verilir.

### 4.3 Kaynak adaptörü kırıldı (site yapısı değişti)

1. `mevzuat-collect probe <KOD>` — liste gerçekten boş mu, alanlar mı eksik?
2. Değişiklik yalnızca seçici/URL ise `config/sources.yaml` içinde ilgili kanalın `link_selector`,
   `group_selector`, `fields` veya `url` değerini güncelleyin (kod değişmez).
3. Yeni yanıtları fixture olarak kaydedip testi güncelleyin:
   `mevzuat-collect probe <KOD> --record tests/fixtures/<kod> --with-details`, sonra `pytest tests/`.
4. Dağıtımdan sonra kaçırılan dönemi toplayın (§4.4). Resmî Gazete dışındaki kaynaklarda liste güncel içeriği
   gösterdiği için normal tarama yeterlidir; liste eski kayıtları göstermiyorsa eksikler çapraz kontrol ve
   paralel çalışma raporundan izlenir.
5. Kaynak uzun süre onarılamayacaksa `sources.yaml`'da `enabled: false` yapılır ve Başkanlığa yazılı bildirilir
   (o kaynak manuel takibe döner).

### 4.4 Kaçırılan dönemi toplama (backfill)

```bash
mevzuat-monitor backfill RESMI_GAZETE --from 2026-09-01 --to 2026-09-10
```

veya `POST /api/v1/admin/sources/{kod}/backfill {from, to}` (tek istekte ≤62 gün). LAN internete çıkamadığı için
(`CRAWL_MODE=remote`) istek DMZ crawler'a Mongo `crawl_requests` üzerinden iletilir; crawler ≤15 sn içinde alır,
sonuç ingest ile gelir. DMZ'de doğrudan: `podman exec mevzuat-crawler mevzuat-crawler backfill KOD --from … --to …`. Resmî Gazete fihristi gün gün
(asıl + mükerrer) taranır. Gelen içerik "yeni" sayılır, YZ hattından geçer ve portala düşer; tekilleştirme sayesinde
zaten var olan kayıtlar çoğalmaz.

### 4.5 Yeniden işleme (reprocess)

Prompt, taksonomi, birim görev tanımı veya eşik değiştiğinde ya da bir aşama toplu hata verdiğinde:

```bash
mevzuat-monitor reprocess summarize --from 2026-09-01 --to 2026-09-30 [--source BDDK]
```

Aşamalar: `extract` (metin/OCR), `classify` (ilgililik + önem), `summarize`, `match` (birim önerisi).
API: `POST /api/v1/admin/reprocess {stage, from, to, source?}`. Karar verilmiş (onaylı/reddedilmiş) kayıtların kararı ve
uzmanın seçtiği birimler değişmez (yalnızca YZ önerileri yenilenir); yeniden işleme denetim izine "Yeniden işlendi" olarak yazılır. Birim tanımları değiştiyse önce
`mevzuat-ai units-load`, sonra `reprocess match`.

### 4.6 LLM / OCR servisi erişilemiyor

- Kayıtlar kaybolmaz: çağrı başarısız olan kayıt `AI_FAILED` (aşama `extra.ai_stage`) veya `EXTRACT_FAILED` olarak
  bekler, YZ hattı alarmı açılır.
- Servis düzeldikten sonra `mevzuat-monitor reprocess classify|summarize|match --from … --to …` veya OCR için
  `reprocess extract`.
- Sağlayıcı değişikliği (ör. OpenAI → kurum içi vLLM) yalnızca `.env` (`LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`)
  ile yapılır; değişiklikten sonra `make eval` ile değerlendirme seti çalıştırılıp sonuç önceki sürümle
  karşılaştırılır.

### 4.7 Denetim izi doğrulaması başarısız

`audit-verify` her olayın hash'ini yeniden hesaplar ve aynı kaydın önceki olayına bağlılığını kontrol eder. Uygulama olayları
yalnızca ekler (ORM düzeyinde güncelleme/silme engellidir); bu nedenle zincir kırığı veritabanına doğrudan
müdahale anlamına gelir.

1. Çıktıdaki olay kimliklerini ve kayıtları not edin; **veriyi düzeltmeye çalışmayın**.
2. BT güvenlik ekibine olay kaydı açın; veritabanı erişim loglarını ve son yedekle karşılaştırmayı isteyin.
3. "hash yok (zincir öncesi)" uyarısı hata değildir: hash zinciri (migrasyon 0002) öncesinde yazılmış olaylardır.

## 5. Anahtar ve sertifika rotasyonu

| Ne | Nerede | Adımlar |
|---|---|---|
| LLM API anahtarı | `LLM_API_KEY` (kasa) | Yeni anahtarı kasaya/Secret'a yaz → `kubectl -n artint rollout restart deploy/mevzuat-core-worker deploy/mevzuat-core` → `mevzuat-ai llm-check` → eski anahtarı sağlayıcıda iptal et |
| OCR anahtarı | `OCR_API_KEY` | Aynı sıra; kontrol `mevzuat-process ocr-check` |
| Keycloak imza anahtarı | `KEYCLOAK_PUBLIC_KEYS_DIR` | Keycloak'ta yeni anahtar oluşturulunca açık anahtarı (PEM, dosya adı = `kid`) dizine **eski anahtarı silmeden** ekle (api dosya değişikliğini kendisi algılar); eski anahtarla imzalı tokenlar bittikten sonra (token ömrü) eski PEM'i kaldır. Doğrulama çevrimdışıdır; Keycloak'a çalışma anında bağlanılmaz |
| Kaynak TLS ara sertifikası | `config/certs/` | §4.2 |
| Kurum proxy kök sertifikası | `CA_BUNDLE` | Yeni paketi DMZ'ye dağıt, `systemctl restart mevzuat-crawler`, `check-access` |
| Veritabanı parolası | `DATABASE_URL` | PostgreSQL'de parolayı değiştir → kasayı güncelle → tüm servisleri sırayla yeniden başlat |

Bir anahtar sızdıysa (ör. yanlışlıkla sohbet/e-posta ile paylaşıldıysa) önce sağlayıcıda iptal edilir, sonra yenisi
verilir.

## 6. Eşik ayarı (paralel çalışma sonrası)

1. `GET /api/v1/evaluation/report?from&to` → `threshold_sweep` her eşik için kaçırma/gereksiz bildirim sayısını verir.
2. Başkanlıkla seçilen eşik çalışma zamanında uygulanır (yeniden dağıtım gerekmez):
   ```bash
   mevzuat-ai setting relevance_threshold 0.35
   mevzuat-ai setting                  # geçerli değerler
   ```
3. Geçmiş dönemin yeni eşikle portala düşmesi isteniyorsa `mevzuat-monitor reprocess classify --from … --to …`.

## 7. Şema değişikliği (migrasyon)

```bash
alembic upgrade head          # veya: docker compose … run --rm migrate
alembic current               # uygulanmış sürüm
alembic downgrade -1          # geri alma (önce yedek!)
```

Sıra: yedek al → ArgoCD senkronu (migrate PreSync Job'ı önce çalışır; başarısız olursa senkron durur) → api/worker/beat
yeni imajla açılır. Geliştirici yeni model alanı eklediğinde
`alembic revision --autogenerate -m "…"` ile migrasyon üretir; `tests/test_migrations.py` migrasyon sonucu şemanın
modellerle aynı olduğunu doğrular.

## 8. Yedekleme ve geri yükleme

- **Veritabanı:** günlük `pg_dump -Fc` (en az 30 gün saklama) + haftalık geri yükleme denemesi.
- **MongoDB `mevzuat_crawl` (GridFS ham arşiv dahil):** günlük `mongodump --db mevzuat_crawl`. Arşiv kaybolursa
  kayıtlar kalır ama yeniden işleme yapılamaz. Redis yedeklenmez (yalnızca tetikleyici).
- **Yapılandırma:** `config/` depoda; `.env` kasada.
- Geri yükleme: `pg_restore -d mevzuat yedek.dump` → `alembic upgrade head` → `mevzuat-monitor audit-verify` →
  yedek tarihinden bugüne `backfill` (RG) ve normal tarama.

## 9. Kapasite

1 yıllık veri hacmiyle (≈27 bin düzenleme, 4 bin portal kaydı, 9,6 bin tarama) PostgreSQL 16 üzerinde ölçülen
yanıt süreleri (`make load-test`, medyan): liste 14 ms, filtreli liste 12–25 ms, detay 3 ms, kaynak sağlığı 141 ms,
90 günlük paralel çalışma raporu 219 ms. Ayrıntı: `backend/README.md` → "Test, paralel çalışma ve devreye alma".
