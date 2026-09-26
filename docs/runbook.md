# Mevzuat Takip — İşletim Kılavuzu (Runbook)

Hedef kitle: sistemi kuran ve işleten ekip (BT operasyon + Mevzuat ve Uyum Başkanlığı sistem sorumlusu).
Komutlar `backend/` dizininden, ilgili sanal ortam veya konteyner içinde çalıştırılır
(`docker compose … exec api <komut>`).

## 1. Bileşenler

| Bileşen | Görev | Ölçek |
|---|---|---|
| `db` (PostgreSQL 16) | Tüm durum: kayıtlar, özetler, denetim izi, alarmlar | Tek örnek + günlük yedek |
| Redis (kurumdaki) | Celery kuyrukları ve Beat | — |
| `api` (uvicorn) | Portal API + portal sayfası (`/`) | 1–2 örnek |
| `worker-collect` | Kaynak tarama (`collect` kuyruğu) | eşzamanlılık 4 |
| `worker-process` | Metin çıkarma, OCR, tekilleştirme | 2 |
| `worker-ai` | İlgililik, özet, birim önerisi (LLM) | 2 (GPU yüküne göre) |
| `worker-monitor` | Kaynak sağlığı, alarmlar | 1 |
| `beat` | Zamanlayıcı (sources.yaml pencereleri, 10 dk izleme) | **Tek** örnek |
| `migrate` | `alembic upgrade head` (tek seferlik) | — |

Ham arşiv `/data/raw` biriminde tutulur ve **silinmez** (yeniden işleme ve denetim bunun üzerinden yapılır).

## 2. Kurulum

1. `backend/.env.example` → `backend/.env`; en az şunlar doldurulur:
   `DATABASE_URL`, `REDIS_URL`, `RAW_STORAGE_DIR`, `HTTPS_PROXY`/`CA_BUNDLE` (kurum çıkışı),
   `LLM_*`, `OCR_*`, `AUTH_MODE=keycloak` + `KEYCLOAK_*`, isteğe bağlı `ALERT_WEBHOOK_URL`.
   `.env` sürüm kontrolüne **girmez**; anahtarlar kasadan (vault) veya konteyner sırlarından verilir.
2. İmaj: `make docker-build` (depo kökünden `backend/docker/Dockerfile`).
3. Şema: `docker compose -f backend/docker/docker-compose.yml run --rm migrate`
   (üretimde `DB_AUTO_CREATE=false`; şema yalnızca Alembic ile değişir).
4. Başlangıç verisi: `mevzuat-ai units-load` (birim görev tanımları, `config/units.yaml`).
5. Erişim testi: `mevzuat-collect check-access` → 9 kaynak `ok`. Hata varsa §4.2.
6. LLM ve OCR testi: `mevzuat-ai llm-check`, `mevzuat-process ocr-check`.
7. İlk tarama: `mevzuat-collect run all`. İlk taramada bulunan eski içerik **taban çizgisi** (baseline) sayılır,
   portala düşmez. Beat açıldıktan sonra gelen yeni içerik YZ hattına girer.
8. Duman testi: portal `/` açılır, `GET /api/v1/sources/health` tüm kaynakları `ok` gösterir,
   `mevzuat-monitor audit-verify` `ok` döner.

Yerel deneme için: `make compose-up` (Redis'i de ayağa kaldıran `dev` profili).

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
| Erişim hatası (down) | `mevzuat-collect check-access`, worker-collect logları | Proxy/firewall değişikliği → BT ağ; TLS hatası → §4.2; site bakımda → bekle, sonra §4.4 |
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

veya `POST /api/v1/admin/sources/{kod}/backfill {from, to}` (tek istekte ≤62 gün). Resmî Gazete fihristi gün gün
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
| LLM API anahtarı | `LLM_API_KEY` (kasa) | Yeni anahtarı kasaya yaz → worker-ai ve api'yi yeniden başlat → `mevzuat-ai llm-check` → eski anahtarı sağlayıcıda iptal et |
| OCR anahtarı | `OCR_API_KEY` | Aynı sıra; kontrol `mevzuat-process ocr-check` |
| Keycloak imza anahtarı | `KEYCLOAK_PUBLIC_KEYS_DIR` | Keycloak'ta yeni anahtar oluşturulunca açık anahtarı (PEM, dosya adı = `kid`) dizine **eski anahtarı silmeden** ekle (api dosya değişikliğini kendisi algılar); eski anahtarla imzalı tokenlar bittikten sonra (token ömrü) eski PEM'i kaldır. Doğrulama çevrimdışıdır; Keycloak'a çalışma anında bağlanılmaz |
| Kaynak TLS ara sertifikası | `config/certs/` | §4.2 |
| Kurum proxy kök sertifikası | `CA_BUNDLE` | Yeni paketi dağıt, worker-collect'i yeniden başlat, `check-access` |
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

Sıra: yedek al → `migrate` → api/worker'ları yeni imajla başlat. Geliştirici yeni model alanı eklediğinde
`alembic revision --autogenerate -m "…"` ile migrasyon üretir; `tests/test_migrations.py` migrasyon sonucu şemanın
modellerle aynı olduğunu doğrular.

## 8. Yedekleme ve geri yükleme

- **Veritabanı:** günlük `pg_dump -Fc` (en az 30 gün saklama) + haftalık geri yükleme denemesi.
- **Ham arşiv (`/data/raw`):** artımlı dosya yedeği. Arşiv kaybolursa kayıtlar kalır ama yeniden işleme yapılamaz.
- **Yapılandırma:** `config/` depoda; `.env` kasada.
- Geri yükleme: `pg_restore -d mevzuat yedek.dump` → `alembic upgrade head` → `mevzuat-monitor audit-verify` →
  yedek tarihinden bugüne `backfill` (RG) ve normal tarama.

## 9. Kapasite

1 yıllık veri hacmiyle (≈27 bin düzenleme, 4 bin portal kaydı, 9,6 bin tarama) PostgreSQL 16 üzerinde ölçülen
yanıt süreleri (`make load-test`, medyan): liste 14 ms, filtreli liste 12–25 ms, detay 3 ms, kaynak sağlığı 141 ms,
90 günlük paralel çalışma raporu 219 ms. Ayrıntı: `backend/README.md` → "Test, paralel çalışma ve devreye alma".
