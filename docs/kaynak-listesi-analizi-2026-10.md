# Kaynak Listesi Güncellemesi — Etki Analizi ve İş Planı

**Tarih:** 07.10.2026 · **Girdi:** Mevzuat ve Uyum Başkanlığı'nın gönderdiği
`Regulators_and_website_links - 05.10.2026.xlsx` (58 satır, sayfa "REGULATOR LİST", sütunlar: Kurum, Link)
· **Karşılaştırılan:** `backend/config/sources.yaml` (Kapsam Formu v3, 9 kaynak) ve `docs/kaynak-kesif-raporu.md`

## 1. Özet

- Listede **58 satır** var. Bunların **11'i bugün tam karşılanıyor** (9 kaynak; RG mükerrer ve BDDK "RG'de
  yayımlanmayan kurul kararları" satırları mevcut kanallar). **4 satır mevcut kaynağa yeni kanal ya da doğrulama**
  gerektiriyor. **43 satır yeni kurum** (bir çifti muhtemelen mükerrer/güncel değil, bkz. §4).
- Liste bir **kanal tanımı değil, ana sayfa listesi.** 58 satırın 46'sı kurumun ana sayfasını gösteriyor. Bizim
  çalışma birimimiz "kurum" değil "kanal" (izlenecek liste sayfası + okuma stratejisi). Bugünkü 9 kaynak 23 kanal
  demek. Her yeni kurum için **"bu kurumda neyi izliyoruz?"** sorusunun Başkanlıkla cevaplanması gerekiyor
  (duyurular mı, mevzuat listesi mi, kurul kararları mı, taslaklar mı?). Bu cevap olmadan geliştirme başlamamalı.
- Yeni kurumların büyük kısmının **bağlayıcı düzenlemeleri zaten Resmî Gazete'den geliyor** (HMB, GİB, SEDDK,
  BTK, KGK, SGK tebliğ/yönetmelikleri; AYM iptal kararları ve Yargıtay içtihadı birleştirme kararları RG yargı
  bölümünde). Bu kurumların sitesini izlemenin katma değeri **RG'ye girmeyen içerik**: duyuru, taslak, rehber,
  SSS, kurul kararı, sektör yazısı. Kapsam bu gözle daraltılırsa iş yükü belirgin şekilde düşer.
- Bu bir **kapsam değişikliği** (Kapsam Formu v3 / Talep 138380 → 9 kaynak + KEP). Formun revize edilmesi,
  takvim ve DMZ firewall izin listesinin buna göre güncellenmesi gerekir.

## 1a. Kapsam değerlendirmesi: genişleme mi, kayma mı?

Talep tanımı üç girdi sayıyor: (1) Resmî Gazete, (2) kamu otoritelerinin yayımladığı mevzuat ve basın
duyuruları, (3) KEP'e gelen mevzuat içerikli resmî yazılar. Kapsam Formu v3 ayrıca kaynak listesinin "nihai ve
sabit" olduğunu, **ek kaynak talebinin kapsam değişikliği sayılacağını** yazıyor. Listeyi iki testle ayırınca üç
grup çıkıyor:

| Grup | Amaç testi (talep tanımı) | Liste testi (9 kaynak) | Nitelik | Satırlar |
|---|---|---|---|---|
| **I — Kapsam genişlemesi** | İçinde: kamu otoritesi/öz-düzenleyici, düzenleyici içerik | Dışında | Meşru talep; Kapsam Formu hükmüne göre **değişiklik talebi + ek efor/takvim** | Öncelik A ve B'deki kamu otoriteleri ve birlikler (TKBB Danışma Kurulu, TBB, SEDDK, HMB, GİB, TMSF, Siber Güvenlik Bşk., Risk Merkezi, KGK, SGK, BKM, MKK, Borsa İstanbul, Takasbank …) |
| **II — Sınırda** | Yorumlanabilir: kamu otoritesi yayımlıyor ama "mevzuat/duyuru" değil, ya da otorite Türk değil, ya da metin henüz mevzuat değil | Kısmen | Her biri için Başkanlıkla tek tek karar | MASAK SSS, Reklam Kurulu kararları, TBMM kanun teklifleri, FATF, BIS, KAP, Diyanet |
| **III — Kapsam kayması** | **Dışında:** kamu otoritesi değil (haber, topluluk, toplayıcı) ya da mevzuat değil (içtihat) | Dışında | Ürünün niteliğini değiştirir; bu talebin kapsamında ele alınmamalı | katilimanaliz, procompliance, Fintech İstanbul; KAYSİS ve mevzuat.gov.tr (kaynak olarak; çözümleyici kullanımı sürer); Anayasa Mahkemesi (bireysel başvuru), Yargıtay, Danıştay, Barolar Birliği, Adalet Bakanlığı |

