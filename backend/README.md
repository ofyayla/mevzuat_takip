# Mevzuat Takip — Backend

Hazır olanlar: **kaynak toplama altyapısı (İK-1)**: 9 resmi kaynağın (KEP hariç) taranması, yeni/değişen içeriğin
tespiti, ham arşiv ve kaynak bazlı sağlık verisi; **belge işleme ve tekilleştirme (İK-2)**: metin çıkarma, OCR,
aynı düzenlemenin farklı kaynaklardaki yayınlarının tek kayda bağlanması; **mevzuat tespiti ve önceliklendirme (İK-3)**:
LLM ile ilgililik, göreli güven skoru ve önem derecesi. Kaynak bazlı keşif bulguları `../docs/kaynak-kesif-raporu.md`
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
mevzuat-collect ca-fetch www.bddk.org.tr       # sunucu TLS ara sertifikasını göndermiyorsa AIA'dan indirip doğrular

celery -A app.worker worker -Q collect -c 4    # zamanlanmış çalışma (REDIS_URL gerekir)
celery -A app.worker worker -Q process -c 2    # İK-2 metin çıkarma + tekilleştirme
celery -A app.worker worker -Q ai -c 2         # İK-3 LLM (GPU yükü: düşük eşzamanlılık)
celery -A app.worker beat                      # sources.yaml'daki cron pencereleri
```

## Mimari (toplama katmanı)

```
config/sources.yaml ──► SourceConfig ──► kanal başına Strategy ──► ItemRef listesi
                                                │
         Fetcher (httpx | curl_cffi | Playwright)   ▼
         proxy · CA + config/certs ·           SourceCollector.run()   (resolvers: mevzuat.gov.tr → kanonik id)
         host izin listesi · istek aralığı ·     ├─ liste satırı değişmemiş  → atla (istek yok)
         retry · Recorder                        │    └─ refresh_after_days doldu → yeniden indir, hash karşılaştır
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
| `resmi_gazete` | Fihrist (asıl + mükerrer, bugün + dün) + Çeşitli İlânlar'dan kurum filtreli ilanlar | Resmî Gazete |
| `feed` | RSS/Atom | TCMB basın duyuruları |
| `wordpress_api` | WP REST, `modified_after` ile artımlı | MASAK |
| `json_api` | JSON liste uç noktası, alan eşlemesi YAML'da | SPK Mevzuat Sistemi |
| `css_list` | CSS seçicili liste (tarih, özet, ek alan regex'leri, sayfalama) | SPK, KVKK, Ticaret, Rekabet kararları |
| `link_pattern` | href deseni, başlık üst elemandan, CSS'e bağımsız; grup başlığı ve ek alanlar | TCMB mevzuat, TKBB, Rekabet duyuruları, BDDK, Ticaret tüketici mevzuatı |

Dış belge sitelerine giden bağlantılar (şimdilik mevzuat.gov.tr) `app/collectors/resolvers.py` ile kanonik kimliğe
çevrilir ve tam metin indirilir; host'un kaynağın `allowed_hosts` listesinde olması gerekir.

Yeni bir kaynak/kanal eklemek çoğunlukla yalnızca `sources.yaml` düzenlemesi gerektirir. Önce `probe` ile denenir,
sonra `--record` ile fixture kaydedilir ve `tests/test_sources_replay.py` dosyasına bir satır eklenir.

### İzleme verisi (İK-7 girdisi)

Her `fetch_run` kaydı kanal bazında şunları tutar: öğe sayısı, sayfa sayısı, yeni/değişen/aynı/ertelenen sayıları,
**yapı imzası** (öğe kapsayıcılarının etiket/sınıf yolu özeti) ve hata. HTTP 200 dönen ama beklenen minimum öğeyi
içermeyen liste `structure_alert=True` olur. Bu, "sessiz bozulma" senaryosunun sinyalidir. İmza değişikliği
`structure_changed` olarak işaretlenir (erken uyarı).

## Belge işleme ve tekilleştirme (İK-2)

```bash
mevzuat-process run                  # bekleyen ham belgeler: metin çıkar → tekil düzenlemeye bağla
mevzuat-process show 42              # düzenleme #42 ve bağlı belgeleri (kaynak, eşleşme yöntemi, skor)
mevzuat-process text 1234            # bir belgenin çıkarılmış metni
mevzuat-process review               # tekilleştirmede onay bekleyen çiftler (skor 0.70–0.90)
mevzuat-process merge 57 42          # onay: #57'yi #42'ye kat
mevzuat-process split 1234           # yanlış birleştirmeyi geri al (belge + ekleri yeni düzenlemeye)
mevzuat-process ocr-check            # OCR servisine bağlantı testi (taranmış örnek PDF üretip gönderir)
mevzuat-process reextract            # OCR bekleyen belgeleri yeniden işle (servis sonradan açıldıysa)
```

Toplama görevi yeni belge bulduğunda işleme görevini tetikler; Beat ayrıca 15 dakikada bir bekleyenleri tarar.

**Durumlar:** `raw_document`: `FETCHED → EXTRACTED → LINKED`, geçici hatada `EXTRACT_FAILED` (3 deneme).
`regulation`: `NEW` (YZ hattına girer, İK-3) veya `BASELINE` (ilk taramada sitede zaten duran içerik; arşivlenir ve
tekilleştirmeye katılır, YZ'ye gitmez), birleştirilmiş kayıtlar `MERGED`.

**Metin çıkarma:**

| İçerik | Yöntem |
|---|---|
| HTML | Kanalın `content_selector`'ı (RG: `body`, BDDK: `#content-container`), yoksa trafilatura; blok elemanlarında satır kırılır, "MADDE 1-" ve "…Kurumundan:" satırları korunur |
| PDF | PyMuPDF metin katmanı. Taranmış sayfalar (metin < 40 karakter, ya da < 250 karakter ve sayfanın ≥ %25'i görüntü, ya da metin katmanı çöp) yalnızca o sayfalar olarak OCR'a gönderilir |
| DOCX / görüntü / liste satırı | python-docx / OCR / başlık + özet + ek alanlar |
| .doc, .xlsx vb. | Desteklenmiyor: metin boş, belge başlığıyla bağlanır (hata sayılmaz) |

**OCR:** kurumdaki Azure Document Intelligence "Read" (`prebuilt-read`) servisi. `.env`: `OCR_PROVIDER=azure_di`,
`OCR_ENDPOINT`, gerekiyorsa `OCR_API_KEY`. Servis v3 kurulumuysa `OCR_API_VERSION=2023-07-31` ve
`OCR_PATH_PREFIX=formrecognizer`. OCR kapalıyken taranmış sayfalar `extra.ocr_pending_pages` ile işaretlenir; ilk
taramadaki eski belgeler varsayılan olarak OCR'a gönderilmez (`OCR_BASELINE=false`).

**Tekilleştirme** (`app/processing/dedupe.py`):
1. Aynı kaynak kimliğinin yeni sürümü → aynı düzenleme (`same_document`, `extra.content_updates`); ek → ana belgesi.
2. Güçlü anahtar: kanonik URL, mevzuat.gov.tr kimliği, `karar:<kurum>:<sayı>`, `no:<kurum>:<tür>:<sıra no>`.
   Örn. BDDK "(18.09.2026 - 11572) …" = RG "…Kurulunun 18/09/2026 Tarihli ve 11572 Sayılı Kararı".
3. Zayıf anahtar (başlık + kurum) ve bulanık skor yalnızca **farklı kaynaklar** arasında: aynı kaynak her ay aynı
   başlıklı duyuru yayımlayabiliyor. Kurumları bilinen ve farklı iki kayıt aday olmaz.
4. Skor = 0.6·başlık + 0.3·metin + 0.1·kurum; ±7 gün. ≥0.90 otomatik; 0.70–0.90 `confirmer` (LLM, İK-3'te) yoksa
   yeni kayıt + `needs_dedupe_review`; <0.70 yeni.
5. Resmî yayım tarihi RG tarihidir (RG kopyasından ya da kurum belgesindeki "(… tarih ve 33375 sayılı Resmî
   Gazete'de yayımlanmıştır.)" atfından).

## Mevzuat tespiti ve önceliklendirme (İK-3)

```bash
mevzuat-ai llm-check                         # LLM bağlantısı ve şemalı çıktı testi
mevzuat-ai classify                          # NEW düzenlemeleri sınıflandır (--id N --force: yeniden)
mevzuat-ai show 42                           # karar, skor bileşenleri, oylar, uygulanan önem kuralları
mevzuat-ai calls                             # son LLM çağrıları; görev bazında gecikme/token
mevzuat-ai setting relevance_threshold 0.35  # çalışma zamanı eşiği (app_setting; kod dağıtımı gerekmez)
mevzuat-ai eval tests/eval/relevance_v0.jsonl  # recall / precision / eşik taraması / önem isabeti
```

`.env`: yerelde `LLM_PROVIDER=openai` + `LLM_API_KEY`; kurumda `LLM_PROVIDER=vllm`,
`LLM_BASE_URL=http://10.144.100.204:8806/v1`, `LLM_MODEL=Qwen/Qwen3.6-35B-A3B-FP8`. İşleme görevi yeni düzenleme
açtığında sınıflandırma görevini tetikler; Beat ayrıca saatte iki kez bekleyenleri tarar.

**Akış:** `NEW → RELEVANT` (portala düşer, İK-4 özetler) / `IRRELEVANT` (silinmez) / `AI_FAILED` (3 deneme).
`BASELINE` ve `MERGED` kayıtlar sınıflandırılmaz.

**Güven skoru** (model eğitimi yok, kalibre olasılık yerine göreli skor):
`0.6·model skoru (3 örneğin ortalaması) + 0.3·"ilgili" oy oranı (self-consistency) + 0.1·kural sinyali`
(kaynak/kurum önceliği + "bankalar", "katılım bankaları" … anahtar kelimeleri). Bant: ≥0.75 Yüksek, ≥0.45 Orta.
**Yüksek duyarlılık:** skor eşik (0.30) altında olsa bile örneklerin çoğunluğu tereddüt bildirdiyse veya oylar
bölündüyse kayıt portala düşer, güven "Düşük" gösterilir. Metni çıkarılamamış belgede (OCR bekleyen) eşiğin yarısı uygulanır.

**Önem:** LLM taksonomideki tanımlara göre önerir, sonra Başkanlık kuralları uygulanır: kritik kalıplar (idari para
cezası, sermaye yeterliliği, zorunlu karşılık …) veya yayım tarihinde yürürlüğe girip bankaları muhatap alan
düzenleme → en az "Yüksek"; Duyuru/Basın Duyurusu/Bülten/İlan → en fazla "Orta" (tavan, tabandan önceliklidir).

**Bilgi tabanı (v0 TASLAK, çalıştay çıktısıyla değiştirilecek):** `config/taxonomy.yaml` (kurum profili, 14 konu,
açıkça ilgisiz sınıflar, önem tanımları ve kuralları), `config/labeling_guide.md`, `config/fewshot_relevance.yaml`.
Bu dosyalar değişince prompt sürümü değişir; önbellek eski kriterlerle üretilmiş sonucu kullanmaz.

**LLM katmanı** (`app/ai/llm_client.py`): OpenAI ve vLLM için tek istemci; `json_schema` (strict) ile yapılandırılmış
çıktı, Qwen `<think>` / `reasoning_content` ayrıştırma, görev bazlı thinking (`LLM_THINKING_TASKS`), bir kez şema
onarma, geçici hatalarda retry. Her çağrı `llm_call` tablosunda (görev, prompt sürümü, gecikme, token, durum); aynı
girdi için önbellek. Prompt'lar `app/ai/prompts/*.v1.*.j2`; kaynak metin `<kaynak_metin>` içinde verilir.
İK-2'deki tekilleştirmenin belirsiz bandı (0.70–0.90) LLM varsa artık ona sorulur (`dedupe_confirm`, thinking kapalı).

**Değerlendirme seti** `tests/eval/relevance_v0.jsonl`: 18–25.09.2026 canlı verisinden 55 kayıt, **taslak etiketler**
(Başkanlık gözden geçirmeli). gpt-4.1-mini ile ölçüm (25.09.2026): recall 1.0, precision 0.96–1.0 (örnekleme
değişkenliği), önem ±1 isabeti 1.0. Aynı penceredeki 212 düzenlemenin tamamı: 120 ilgili / 92 ilgisiz; RG'nin 77
maddesinden 11'i ilgili.
Küçük ve yalnızca açık vakalardan oluşan bu set bir kalibrasyon değil, hattın doğru çalıştığının kanıtıdır; eşik
paralel çalışmada Başkanlık etiketleriyle ayarlanacak (İK-8).

## Testler

```bash
pytest                 # ağ gerektirmez
```

- `tests/fixtures/<kaynak>/`: 25.09.2026'da canlı sitelerden `probe --record` ile kaydedilen gerçek yanıtlar.
  Testler bu yanıtları `ReplayFetcher` ile oynatır.
- BDDK fixture'ları Türkiye'den kaydedildi (yurt dışı IP'lerinden erişilemiyor). Fixture boyutunu makul tutmak için
  kanal başına tek detay saklandı, 300 KB üzeri ekler çıkarıldı.

## TLS: eksik ara sertifika

BDDK, Resmî Gazete ve mevzuat.gov.tr (Türkiye'den bağlanıldığında) TLS ara sertifikasını göndermiyor. Tarayıcılar
bunu fark ettirmez; curl/httpx `unable to get local issuer certificate` verir. `config/certs/*.pem` dosyaları kök
deposuna (`CA_BUNDLE` veya certifi) eklenir. Sertifika yenilendiğinde `check-access` hatayı gösterir ve
`mevzuat-collect ca-fetch <host>` önerir; komut yeni ara sertifikayı AIA adresinden indirir, zinciri köke karşı
doğrular ve ancak ondan sonra yazar. Doğrulama hiçbir durumda kapatılmaz.

## Bilinen kısıtlar

- Alembic migrasyonları henüz yok; tablolar ilk çalıştırmada `create_all` ile oluşturuluyor. `create_all` mevcut
  tabloya sütun eklemez: İK-1 döneminde oluşturulmuş bir yerel veritabanı İK-2 ile kullanılamaz (silinip yeniden
  oluşturulmalı). Üretim kurulumundan önce migrasyon altyapısı eklenmeli.
- LLM ayarlı değilse tekilleştirmenin belirsiz bandı (0.70–0.90) ayrı düzenleme olarak açılır ve
  `mevzuat-process review` ile listelenir.
- Taksonomi, etiketleme kılavuzu ve değerlendirme etiketleri çalıştay öncesi taslaktır. Kurum içi vLLM/Qwen ile henüz
  denenmedi (geliştirme ortamı kurum ağı dışında); `mevzuat-ai llm-check` ve `eval` kurumda çalıştırılmalı.
- KEP adaptörü bu kapsamın dışında (erişim yöntemi belirsiz; plan §6 İK-1).
- BDDK yurt dışı IP'lerinden erişilemiyor; bulut CI'da canlı test çalıştırılamaz (replay testleri ağ gerektirmez).
