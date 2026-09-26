# Mevzuat Takip — Backend

Hazır olanlar: **kaynak toplama altyapısı (İK-1)**: 9 resmi kaynağın (KEP hariç) taranması, yeni/değişen içeriğin
tespiti, ham arşiv ve kaynak bazlı sağlık verisi; **belge işleme ve tekilleştirme (İK-2)**: metin çıkarma, OCR,
aynı düzenlemenin farklı kaynaklardaki yayınlarının tek kayda bağlanması; **mevzuat tespiti ve önceliklendirme (İK-3)**:
LLM ile ilgililik, göreli güven skoru ve önem derecesi; **içerik analizi ve özet (İK-4)**: her ifadesi kaynak
metinden alıntıyla doğrulanan yapılandırılmış özet ve yürürlük tarihi; **birim eşleştirme önerisi (İK-5)**: görev
tanımlarına dayalı, gerekçeli birim önerileri; **Portal API (İK-6)**: liste/detay, onay/red, birim değiştirme,
denetim izi, Keycloak ile çevrimdışı JWT doğrulaması ve API'ye bağlı portal; **sürdürülebilirlik ve izleme (İK-7)**:
kaynak sağlığı, sessizlik/hacim/yapı alarmları, kaynaklar arası çapraz kontrol, geriye dönük toplama ve yeniden işleme. Kaynak bazlı keşif bulguları `../docs/kaynak-kesif-raporu.md`
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
uvicorn app.api.main:app --port 8000          # İK-6 Portal API + portal (http://localhost:8000/)
celery -A app.worker worker -Q monitor -c 1    # İK-7 izleme (Beat: 10 dakikada bir)
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

## İçerik analizi ve özet (İK-4)

```bash
mevzuat-ai summarize                 # RELEVANT düzenlemeleri özetle (--id N --force: yeni sürüm üret)
mevzuat-ai show 42                   # özet, konular, yürürlük, her cümlenin kaynak alıntısı ve karakter aralığı
```

Sınıflandırma ilgili bulduğu düzenlemeler için özet görevini tetikler; Beat saatte iki kez bekleyenleri tarar.
Akış: `RELEVANT → SUMMARIZED` (ya da `AI_FAILED`, `extra.ai_stage=summary`, 3 deneme). Özetler sürümlüdür
(`regulation_summary.version`, `is_current`).

**Çıktı** (portal alanları): `short_content` (2–4 cümle), `relevant_topics` (2–5 kısa konu), yürürlük tarihi
(`regulation.effective_date` + ifade), `source_links` (Resmî Gazete / yayım sayfası / belge dosyası / ekler ayrı
etiketlerle), `grounding_score` (doğrulanan ifade oranı), `unverified_claims` (özetten kaldırılan ifadeler — portal
"doğrulanamayan ifade kaldırıldı" gösterir).