Grup III'ün kayma sayılmasının nedeni yalnız kaynak sayısı değil; talebin tasarım varsayımlarıyla çelişmesi:

- **Kaynağa dayalılık (İK-4):** Özetin her ifadesi kaynak metne dayandırılıyor ve kayıt "resmî yayım sayfasına"
  bağlanıyor. Haber metni resmî metin değil; haberden açılan kayıt yanlış ya da eksik aktarımı resmî bilgi gibi
  Başkanlığa sunar, denetim izinde "kaynak" ikincil bir site olur.
- **Tekilleştirme (İK-2):** Aynı düzenleme için resmî kayda ek olarak her haber ayrı bir aday üretir; mükerrer
  bildirimi önleme hedefini zorlar.
- **İlgililik ve etiketleme (İK-3):** Etiketleme kılavuzu ve few-shot örnekleri düzenleyici metinler için
  hazırlanıyor. Yorum yazısı, haber ve mahkeme kararı ayrı bir sınıflandırma problemi.
- **İçtihat:** Mahkeme kararı mevzuat değil; hacimli, kişisel veri içeren ve arama formuyla erişilen bir alan.
  Bankayı bağlayan kararlar (AYM iptal, Yargıtay İBK) zaten RG'de yayımlanıyor ve kapsamda.

Muhtemel açıklama: liste, Başkanlığın bugün **elle baktığı siteler listesi** (ana sayfa linkleri, haber siteleri,
SSS sayfası bunu gösteriyor). "Manuel takip ihtiyacını azaltma" hedefiyle bakılınca anlaşılır, ama bu bir kaynak
şartnamesi değil. Önerilen tutum:

1. **Grup I:** değişiklik talebi olarak kabul; fazlara bölünmüş ek efor ve firewall talebiyle.
2. **Grup II:** her satır için "hangi sayfa, hangi amaçla" sorusuyla tek tek karar. Mevcut emsaller: Rekabet
   ve BDDK kurul kararları kapsamda olduğu için Reklam Kurulu kararları Grup I'e yakın; MASAK SSS mevcut kaynağın
   bir sayfası olduğu için düşük efor.
3. **Grup III:** bu talebin kapsamı dışında. İhtiyaç sürerse ayrı talep olarak "haber/erken uyarı akışı"
   (onaya düşen mevzuat kaydı açmayan, ayrı ekranlı bilgilendirme) ve "içtihat takibi" tanımlanabilir; o zamana
   kadar bu siteler elle izlenmeye devam eder.

## 2. Satır satır eşleştirme

Durum: ✅ mevcut · 🟡 mevcut kaynakta yeni kanal/doğrulama · 🆕 yeni kurum.
Öncelik (yalnız 🆕/🟡): **A** bankayı doğrudan bağlayan / öz-düzenleyici · **B** sektörel, dolaylı ·
**C** yargı/içtihat (özel yaklaşım) · **D** ikincil kaynak (haber, toplayıcı) — farklı işleme.

