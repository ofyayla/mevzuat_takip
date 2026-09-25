# Kaynak Keşif Raporu (İK-1 — Kaynak envanteri ve teknik keşif)

**Tarih:** 25.09.2026 · **Kapsam:** Kapsam Formu v3'teki 9 web kaynağı (KEP hariç)

Bu rapor, her kaynağa **en sağlam erişim yolunun** (resmi besleme → API → HTML) nasıl seçildiğini ve canlı sitede
neyin doğrulandığını özetler. Kanal tanımları `backend/config/sources.yaml` dosyasındadır. Her kanal için
kaydedilen gerçek yanıtlar `backend/tests/fixtures/<kaynak>/` altında durur.

## Özet tablo

| Kaynak | Erişim yöntemi | İstemci | Doğrulama | Not |
|---|---|---|---|---|
| **Resmî Gazete** | Fihrist uç noktası `/fihrist?tarih=YYYY-MM-DD[&mukerrer=N]` | `impersonate` | ✅ canlı | Site tarayıcı olmayan istemcileri **TLS parmak izinden** engelliyor. `/rss` adresi HTML döndürüyor, besleme yok |
| **BDDK** | HTML link deseni (`/Duyuru/Detay/{id}`, `/Mevzuat/Detay/{id}`, ekler `/Duyuru/EkGetir/`) | `impersonate` | ⚠️ **doğrulanmadı** | Geliştirme ortamından TCP bağlantısı kurulamadı (httpx, curl_cffi ve Chromium ile denendi; büyük olasılıkla yurt dışı IP engeli). Desenler web aramasıyla teyit edildi |
| **SPK** | HTML liste (`.liste a.link`): bültenler (PDF) + basın duyuruları | `httpx` | ✅ canlı | Düzenlemeler SPK Bülteni PDF'lerinde yayımlanır. `mevzuat.spk.gov.tr` alt alan adı izin listesinde değildi |
| **TCMB** | **Atom beslemesi** (Basın Duyuruları) + mevzuat belge listeleri | `httpx` | ✅ canlı | Beslemedeki tarihler Türkçe ("17 Eyl 2026"); kendi ayrıştırıcımızla okunuyor. Mevzuat PDF bağlantısındaki `CACHEID` belge güncellendiğinde değişir ve sürüm anahtarı olarak kullanılıyor |
| **KVKK** | HTML liste: duyurular (`.news__box`), kurul kararları ve mevzuat (`.members__item`) | `httpx` | ✅ canlı | Kurul kararı/yönetmelik bağlantıları çoğunlukla doğrudan RG'ye gider; bunlar liste satırı olarak saklanır, içerik RG kaynağından gelir |
| **MASAK** | **WordPress REST API** `masak.hmb.gov.tr/portal/v2/{posts,pages}` | `httpx` | ✅ canlı | Site bir React SPA; HTML kazıma mümkün değil ama API herkese açık. `modified_after` ile artımlı tarama yapılıyor, içerik yanıtla birlikte geliyor |
| **Ticaret Bakanlığı** | HTML liste (`ul.dizin-content li`) + tüketici mevzuatı link listesi | `impersonate` | ✅ canlı | TLS parmak izi engeli var (RG ile aynı). `/rss` yok. Tüketici mevzuatı bağlantıları mevzuat.gov.tr'ye gider |
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
3. **Coğrafi engel:** BDDK'ya yurt dışı çıkışlı ortamdan bağlanılamıyor. Kurum ağından erişimde sorun beklenmiyor.
   Doğrulama komutu aşağıda.
4. **Kibar tarama:** Host başına istek aralığı (varsayılan 1,5 sn, RG için 3 sn), kendini tanıtan User-Agent,
   geçici hatalarda üstel geri çekilme. RG `robots.txt` dosyası yalnızca Googlebot için noindex kuralları içeriyor.
   Diğer kaynakların çoğunda `robots.txt` yok veya kısıtlama yok.
5. **Çapraz kaynak bağlantıları:** KVKK kurul kararları ve Ticaret tüketici mevzuatı RG'ye veya mevzuat.gov.tr'ye
   bağlanıyor. İzin listesi dışındaki bir host'a giden öğe indirilmiyor, yalnızca liste satırı (JSON) saklanıyor.
   Tekilleştirme (İK-2) bu kayıtları RG kaydıyla birleştirecek.

## Kurum ağında yapılacak doğrulama

```bash
cd backend
mevzuat-collect check-access                                # 9 kaynağa bağlantı testi (firewall istisnası sonrası)
mevzuat-collect probe BDDK --record tests/fixtures/bddk --with-details --limit 3
mevzuat-collect probe RESMI_GAZETE                          # diğer kaynaklar için de aynı
```

BDDK probe çıktısı gelince:
- `sources.yaml` içindeki BDDK kanallarının liste kimlikleri (39/40/55) ve eklenecek diğer kategoriler netleştirilecek.
- `verified` alanı doldurulacak.
- `tests/test_bddk_synthetic.py` gerçek fixture'larla değiştirilecek.

## Açık konular

| # | Konu | Öneri |
|---|---|---|
| 1 | BDDK liste kategorileri (Kurul Kararları, Genelgeler, Tebliğler, Rehberler, Düzenleme Taslakları) | Kurum ağından probe sonrası `Mevzuat/Liste/{id}` kanalları eklenecek |
| 2 | `mevzuat.spk.gov.tr` ve `mevzuat.gov.tr` erişimi | Proxy izin listesine eklenirse SPK mevzuat değişiklikleri ve Ticaret tüketici mevzuatının tam metni doğrudan alınabilir |
| 3 | Resmî Gazete İLÂN BÖLÜMÜ | Varsayılan olarak hariç. Banka lisans iptali gibi ilanlar isteniyorsa `exclude_sections` boşaltılır |
| 4 | TCMB mevzuat listeleri tarihsiz | Yeni belge "ilk görüldüğü tarih" ile işaretlenir. İlk çalıştırmada mevcut ~260 belge arşive alınır (tek seferlik birikim) |