**Kaynağa dayandırma** (`app/ai/grounding.py`): LLM her cümle ve konu için kaynaktan birebir alıntı verir. Alıntı
tüm bağlı belgelerde (RG kopyası, kurum sayfası, ek PDF'ler) aranır: tam eşleşme → kelime dizisi eşleşmesi
(noktalama/boşluk farkı yok sayılır) → bulanık (`partial_ratio ≥ 90`) → atlamalı (alıntının tüm kelimeleri aynı
sırayla, dar bir pencerede; LLM "…" koymadan bent atladığında). Kaynakta olmayan kelime hiçbir yolda kabul
edilmez. Bulunan alıntının orijinal metindeki karakter aralığı kaydedilir. Doğrulanamayan ifade için bir kez geri
bildirimle yeniden üretim yapılır, yine doğrulanamazsa özetten çıkarılır. Hiçbir cümle doğrulanamazsa özet
yayımlanmaz (`AI_FAILED`).

**Yürürlük tarihi:** Kanıt olarak yalnızca yürürlük/uygulama hükmü kabul edilir ("… yürürlüğe girer", "…
uygulanır"); karar veya toplantı tarihi yürürlük sayılmaz. Tarih ifadeden deterministik hesaplanır: açık tarih,
"yayımı tarihinde", "yayımından N gün/ay/yıl sonra" (yayım tarihi RG tarihidir). LLM'in verdiği tarih ifadeyle
tutarsızsa ifade kazanır. LLM hükmü bulamazsa metindeki "…yürürlüğe girer" cümlesi aranır.

**Uzun belgeler:** metin 36.000 karakteri aşarsa map-reduce: ~14.000 karakterlik bölümlerden alıntılı notlar
(alıntıları doğrulanmayan not atılır) → notlardan birleşik özet (alıntılar notlardan kopyalanır, yeniden doğrulanır).

Düzenleme taslakları ("… Taslağı") türü deterministik olarak "Düzenleme Taslağı" olur (önem tavanı "Yüksek").
Kaynak metni 200 karakterden kısa olan (OCR bekleyen taranmış belge vb.) kayıt için LLM çağrılmaz: kayıt `RELEVANT`
kalır, `extra.summary_blocked=metin_yok` işaretlenir ve metin geldiğinde özetlenir.

**Canlı ölçüm** (gpt-4.1-mini, 18–25.09.2026 penceresi, 120 ilgili düzenleme): 105 özet, 14 metin bekliyor, 1
başarısız. Tek parça özet (89) ortalama dayandırma 0.996; map-reduce (16 uzun belge) 0.851 — birleşik özet adımı
zayıf nokta. 857 alıntının %89'u tam, %7'si bulanık, %3'ü kelime dizisi, %0.7'si atlamalı eşleşme. 20 kayıtta
doğrulanmış yürürlük tarihi; 7 kayıtta LLM'in önerdiği tarih ifadeyle doğrulanamadığı için boş bırakıldı.

## Birim eşleştirme önerisi (İK-5)

```bash
mevzuat-ai units-load                # config/units.yaml → unit tablosu (dosyada olmayan birim pasifleşir)
mevzuat-ai units                     # birimler ve görev maddesi sayıları
mevzuat-ai match                     # SUMMARIZED (ve metin bekleyen) düzenlemelere öneri (--force: yenile)
mevzuat-ai show 42                   # öneriler, eşleşen görev maddesi, elenen öneriler ve nedenleri
```

Özet görevi eşleştirmeyi tetikler; Beat saatte iki kez bekleyenleri tarar. Akış: `SUMMARIZED → READY` (portalda
"Bekliyor"). Metni olmadığı için özetlenemeyen kayıt da başlık ve ilgililik gerekçesiyle öneri alır, `RELEVANT` kalır.

**Bilgi tabanı** `config/units.yaml` — **v0 TASLAK**: Albaraka Türk organizasyon şemasından (10.08.2026) 44 birim
(Denetim Komitesine bağlı başkanlıklar, Danışma Komitesine bağlı Katılım Bankacılığı İlkeleri Kontrol ve Uyum
Başkanlığı, Genel Müdüre bağlı 3 müdürlük, 8 GMY altındaki müdürlükler). Birim adları ve bağlılıklar şemadan,
görev tanımları birim adlarından türetilmiş taslaktır; kişi isimleri alınmamıştır. Yönetim Kurulu komiteleri öneri
hedefi değildir. Nihai görev tanımları geldiğinde yalnızca bu dosya güncellenir.

**Eşleştirme** (`app/ai/unit_matching.py`): tüm görev tanımları tek prompt'ta; `unit_code` JSON şemada `enum`
(kapalı liste). Model her öneri için birimin görev maddelerinden birini aynen kopyalar; madde görev tanımında
bulunamazsa öneri elenir. Birimin düzenleme alanları (`regulatory_areas`) İK-3'ün bulduğu konu kodlarıyla hiç
örtüşmüyorsa öneri elenir ("doğru madde, yanlış anlam" hatası). Skoru < 0.30 olan elenir; en fazla 4 öneri.
Hiç öneri kalmazsa varsayılan birim atanmaz ("Birim önerilemedi"). Anahtar kelime ön eşleşmesi (Türkçe eklere
dayanıklı kök eşleşmesi) modele yalnızca ipucu olarak verilir.

**Öğrenme döngüsü:** `record_unit_decision()` (İK-6 portal kararı çağırır) Başkanlığın onayını
(`user_decision`, ağırlık 1) ve düzeltmesini (`user_correction`, ağırlık 2) `fewshot_example`'a yazar; yeni
düzenleme için başlık/konu benzerliğine göre en yakın örnekler prompt'a girer.

**Görev tanımı yazım kuralı:** Her düzenlemeye uyan genel maddeler ("mevzuat değişikliklerinin takibi", "faaliyetlerin
mevzuata uyumunun kontrolü", "mevzuat değişikliklerinin süreçlere yansıtılması") o birimi varsayılan hedef yapar;
canlı denemede Mevzuat ve Uyum ve Süreç İyileştirme birimlerinde görüldü. `tests/test_units.py` bu kalıpları reddeder.

**Canlı ölçüm** (gpt-4.1-mini, 120 ilgili düzenleme): 102'sine öneri (toplam 158), 18'ine önerilemedi (çoğu başka
kuruluşların izin kararları ve metni olmayan kayıtlar), hata yok. Alan kontrolü 21 öneriyi eledi; çoğu zayıf öneriydi,
birkaçı İK-3 konu listesi dar kaldığı için elenen meşru öneri (ör. Yeşil Varlık Oranı → Yatırımcı İlişkileri ve
Sürdürülebilirlik).

## Portal API (İK-6)

```bash
uvicorn app.api.main:app --port 8000      # portal: http://localhost:8000/   API belgesi: /docs
```

| Metot | Yol | Açıklama |
|---|---|---|
| GET | `/api/v1/regulations` | Liste: `source`, `severity`, `status`, `unit`, `date_range` (7/30), `q`, `metric` (`pending`, `critical_high`, `today`, `upcoming`), `page`, `page_size` (≤100). Sıra: önem ↓, yayım tarihi ↓ |
| GET | `/api/v1/regulations/stats` | Metrik kartları (filtrelerden bağımsız) |
| GET | `/api/v1/regulations/{id}` | Detay: özet, kanıtlar, kaldırılan ifadeler, birim önerileri, güven, kaynak bağlantıları, denetim izi, YZ alanları; `ETag` |
| POST | `/api/v1/regulations/{id}/views` | "İncelemeye alındı" (kullanıcı başına bir kez) |
| POST | `/api/v1/regulations/{id}/decision` | `{"decision": "approve"\|"reject", "note"?}`; yalnızca `Bekliyor` (aksi 409); `If-Match` (aksi 412) |
| PUT | `/api/v1/regulations/{id}/units` | `{"unit_codes": [...], "note"?}`; YZ gerekçesi korunur, yeni birim "Uzman tarafından manuel olarak atandı." |
| GET | `/api/v1/units`, `/sources`, `/sources/health`, `/me` | Seçenekler, kaynak sağlığı (durum çubuğu + uyarı şeridi), kullanıcı |
| GET/PUT | `/api/v1/admin/settings` | Çalışma zamanı eşikleri (admin) |
| POST | `/api/v1/admin/regulations/{id}/regenerate`, `/split`, `/admin/sources/{code}/run` | Yeniden üretim, tekilleştirme geri alma, anında tarama (admin) |
| GET | `/healthz`, `/readyz` | Canlılık / hazır olma (DB, LLM, OCR) |

Hata biçimi `{"error": {"code", "message", "details"}}`. Yanıt alanları portalın veri sözleşmesiyle aynı adlardadır
(camelCase). Portalda YZ'nin ilgili bulduğu tüm kayıtlar görünür; analizi tamamlanmamış olanlar (özet/OCR bekleyen,
YZ hatası) da `analysisStatus` ile gösterilir — takılan bir kayıt Başkanlıktan gizlenmez.

**İş kuralları:** Karar verilmiş kayıtta birim değişikliği yapılmaz (409). Onayda aktif birim önerileri
`is_final` olarak dondurulur (Faz 2 yönlendirmesi), birim kararı İK-5 örnek havuzuna yazılır (değişiklik yapılmışsa
düzeltme ağırlığı 2 ile zaten yazılmıştır), `outbox`'a `record.approved`/`record.rejected` olayı düşer. Denetim izi
(`audit_event`) yalnızca eklemeye açıktır (ORM güncelleme/silmeyi reddeder; üretimde DB yetkisiyle de korunmalı).

**Kimlik doğrulama** (plan §8): `AUTH_MODE=disabled` (demo) — kullanıcı `X-Demo-User` başlığından (URL kodlamalı;
HTTP başlıkları Türkçe karakter taşıyamaz) veya `DEMO_USER_NAME`'den, tüm roller açık. `AUTH_MODE=keycloak` —
`Authorization: Bearer <JWT>`; imza `KEYCLOAK_PUBLIC_KEYS_DIR/*.pem` realm açık anahtar(lar)ıyla çevrimdışı doğrulanır
(JWKS çağrısı yok; anahtar rotasyonu için birden fazla `.pem`, token başlığındaki `kid` dosya adıyla eşleşir), `iss`,
`aud`/`azp`, `exp` (±60 sn). Roller: `mevzuat_viewer` (okuma), `mevzuat_expert` (karar, birim), `mevzuat_admin`.

**Portal** (`../Mevzuat Takip Portali.dc.html`): demo verisi (`RAW_RECORDS`, `SOURCE_SCENARIOS`) kaldırıldı; liste,
metrikler, filtre seçenekleri, kaynak sağlığı, detay, onay/red ve birim değiştirme API'ye bağlı. Filtreleme ve
sayfalama sunucu tarafında; arama 300 ms gecikmeyle, eski yanıtlar yok sayılır. Onay/red diyaloğuna not alanı eklendi;
detay açılınca `views` çağrılır; tarihler `tr-TR` biçiminde. Demo sürümündeki "karar verildi" bilgisinin hiç
görünmemesi hatası düzeltildi. Keycloak için sayfa, token döndüren `window.MTP_TOKEN_PROVIDER` (async) tanımlar
(`keycloak-js` entegrasyonu kurum realm bilgisi gelince eklenecek); `window.MTP_API_BASE` API adresini değiştirir.

## Sürdürülebilirlik ve izleme (İK-7)

```bash
mevzuat-monitor check [--dry-run]                                  # kontroller + alarm senkronu
mevzuat-monitor alerts [--all]
mevzuat-monitor backfill RESMI_GAZETE --from 2026-09-01 --to 2026-09-10   # kaçırılan dönemi gün gün topla
mevzuat-monitor reprocess summarize --from 2026-09-01 --to 2026-09-30 [--source BDDK]
```

Yönetim API'si: `GET /api/v1/admin/alerts?status=open|closed|all`, `POST /admin/monitor/run`,
`POST /admin/reprocess {stage, from, to, source?}`, `POST /admin/sources/{kod}/backfill {from, to}` (≤62 gün).

**Kontroller** (`app/monitoring/checks.py`; kaynağın durumu bulguların en ağırından: down > delayed > ok):

| Kontrol | Mantık | Durum |
|---|---|---|
| Erişim hatası | Son 3 tarama başarısız → down; yalnızca son tarama başarısız → delayed | down / delayed |
| Sessizlik | Son **yeni** içerikten (ilk taramadaki eski içerik sayılmaz) beri geçen süre > tolerans. İş günü kaynaklarında hafta sonu, sabit resmî tatiller ve `config/holidays.yaml`'daki dini bayramlar sayılmaz. Tolerans önce `sources.yaml`'dan, 28 gün ve ≥8 yayın aralığı biriktikten sonra tarihsel aralıkların p95'inden (en az 12 saat) | delayed |
| Hacim | Son 7 gün < geçmiş 8 haftanın ortalamasının %20'si → düşük; > ortalama + 3σ → yüksek (uyarı) | delayed / uyarı |
| Yapı | Son taramada 200 dönen liste boş (sessiz bozulma) → delayed; yapı imzası değişti → erken uyarı | delayed / uyarı |
| Çapraz kontrol | RG'de BDDK/SPK/KVKK/MASAK/TCMB adına yayımlanan yönetmelik/tebliğ/karar 48 saatte kurumun kaynağında görülmezse; ya da kurum belgesi "…Resmî Gazete'de yayımlanmıştır" dediği halde RG kopyası yoksa | uyarı |
| YZ hattı | Deneme hakkını tüketmiş `AI_FAILED` kayıt | uyarı |

Rekabet ve Ticaret çapraz kontrol listesinde değil (`CROSS_CHECK_ISSUERS`): RG yayınları (ör. ithalat tebliğleri)
izlenen kanallarda yer almadığı için sürekli yanlış alarm üretir.

**Alarmlar** (`alert` tablosu): anahtarla tekilleşir; koşul sürdükçe güncellenir, kalkınca kapanır, tekrar oluşursa
yeni kayıt açılır. Açılış/kapanış yapılandırılmış log olarak yazılır; `ALERT_WEBHOOK_URL` tanımlıysa JSON POST edilir.
Portal durum çubuğu ve uyarı şeridi (`/sources/health`) aynı hesaptan beslenir.

**Veri kaybı olmaması:** ham içerik silinmez. Resmî Gazete fihristi tarih bazlı olduğu için `backfill` geçmiş günleri
doğrudan tarar (asıl + mükerrer); diğer kaynakların listeleri güncel içeriği gösterdiğinden onlarda backfill normal
taramadır. Kanalın ilk taraması tamamlandıysa backfill ile gelen içerik "yeni" sayılır ve YZ hattına girer.
`reprocess` seçilen aşamayı (extract, classify, summarize, match) yayım tarihine göre bir aralıkta yeniden çalıştırır
ve portal kayıtlarının denetim izine "Yeniden işlendi" yazar.

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
- Portal–Keycloak girişi (`keycloak-js`) henüz eklenmedi; backend doğrulaması hazır, realm/istemci bilgisi bekleniyor.
- İzleme sonucu önbelleğe alınmıyor (plan Redis önbelleği öngörüyordu); 9 kaynak için canlı hesap yeterince hızlı.
  Dini bayram tarihleri her yıl `config/holidays.yaml`'a girilmeli (2027 tarihleri teyit edilmedi).
- Arama SQLite/PostgreSQL `ILIKE` ile yapılıyor; Türkçe büyük/küçük harf duyarsız tam metin arama (tsvector) üretim
  PostgreSQL'inde eklenecek.
- Birim görev tanımları taslaktır (organizasyon şemasından türetildi). KVKK uyum programının Mevzuat ve Uyum
  Başkanlığında olduğu bir varsayımdır; şemada KVKK'ya açıkça sahip birim yok.
- Taksonomi, etiketleme kılavuzu ve değerlendirme etiketleri çalıştay öncesi taslaktır. Kurum içi vLLM/Qwen ile henüz
  denenmedi (geliştirme ortamı kurum ağı dışında); `mevzuat-ai llm-check` ve `eval` kurumda çalıştırılmalı.
- KEP adaptörü bu kapsamın dışında (erişim yöntemi belirsiz; plan §6 İK-1).
- BDDK yurt dışı IP'lerinden erişilemiyor; bulut CI'da canlı test çalıştırılamaz (replay testleri ağ gerektirmez).
