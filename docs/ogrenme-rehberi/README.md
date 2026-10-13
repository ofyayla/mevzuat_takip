# Mevzuat Takip — Geliştirici Öğrenme Rehberi

Bu klasör, projeyi **crawler'dan başlayarak uçtan uca** öğrenmek, geliştirme noktalarını görmek ve sistemi
yönetip sunabilmek isteyen geliştiriciler için hazırlandı. Buradaki her teknik bilgi kodla doğrulandı; bölümlerde
ilgili dosya ve fonksiyon adları verilir. Kod değişirse belge değil kod esastır. Bir çelişki görürseniz belgeyi
düzeltin.

Mevcut operasyon belgelerinin yerine geçmez, onlara giden yolu açar:

| Belge | Ne için |
|---|---|
| Bu rehber (`docs/ogrenme-rehberi/`) | Öğrenme sırası, mimari, akış, takvim, geliştirme noktaları, sunum |
| `mevzuat-core/docs/runbook.md` | İşletim ve olay müdahalesi (alarm → ne yapılır) |
| `mevzuat-core/docs/kurum-devreye-alma.md` | Kurumda ilk kurulum adımları |
| `mevzuat-core/docs/backend-gelistirme-plani.md` | Tasarım kararlarının gerekçeleri (plan §…) |
| `mevzuat-core/app/README.md` | İK bazında modül açıklamaları ve komutlar |
| `mevzuat-crawler/API_REFERENCE.md` | Crawler komutları, ortam değişkenleri, MongoDB sözleşmesi |

## Okuma sırası

| # | Belge | Süre | Sonunda şunu yapabilmelisiniz |
|---|---|---|---|
| 1 | [01 — Genel bakış](01-genel-bakis.md) | 30 dk | Sistemi 2 dakikada anlatmak; DMZ/LAN ayrımını ve veri yolunu çizmek |
| 2 | [02 — Crawler mimarisi](02-crawler-mimarisi.md) | 2–3 saat | Bir taramanın her adımını, sürümlemeyi, BASELINE'ı, kilitleri açıklamak |
| 3 | [03 — Tarama takvimi ve kaynaklar](03-tarama-takvimi-ve-kaynaklar.md) | 1 saat | "Hangi kaynak ne sıklıkla taranıyor, bir yayın en geç ne zaman yakalanır?" sorusunu cevaplamak |
| 4 | [04 — Crawler sonrası uçtan uca akış](04-uctan-uca-akis.md) | 1–2 saat | Mongo → PostgreSQL → metin → tekilleştirme → YZ → portal zincirini izlemek |
| 5 | [05 — Uygulamalı çalışma ve işletim](05-uygulamali-calisma.md) | 2–4 saat | Testleri çalıştırmak, ağsız tarama denemek, yeni kaynak eklemek, sorun ayıklamak |
| 6 | [06 — Geliştirme noktaları](06-gelistirme-noktalari.md) | 1 saat | Bilinen riskleri ve iyileştirme önerilerini önceliklendirmek |
| 7 | [07 — Sunum rehberi](07-sunum-rehberi.md) | 1 saat | Projeyi teknik ve teknik olmayan dinleyiciye sunmak, soruları cevaplamak |
| 8 | [08 — İyileştirme planı ve uygulama durumu](08-iyilestirme-plani.md) | 45 dk | 06'daki riskler ve kod taramasının hangilerinin uygulandığını, hangilerinin neden ertelendiğini görmek |

## Önerilen 5 günlük plan (ekip için)

| Gün | Konu | Pratik |
|---|---|---|
| 1 | 01 + 02 (ilk yarı: servis döngüsü, zamanlama, kilitler) | `mevzuat-crawler/app` testlerini çalıştırın (05 §1) |
| 2 | 02 (ikinci yarı: SourceCollector, stratejiler, HTTP/TLS) + 03 | Fixture'lardan ağsız kanal listeleme (05 §2), `sources.yaml`'ı okuyup her kanalı açıklayın |
| 3 | 04 (ingest, işleme, tekilleştirme, YZ, izleme) | `docker compose up` ile yerel uçtan uca ortam (05 §3); Mongo'da `fetch_runs`'a bakın |
| 4 | 05 (yeni kaynak ekleme, sorun ayıklama) | Bir test kaynağı ekleyip replay testi yazın (05 §5) |
| 5 | 06 + 07 | Her geliştirici 06'dan bir maddeyi derinlemesine inceler; ekip 07'deki akışla deneme sunumu yapar |

Bir eşleştirme önerisi: Ekipten biri "crawler sahibi", biri "core/YZ sahibi" olsun. Gün 5'te birbirlerinin alanını
sunsunlar. Anlatamadığınız yer, öğrenmediğiniz yerdir.

## Hakimiyet kontrol listesi

Aşağıdaki soruları koda bakmadan cevaplayabiliyorsanız crawler'a hakimsiniz. Cevaplar ilgili bölümlerde var.

1. Crawler neden Celery değil de tek süreçli bir döngü? Neden gunicorn yok? (02 §1)
2. Zamanlama nereden geliyor? Servis kapalıyken kaçan tetiklemeler ne olur? (02 §2, 03 §3)
3. Aynı kaynağın aynı anda iki kez taranmasını engelleyen iki mekanizma nedir? (02 §3)
4. "Şimdi tara" isteği (`POST /api/v1/admin/sources/{code}/run`) LAN'daki API'den DMZ'deki crawler'a nasıl
   ulaşır? Kaç saniyede alınır? (02 §4)
5. Bir liste öğesi için detay sayfası ne zaman **indirilmez**? Ne zaman yeni sürüm açılır? (02 §5.3)
6. BASELINE nedir, ne zaman biter, neden önemlidir? (02 §5.4)
7. `max_details=200` bütçesi dolduğunda ne olur? (02 §5.2)
8. `refresh_after_days` hangi kaynaklarda ve neden var? (02 §5.5, 03 §2)
9. KVKK listesindeki bir bağlantı Resmî Gazete'ye gidiyorsa crawler ne saklar? (02 §5.6)
10. `impersonate` istemcisi neden gerekli? Eksik TLS ara sertifikası nasıl çözülüyor? (02 §6)
11. Crawler'ın MongoDB'de yazdığı 6 koleksiyon ve GridFS kovası hangileri? `synced`/`rev` kuralı nedir? (02 §8)
12. Crawler durursa LAN tarafı bunu kaç dakikada fark eder, hangi alarmla? (04 §6)
13. Resmî Gazete günde kaç kez, BDDK haftada kaç kez taranıyor? (03 §1)
14. Bir site şablonunu değiştirirse bunu hangi sinyaller yakalar? (02 §5.7, 04 §6)
15. Bir belge taramadan portala kadar hangi durumlardan geçer? (04 §4)