| # | Kurum (Excel) | Link | Durum | Öncelik | Not |
|---|---|---|---|---|---|
| 1 | Resmî Gazete | resmigazete.gov.tr | ✅ | | `RESMI_GAZETE/fihrist` |
| 2 | Resmî Gazete Mükerrer | `…/fihrist?tarih=2026-09-06&mukerrer=1` | ✅ | | `max_mukerrer: 4`. Excel'deki link sabit tarihli bir örnek |
| 3 | BDDK | bddk.org.tr | ✅ | | 7 kanal |
| 4 | BDDK RG'de yayınlanmayan kurul kararları | bddk.org.tr/Mevzuat/Liste/56 | ✅ | | `BDDK/kurul_kararlari` |
| 5 | SPK | spk.gov.tr | ✅ | | bülten, basın duyurusu, mevzuat API |
| 6 | Helal Akreditasyon Kurumu | hak.gov.tr | 🆕 | B | Faizsiz finans standartlarıyla ilgisi Başkanlıkla netleşmeli |
| 7 | TCMB | tcmb.gov.tr | ✅ | | Atom + mevzuat listeleri |
| 8 | Hazine ve Maliye Bakanlığı | hmb.gov.tr | 🆕 | A | Tebliğler RG'den geliyor; site duyuruları + yaptırım/borçlanma duyuruları |
| 9 | MASAK | masak.hmb.gov.tr | ✅ | | WordPress REST |
| 10 | MASAK — Sıkça Sorulan Sorular | masak.hmb.gov.tr/sikca-sorulan-sorular | 🟡 | A | `pages` uç noktası + `modified_after` ile muhtemelen kapsanıyor; **SSS değişikliğinin yakalandığı doğrulanmalı**, gerekirse içerik-hash'li ayrı kanal |
| 11 | Gelir İdaresi Başkanlığı | gib.gov.tr | 🆕 | A | Tebliğler RG'de; sirküler, özelge, duyuru sitede |
| 12 | Ticaret Bakanlığı | ticaret.gov.tr | ✅ | | duyurular + tüketici mevzuatı |
| 13 | Rekabet Kurumu | rekabet.gov.tr | ✅ | | |
| 14 | KVKK | kvkk.gov.tr | ✅ | | |
| 15 | TKBB | tkbb.org.tr | ✅ | | |
| 16 | TKBB Danışma Kurulu | tkbbdanismakurulu.org.tr | 🆕 | **A (en yüksek)** | Faizsiz bankacılık standartları ve kararları; katılım bankası için bağlayıcı |
| 17 | Türkiye Bankalar Birliği | tbb.org.tr | 🆕 | A | Sektör uygulama esasları, genelgeler. `ISSUERS`'ta kod var |
| 18 | Siber Güvenlik Başkanlığı | siberguvenlik.gov.tr | 🆕 | A | Bilgi güvenliği yükümlülükleri |
| 19 | SEDDK | seddk.gov.tr | 🆕 | A | Bancassurance/BES; RG ilan filtresinde ve `ISSUERS`'ta var |
| 20 | Ticaret Bak. Tüketicinin Korunması ve Piyasa Gözetimi GM | tuketici.ticaret.gov.tr | 🟡 | A | Farklı host; `TICARET` altında duyuru kanalı. `allowed_hosts` zaten `*.ticaret.gov.tr` |
| 21 | — Reklam Kurulu Kararları | ticaret.gov.tr/tuketici/ticari-reklamlar/reklam-kurulu-kararlari | 🟡 | A | `TICARET` altında yeni kanal (karar PDF'leri, banka reklamları) |
| 22 | TBB Risk Merkezi | riskmerkezi.org/tr | 🆕 | A | Raporlama usul ve esasları |
| 23 | TOBB | tobb.org.tr | 🆕 | B | |
| 24 | TBMM | tbmm.gov.tr | 🆕 | B | Kanunlar RG'den geliyor; değeri **kanun teklifi** (erken uyarı). Ayrı kanal tipi |
| 25 | Diyanet İşleri Başkanlığı | diyanet.gov.tr | 🆕 | B | Kapsam belirsiz (Din İşleri Yüksek Kurulu kararları?) — Başkanlığa sorulmalı |
| 26 | Adalet Bakanlığı | adalet.gov.tr | 🆕 | C | |
| 27 | Anayasa Mahkemesi | anayasa.gov.tr | 🆕 | C | Norm denetimi kararları RG'de; bireysel başvuru kararları ayrı ve hacimli |
| 28 | Yargıtay | yargitay.gov.tr | 🆕 | C | İBK'lar RG'de; içtihat takibi ayrı ürün sayılmalı |
| 29 | Danıştay | danistay.gov.tr | 🆕 | C | |
| 30 | Türkiye Barolar Birliği | barobirlik.org.tr | 🆕 | C | |
| 31 | Dışişleri Bakanlığı | mfa.gov.tr | 🆕 | B | Değeri yaptırım/BM kararları ise o sayfa hedeflenmeli |
| 32 | İçişleri Bakanlığı | icisleri.gov.tr | 🆕 | B | |
| 33 | NVİ | nvi.gov.tr | 🆕 | B | Kimlik doğrulama/KPS duyuruları |
| 34 | SGK | sgk.gov.tr | 🆕 | B | Genelgeler sitede |
| 35 | TMSF | tmsf.org.tr | 🆕 | A | Mevduat/katılım fonu sigortası; `ISSUERS`'ta var |
| 36 | Aile, Çalışma ve Sosyal Hizmetler Bakanlığı | ailevecalisma.gov.tr | 🆕 ⚠ | B | Bakanlık 2021'de ikiye ayrıldı; #37 ile mükerrer olabilir, **adres güncel değil** — teyit |
| 37 | Çalışma ve Sosyal Güvenlik Bakanlığı | csgb.gov.tr | 🆕 | B | |
| 38 | Sanayi ve Teknoloji Bakanlığı | sanayi.gov.tr | 🆕 | B | |
| 39 | BTK | btk.gov.tr | 🆕 | A/B | Elektronik haberleşme, uzaktan kimlik doğrulama ile kesişim |
| 40 | KGK | kgk.gov.tr | 🆕 | B | TFRS/denetim standartları |
| 41 | Kredi Kayıt Bürosu | kkb.com.tr | 🆕 | B | |
| 42 | Bankalararası Kart Merkezi | bkm.com.tr | 🆕 | B | |
| 43 | "Fintech" | fintechistanbul.org | 🆕 | D | Haber/topluluk sitesi |
| 44 | İstanbul Ticaret Odası | ito.org.tr | 🆕 | B | |
| 45 | KAP | kap.org.tr | 🆕 | B | Çok yüksek hacim; yalnız düzenleyici bildirim türleri (kurum filtresiyle) izlenmeli |
| 46 | Mevzuat Bilgi Sistemi | mevzuat.gov.tr | 🟡 | D | Bugün **çözümleyici** olarak kullanılıyor (BDDK/Ticaret bağlantıları). "Son eklenen mevzuat" ayrıca kaynak yapılabilir |
| 47 | KAYSİS | kaysis.gov.tr | 🆕 | D | Toplayıcı |
| 48 | İslami Finans Haber Analiz Portalı | katilimanaliz.com | 🆕 | D | Haber |
| 49 | Bankacılık ve Finans Mevzuat ve Uyum Haberleri | procompliance.net | 🆕 | D | Haber (ticari) |
| 50 | EPDK | epdk.org.tr | 🆕 | B | Adres eski olabilir (epdk.gov.tr); bankayla ilgisi sınırlı — teyit |
| 51 | Türkiye Sermaye Piyasaları Birliği | tspb.org.tr | 🆕 | B | |
| 52 | Yatırımcı Tazmin Merkezi | ytm.gov.tr | 🆕 | B | |
| 53 | Merkezi Kayıt Kuruluşu | mkk.com.tr | 🆕 | B | RG ilan filtresinde var |
| 54 | Borsa İstanbul | borsaistanbul.com | 🆕 | B | RG ilan filtresinde var |
| 55 | Darphane ve Damga Matbaası GM | darphane.gov.tr | 🆕 | B | |
| 56 | Takasbank | takasbank.com.tr | 🆕 | B | RG ilan filtresinde var |
| 57 | FATF | fatf-gafi.org | 🆕 | A | Yabancı kaynak, İngilizce; gri liste / kamuoyu açıklamaları (yılda 3 genel kurul) |
| 58 | BASEL / BIS | bis.org | 🆕 | A/B | Yabancı, İngilizce; BCBS yayınları için resmi RSS beslemeleri var |

**Dağılım (43 yeni kurum + 4 yeni kanal):** A: 14 (11 kurum + 3 kanal) · B: 23 · C: 5 · D: 5 (#46 dahil).

## 3. Kurum başına ne iş çıkar?

Bugünkü hatta bir kaynağı "devreye almak" şu adımlardan oluşuyor; yeni her kurum için tekrarlanır:

| Adım | Ne | Dokunulan yer |
|---|---|---|
| 1 | **Kapsam:** hangi sayfalar izlenecek (Başkanlıkla) | — |
| 2 | **Keşif (Türkiye'den):** erişim, TLS (parmak izi engeli, eksik ara sertifika), besleme/API var mı, yapı | `mevzuat-collect probe`, `check-access`, `ca-fetch` |
| 3 | **Kanal tanımı:** strateji, seçiciler, tarih ayrıştırma, sayfalama, `refresh_after_days` | `config/sources.yaml` |
| 4 | **Fixture + replay testi** | `tests/fixtures/<kod>/`, `tests/test_sources_replay.py`, `tests/test_sources_config.py` (`SCOPE_SOURCES`) |
| 5 | **İzleme eşikleri:** `schedule`, `expected.silence_tolerance_hours`, `min_items_per_run` | `config/sources.yaml` |
| 6 | **Kurum tanıma + öncelik:** RG'de kurum adıyla eşleşme, çapraz kontrol, ilgililik ağırlığı | `app/processing/normalize.py` (`ISSUERS`, `ISSUER_PARENT`), `config/taxonomy.yaml` (`source_priority`, `issuer_priority`), RG `ilan_issuers` |
| 7 | **Birim eşleştirme:** kurumun ilgili birimleri/anahtar kelimeleri | `config/units.yaml` |
| 8 | **Ağ:** DMZ → internet 443 izin listesi | `docs/kurum-devreye-alma.md` §8, `scripts/erisim_testi.py` |
| 9 | **Belgeler** | `docs/kaynak-kesif-raporu.md`, runbook |

Mevcut stratejiler (`feed`, `wordpress_api`, `css_list`, `link_pattern`, `json_api`) kamu sitelerinin çoğunu
yapılandırmayla karşılar; çoğu kurum **kod değil YAML + fixture** işidir. Kod gerektirmesi beklenenler:

- **Yabancı / İngilizce kaynaklar (FATF, BIS):** YZ istemleri ve ilgililik/özet adımı Türkçe varsayıyor;
  İngilizce metin için istem uyarlaması, tarih biçimleri, gerekirse özetin Türkçe üretilmesi.
- **Haber/toplayıcı kaynaklar (D grubu):** Bunlar resmî düzenleme değil; onaya düşen "mevzuat" kaydı açmamalı.
  Önerilen rol: mevcut **çapraz kontrol** (`app/monitoring/cross_check.py`) için erken sinyal — "haberde geçen
  düzenleme 48 saatte resmî kaynakta görülmedi" alarmı — ya da ayrı bir "haber" akışı. Ayrı kaynak türü ve portal
  ayrımı gerekir. Ticari siteler için (procompliance) kullanım koşulları kontrol edilmeli.
- **Yargı kaynakları (C grubu):** Karar arama sistemleri (çoğu form/JS tabanlı), yüksek hacim, kişisel veri
  içeren karar metinleri. RG'ye girenler zaten kapsamda. Ayrı bir "içtihat takibi" kapsamı olarak ele alınmalı.
- **TBMM kanun teklifleri:** Belge değil süreç (teklif → komisyon → Genel Kurul). Erken uyarı kaydı olarak
  ayrı tür önerilir.
- **KAP:** Hacim nedeniyle bildirim türü/kurum filtresi şart; JSON uçları muhtemelen `json_api` ile okunur.
- **Tarayıcı (`client: browser`) gerekebilecek SPA siteler:** keşifte netleşir; DMZ imajına Chromium eklemek
  imaj boyutu ve güvenlik onayı demek.

## 4. Listede düzeltilmesi / teyit edilmesi gerekenler

1. **#36 Aile, Çalışma ve Sosyal Hizmetler Bakanlığı** — bakanlık 2021'de ayrıldı; `ailevecalisma.gov.tr` güncel
   değil, #37 (csgb.gov.tr) ile çakışıyor. Kastedilen Aile ve Sosyal Hizmetler Bakanlığı mı, satır silinsin mi?
2. **#50 EPDK** — `epdk.org.tr` eski alan adı olabilir; bankayla ilgisi nedir (ör. enerji projesi finansmanı)?
3. **#2 RG Mükerrer** — link sabit tarihli (06.09.2026); kanal zaten her gün mükerrer sayıları tarıyor.
4. **#25 Diyanet, #6 HAK, #31 Dışişleri, #43 Fintech** — hangi içerik isteniyor, belirsiz.
5. Linklerin bir kısmı `http://` (tarama `https` ile yapılır; yönlendirme keşifte doğrulanır).

## 5. Operasyonel etki

- **Firewall:** DMZ'den internete izin listesine yaklaşık **45–55 yeni alan adı** (www/kök/alt alan adı, ek
  host'lar) girecek. Bilgi Güvenliği onayı en uzun süren kalem olabilir; liste kesinleşince tek seferde talep
  edilmeli (`mevzuat-collect sources` çıktısı).
- **Tarama yükü:** 23 kanal → kabaca 100–120 kanal. Host başına kibar tarama (1,5 sn) korunduğu sürece kaynak
  başına yük küçük; ancak beat takvimi ve DMZ crawler kapasitesi (`scripts/load_test.py`) yeniden ölçülmeli.
- **YZ hacmi/maliyeti:** Duyuru akışı olan her kurum günlük öğe sayısını artırır; ilgisiz öğelerin çoğu ucuz
  ilgililik adımında elenir ama özet/önem/birim adımlarına düşen sayı da artar. B/D grubunda ilgililik eşiği ve
  `source_priority` düşük tutulmalı.
- **İzleme gürültüsü:** Az yayın yapan kurumlarda (`silence_tolerance_hours`) yanlış "sessiz kaynak" alarmı
  riski; her kaynağa yayın sıklığına göre eşik verilmeli, B grubu için `min_items_per_run: 0`.
- **İlk çalıştırma birikimi:** Tarihsiz listelerde mevcut belgeler BASELINE arşivine alınmalı, yoksa ilk gün
  portal yüzlerce eski kayıtla dolar.
- **Portal:** Kaynak filtresi `/api/v1/sources`'tan geliyorsa otomatik genişler; 50+ kaynak için gruplanmış
  (kurum türü/öncelik) filtre gerekir.

## 6. Önerilen yol haritası

| Faz | İçerik | Ön koşul |
|---|---|---|
| **0 — Kapsam netleştirme** (Başkanlıkla 1 toplantı + yazılı onay) | §2 tablosundaki öncelik ve "izlenecek sayfa" sütununun doldurulması; §4 teyitleri; C/D gruplarının bu fazda mı ayrı iş mi olacağı; Kapsam Formu revizyonu | — |
| **1 — Firewall talebi** | Kesinleşen alan adları için tek talep | Faz 0 |
| **2 — A grubu** (≈11 kurum + 3 yeni kanal) | Keşif → YAML → fixture/test → izleme eşikleri. TKBB Danışma Kurulu ilk sırada. FATF/BIS için İngilizce istem uyarlaması | Faz 1 (keşif Türkiye'den yapılmalı) |
| **3 — B grubu** (≈23 kurum) | Çoğunlukla tek "duyurular" kanalı, düşük öncelik ağırlığı, hafif izleme | Faz 2 kalıpları |
| **4 — C ve D grupları** | İçtihat takibi ve haber/erken uyarı akışı — ayrı tasarım | Ayrı kapsam kararı |

Kurum başına kabaca efor (keşiften teste, kurum ağından erişim varsayımıyla): standart HTML/besleme kaynağı
**0,5–1 gün**, TLS/parmak izi sorunu veya çok kanallı kaynak **1–2 gün**, SPA/tarayıcı gerektiren ya da yeni
kaynak türü **3+ gün**. Bu, A+B grupları için kabaca **6–9 hafta-kişi** demek; C ve D ayrıca tasarlanmalı.
Kesin tahmin, Faz 2'nin ilk 3–4 kaynağındaki keşif sonuçlarından sonra güncellenmelidir.

## 7. Başkanlığa sorulacaklar

1. Her kurum için izlenmesini istediğiniz içerik nedir (duyuru / mevzuat / kurul kararı / taslak / SSS)?
2. RG'de yayımlanan düzenlemeler zaten yakalanıyor; kurum sitelerinden ek olarak RG'ye girmeyen içerik mi
   bekleniyor?
3. Haber siteleri (katilimanaliz, procompliance, Fintech İstanbul) portalda onaya düşen kayıt mı olsun, yoksa
   yalnızca bilgilendirme/erken uyarı akışı mı?
4. Yargı kaynaklarında (AYM, Yargıtay, Danıştay) beklenti nedir: RG'deki kararlar yeterli mi, yoksa belirli konu
   başlıklarında içtihat takibi mi isteniyor?
5. FATF/BIS için özetler Türkçe mi üretilsin?
6. §4'teki mükerrer/eski satırlar için karar.
