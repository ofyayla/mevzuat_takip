# Mevzuat Takip — Backend

Bu aşamada **kaynak toplama altyapısı (İK-1)** hazır: 9 resmi kaynağın (KEP hariç) taranması, yeni/değişen içeriğin
tespiti, ham arşiv ve kaynak bazlı sağlık verisi. Kaynak bazlı keşif bulguları `../docs/kaynak-kesif-raporu.md`
dosyasında, genel mimari `../docs/backend-gelistirme-plani.md` dosyasında.

## Kurulum

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # PostgreSQL için: pip install -e ".[dev,postgres]"
cp .env.example .env               # proxy, CA paketi, veritabanı ayarları
```

Varsayılan veritabanı `backend/data/mevzuat.db` (SQLite), ham arşiv `backend/data/raw/`. Üretimde `DATABASE_URL`
PostgreSQL'i, `REDIS_URL` kurumdaki Redis'i gösterir.

## Komutlar

```bash
mevzuat-collect sources                        # kaynaklar, kanallar, doğrulama durumu
mevzuat-collect check-access                   # 9 kaynağa bağlantı testi (firewall/proxy istisnası sonrası)
mevzuat-collect probe TCMB                     # yalnızca listeyi çeker ve gösterir, DB'ye yazmaz
mevzuat-collect probe BDDK --record tests/fixtures/bddk --with-details   # yanıtları fixture olarak kaydet
mevzuat-collect run all                        # tam tarama: detay + ekler + arşiv + DB
mevzuat-collect run RESMI_GAZETE --max-details 50

celery -A app.worker worker -Q collect -c 4    # zamanlanmış çalışma (REDIS_URL gerekir)
celery -A app.worker beat                      # sources.yaml'daki cron pencereleri
```

## Mimari (toplama katmanı)

```
config/sources.yaml ──► SourceConfig ──► kanal başına Strategy ──► ItemRef listesi
                                                │
         Fetcher (httpx | curl_cffi | Playwright)   ▼
         proxy · CA · host izin listesi ·      SourceCollector.run()
         istek aralığı · retry · Recorder        ├─ liste satırı değişmemiş  → atla (istek yok)
                                                 ├─ yeni/değişmiş            → detay + ekler indir
                                                 ├─ içerik hash'i aynı       → sürüm açma
                                                 ├─ içerik farklı            → version+1, eskisi is_latest=False
                                                 ├─ dış host bağlantısı      → yalnızca liste satırı (JSON)
                                                 └─ kanalın ilk taraması     → processing_status=BASELINE
                                                          │
                          FileSystemStorage (sha256) ◄────┴────► DB: source · fetch_run · raw_document
```

| Strateji | Kullanım | Kaynaklar |
|---|---|---|
| `resmi_gazete` | Fihrist (asıl + mükerrer, bugün + dün) | Resmî Gazete |
| `feed` | RSS/Atom | TCMB basın duyuruları |
| `wordpress_api` | WP REST, `modified_after` ile artımlı | MASAK |
| `css_list` | CSS seçicili liste (tarih, özet, ek alan regex'leri, sayfalama) | SPK, KVKK, Ticaret, Rekabet kararları |
| `link_pattern` | href deseni, başlık üst elemandan, CSS'e bağımsız | TCMB mevzuat, TKBB, Rekabet duyuruları, BDDK |

Yeni bir kaynak/kanal eklemek çoğunlukla yalnızca `sources.yaml` düzenlemesi gerektirir. Önce `probe` ile denenir,
sonra `--record` ile fixture kaydedilir ve `tests/test_sources_replay.py` dosyasına bir satır eklenir.

### İzleme verisi (İK-7 girdisi)

Her `fetch_run` kaydı kanal bazında şunları tutar: öğe sayısı, sayfa sayısı, yeni/değişen/aynı/ertelenen sayıları,
**yapı imzası** (öğe kapsayıcılarının etiket/sınıf yolu özeti) ve hata. HTTP 200 dönen ama beklenen minimum öğeyi
içermeyen liste `structure_alert=True` olur. Bu, "sessiz bozulma" senaryosunun sinyalidir. İmza değişikliği
`structure_changed` olarak işaretlenir (erken uyarı).

## Testler

```bash
pytest                 # ağ gerektirmez
```

- `tests/fixtures/<kaynak>/`: 25.09.2026'da canlı sitelerden `probe --record` ile kaydedilen gerçek yanıtlar.
  Testler bu yanıtları `ReplayFetcher` ile oynatır.
- `tests/test_bddk_synthetic.py`: BDDK geliştirme ortamından erişilemediği için **sentetik** HTML kullanır. Kurum
  ağında gerçek fixture'larla değiştirilecek.

## Bilinen kısıtlar

- **BDDK doğrulanmadı** (yurt dışı IP engeli). `verified: null` olarak işaretli; bkz. keşif raporu.
- Metin çıkarma, OCR ve tekilleştirme (İK-2) sonraki adımdır. Ham belgeler `processing_status=FETCHED` ile bekler.
- Alembic migrasyonları henüz yok; tablolar ilk çalıştırmada `create_all` ile oluşturuluyor.
- KEP adaptörü bu kapsamın dışında (erişim yöntemi belirsiz; plan §6 İK-1).
