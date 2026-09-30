# Kaynak Keşif Raporu (İK-1 — Kaynak envanteri ve teknik keşif)

**Tarih:** 25.09.2026 (BDDK ve Türkiye'den erişim bulguları aynı gün yerel makineden eklendi) · **Kapsam:** Kapsam
Formu v3'teki 9 web kaynağı (KEP hariç)

Bu rapor, her kaynağa **en sağlam erişim yolunun** (resmi besleme → API → HTML) nasıl seçildiğini ve canlı sitede
neyin doğrulandığını özetler. Kanal tanımları `backend/config/sources.yaml` dosyasındadır. Her kanal için
kaydedilen gerçek yanıtlar `backend/tests/fixtures/<kaynak>/` altında durur.

## Özet tablo

| Kaynak | Erişim yöntemi | İstemci | Doğrulama | Not |
|---|---|---|---|---|
| **Resmî Gazete** | Fihrist uç noktası `/fihrist?tarih=YYYY-MM-DD[&mukerrer=N]` + Çeşitli İlânlar dizini (kurum filtresiyle) | `impersonate` | ✅ canlı | Site tarayıcı olmayan istemcileri **TLS parmak izinden** engelliyor. `/rss` adresi HTML döndürüyor, besleme yok. Türkiye'den bağlanınca **TLS ara sertifikası gönderilmiyor** (bkz. bulgu 6) |
| **BDDK** | HTML link deseni: Duyuru 39/40/48, Mevzuat 49–52/55/56/58 (`a.mevzuatBaslik`) | `impersonate` | ✅ canlı (TR) | Yurt dışı IP'lerinden erişilemiyor; Türkiye'den doğrulandı. **TLS ara sertifikası gönderilmiyor.** Kanun/yönetmelik bağlantıları mevzuat.gov.tr'ye gider, tam metin oradan alınır |
| **SPK** | HTML liste (`.liste a.link`): bültenler (PDF) + basın duyuruları; **SPK Mevzuat Sistemi JSON API'si** | `httpx` | ✅ canlı | Düzenlemeler SPK Bülteni PDF'lerinde yayımlanır. `mevzuat.spk.gov.tr` bir React uygulaması; `/api/Search/All` 396 kaydı (tebliğ, yönetmelik, rehber, ilke kararı) metadatasıyla veriyor, dosya `/api/{tür}/File/{id}` |
| **TCMB** | **Atom beslemesi** (Basın Duyuruları) + mevzuat belge listeleri | `httpx` | ✅ canlı | Beslemedeki tarihler Türkçe ("17 Eyl 2026"); kendi ayrıştırıcımızla okunuyor. Mevzuat PDF bağlantısındaki `CACHEID` belge güncellendiğinde değişir ve sürüm anahtarı olarak kullanılıyor |
| **KVKK** | HTML liste: duyurular (`.news__box`), kurul kararları ve mevzuat (`.members__item`) | `httpx` | ✅ canlı | Kurul kararı/yönetmelik bağlantıları çoğunlukla doğrudan RG'ye gider; bunlar liste satırı olarak saklanır, içerik RG kaynağından gelir |
| **MASAK** | **WordPress REST API** `masak.hmb.gov.tr/portal/v2/{posts,pages}` | `httpx` | ✅ canlı | Site bir React SPA; HTML kazıma mümkün değil ama API herkese açık. `modified_after` ile artımlı tarama yapılıyor, içerik yanıtla birlikte geliyor |
| **Ticaret Bakanlığı** | HTML liste (`ul.dizin-content li`) + tüketici mevzuatı link listesi | `impersonate` | ✅ canlı | TLS parmak izi engeli var (RG ile aynı). `/rss` yok. Tüketici mevzuatı bağlantıları mevzuat.gov.tr'ye gider; tam metin oradan alınır |
| **Rekabet Kurumu** | HTML: duyurular (link deseni) + kurul kararları (`#kararList table`) | `httpx` | ✅ canlı | Sayfa içeriği `<header>` elemanının içinde; ayrıştırıcı buna göre ayarlandı. Karar sayısı, karar tarihi ve karar türü ayrı alan olarak alınıyor |
| **TKBB** | HTML link deseni: duyurular + birlik düzenlemeleri (idari/mesleki) | `httpx` | ✅ canlı | Tailwind yardımcı sınıfları kullanıldığı için CSS seçici yerine link deseni kullanılıyor; başlık bağlantının üst elemanından alınıyor |

## Genel bulgular

1. **Resmi besleme/API yalnızca 2 kaynakta var:** TCMB (Atom) ve MASAK (WordPress REST). Diğer 7 kaynak HTML'den okunuyor.
   Bu yüzden İK-7'deki yapı değişikliği uyarısı kritik: her taramada kanal bazlı öğe sayısı ve **yapı imzası**
   (öğe kapsayıcılarının etiket/sınıf yolu özeti) `fetch_run.channels` alanına yazılıyor. 200 dönen ama boş gelen liste
   `structure_alert` üretiyor.
2. **TLS parmak izi engeli:** Resmî Gazete ve Ticaret Bakanlığı, `curl`/`httpx` gibi istemcileri reddediyor.
   `curl_cffi` ile Chrome TLS imzası taklit edildiğinde erişim sağlanıyor (Playwright/Chromium'a gerek kalmıyor,
   ama `client: browser` seçeneği son çare olarak hazır).
3. **Coğrafi engel:** BDDK'ya yurt dışı çıkışlı ortamdan bağlanılamıyor. Türkiye'den erişim 25.09.2026'da
   doğrulandı; tüm kanallar ve fixture'lar gerçek yanıtlarla kayıtlı.
4. **Kibar tarama:** Host başına istek aralığı (varsayılan 1,5 sn, RG için 3 sn), kendini tanıtan User-Agent,
   geçici hatalarda üstel geri çekilme. RG `robots.txt` dosyası yalnızca Googlebot için noindex kuralları içeriyor.
   Diğer kaynakların çoğunda `robots.txt` yok veya kısıtlama yok.
5. **Çapraz kaynak bağlantıları:** BDDK düzenlemeleri ve Ticaret tüketici mevzuatı mevzuat.gov.tr'ye bağlanıyor.
   Aynı belgeye farklı biçimlerde bağlantı veriliyor (eski `Metin.Aspx?MevzuatKod=7.5.11180&sourceXmlSearch=…`,
   yeni `/mevzuat?MevzuatNo=11180&MevzuatTur=7&MevzuatTertip=5`, `http`/`https`, `www` var/yok). Çözümleyici
   (`app/collectors/resolvers.py`) bunları `mevzuat.gov.tr:7.5.11180` kanonik kimliğine indiriyor ve tam metni sayfanın
   yüklediği `MevzuatFihristDetayIframe` adresinden alıyor. KVKK kurul kararları RG'ye bağlanıyor; bunlar liste
   satırı (JSON) olarak saklanıyor, içerik RG kaynağından geliyor (İK-2 birleştirir).
6. **Eksik TLS zinciri (Türkiye'den bağlanınca):** BDDK (GlobalSign RSA OV SSL CA 2018) ile Resmî Gazete ve
   mevzuat.gov.tr (ortak `*.tccb.gov.tr` sertifikası, GeoTrust TLS RSA CA G1) TLS el sıkışmasında ara sertifikayı
   göndermiyor. Tarayıcılar eksik halkayı AIA adresinden kendileri indirdiği için sorun tarayıcıda görünmüyor;
   curl/httpx `unable to get local issuer certificate` hatası veriyor. Yurt dışındaki geliştirme ortamında Resmî
   Gazete farklı bir sunucuya düştüğü için bu hata orada görülmemişti. Çözüm: ara sertifikalar
   `backend/config/certs/` altında tutuluyor ve kök deposuna ekleniyor (güven yine köke dayanıyor, doğrulama
   kapatılmıyor). Sertifika yenilendiğinde `mevzuat-collect ca-fetch <host>` yeni ara sertifikayı AIA'dan indirip zinciri
   doğruladıktan sonra yazıyor; `check-access` bu hatayı görünce komutu öneriyor.
7. **Yerinde güncellenen metinler:** mevzuat.gov.tr ve SPK Mevzuat Sistemi konsolide metni aynı adreste güncelliyor
   ve değişiklik tarihi vermiyor. Bu kanallarda `refresh_after_days: 14` ile her belge 14 günde bir yeniden indirilip
   hash'le karşılaştırılıyor (çalıştırma başına en fazla 20 belge, yük yayılıyor). Değişiklik varsa yeni sürüm açılıyor.

## Kurum ağında yapılacak kontrol

```bash
cd backend
mevzuat-collect check-access          # 9 kaynağa bağlantı testi (firewall/proxy istisnası sonrası)
mevzuat-collect ca-fetch www.bddk.org.tr www.resmigazete.gov.tr www.mevzuat.gov.tr   # yalnızca zincir hatası varsa
```

Kurum proxy'si TLS denetimi yapıyorsa `CA_BUNDLE` kurum kök sertifikasını göstermeli; `config/certs` altındaki ara
sertifikalar bu paketin üstüne eklenir. Proxy izin listesine eklenmesi gereken host'lar: kaynakların `allowed_hosts`
alanları, ayrıca `mevzuat.gov.tr`, `www.mevzuat.gov.tr` ve `mevzuat.spk.gov.tr`.

## BDDK kanalları

| Kanal | Liste | İçerik |
|---|---|---|
| `duyurular` | Duyuru/Liste/39 Basın Duyuruları | Detay sayfası + ek (`/Duyuru/EkGetir/`) |
| `mevzuat_duyurulari` | Duyuru/Liste/40 Mevzuat Duyuruları | Detay sayfası + ek |
| `kurulus_duyurulari` | Duyuru/Liste/48 Kuruluş Duyuruları (faaliyet izni, iptal) | Detay sayfası + ek; karar tarihi/sayısı ayrı alan |
| `rg_kurul_kararlari` | Mevzuat/Liste/55 RG'de yayımlanan Kurul Kararları | PDF (`/Mevzuat/DokumanGetir/`) |
| `kurul_kararlari` | Mevzuat/Liste/56 RG'de **yayımlanmayan** Kurul Kararları | PDF (yalnızca BDDK'da bulunur) |
| `duzenleme_taslaklari` | Mevzuat/Liste/58 Düzenleme Taslakları | PDF |
| `duzenlemeler` | Mevzuat/Liste/49–52: kanunlar, bankacılık düzenlemeleri (akordeon grubu `extra.group`), kart ve finansal kiralama düzenlemeleri | mevzuat.gov.tr tam metni veya BDDK PDF'i; 14 günde bir yeniden kontrol |

Kapsam dışı: 41 İnsan Kaynakları, 42 Veri Yayımlama, 43 Duyuru Arşivi, 197 Güncel Duyurular (39'un tekrarı),
54 Kurum iç düzenlemeleri, 63 Mülga Düzenlemeler.

## Resmî Gazete İlân Bölümü

İlân Bölümü bütünüyle alınmıyor (yargı ve ihale ilanları, döviz kurları). Her gün yayımlanan "Çeşitli İlânlar"
dizini açılıyor; dizin her ilanı ayrı PDF'e ilan veren kurumun adıyla bağlıyor ("Bankacılık Düzenleme ve Denetleme
Kurumundan:"). Yalnızca kurum adı `ilan_issuers` listesine uyan ilanlar ayrı öğe oluyor: BDDK, TCMB, SPK, MASAK,
TMSF, KVKK, Rekabet, Ticaret Bakanlığı, SEDDK, Hazine ve Maliye Bakanlığı, TBB/TKBB, Borsa İstanbul/MKK/Takasbank.
Son 30 günlük taramada bu filtre günde 0–2 ilan seçti (ör. 17.09.2026 BDDK ilanı, TCMB ilanları); üniversite,
belediye vb. ilanlar alınmadı. Günde 1–2 ek istek.

## Açık konular

| # | Konu | Öneri |
|---|---|---|
| 1 | TCMB mevzuat listeleri ve BDDK düzenleme taslakları tarihsiz | Yeni belge "ilk görüldüğü tarih" ile işaretlenir. İlk çalıştırmada mevcut belgeler arşive alınır (tek seferlik birikim, BASELINE) |
| 2 | Ara sertifika süreleri | GlobalSign RSA OV SSL CA 2018: 21.11.2028, GeoTrust TLS RSA CA G1: 02.11.2027. `test_intermediate_certificates_valid` süresi dolunca kırmızıya döner; `ca-fetch` ile yenilenir |
