# 03 — Tarama Takvimi ve Kaynaklar

"Crawler ne kadar zamanda bir tarıyor?" sorusunun cevabı tek bir sayı değildir. Her kaynağın kendi cron takvimi
vardır (`config/sources.yaml` → `schedule`, **Europe/Istanbul**). Aşağıdaki tablolar o dosyadan `croniter` ile
hesaplandı (12–18 Ekim 2026 haftası). Takvim değişirse 05 §4'teki betikle yeniden hesaplayın.

## 1. Kaynak başına takvim

| Kaynak | Cron ifadeleri | Hafta içi tarama saatleri | Hafta sonu | Taramalar/hafta |
|---|---|---|---|---|
| **Resmî Gazete** | `10 0 * * *`, `30 2 * * *`, `30 7 * * *`, `0 12 * * *`, `0 17 * * *`, `0 21 * * *`, `45 23 * * *` | 00:10, 02:30, 07:30, 12:00, 17:00, 21:00, 23:45 | Her gün aynı (7/gün) | **49** |
| **BDDK** | `0 8-20 * * 1-5` | Saat başı 08:00–20:00 (13/gün) | Yok | **65** |
| **SPK** | `0 9 * * 1-5`, `0 14 * * 1-5`, `30 19 * * *` | 09:00, 14:00, 19:30 | 19:30 | 17 |
| **TCMB** | `0 9 * * 1-5`, `30 14 * * 1-5`, `0 19 * * 1-5` | 09:00, 14:30, 19:00 | Yok | 15 |
| **KVKK** | `0 9 * * 1-5`, `0 15 * * 1-5`, `0 20 * * *` | 09:00, 15:00, 20:00 | 20:00 | 17 |
| **MASAK** | `0 9 * * 1-5`, `0 15 * * 1-5`, `0 20 * * *` | 09:00, 15:00, 20:00 | 20:00 | 17 |
| **Ticaret Bakanlığı** | `0 10 * * 1-5`, `0 16 * * 1-5` | 10:00, 16:00 | Yok | 10 |
| **Rekabet Kurumu** | `0 10 * * 1-5`, `0 17 * * 1-5` | 10:00, 17:00 | Yok | 10 |
| **TKBB** | `0 10 * * 1-5`, `0 17 * * 1-5` | 10:00, 17:00 | Yok | 10 |
| **Toplam** | | | | **≈210 tarama/hafta** |

Notlar:

- Crawler **resmî tatilleri bilmez**: cron yalnızca haftanın gününe bakar. Tatiller yalnızca izleme tarafında
  sessizlik hesabından düşülür (04 §6).
- Resmî Gazete'nin sık ve gece yarısına yakın pencereleri bilinçlidir. Mükerrer sayılar gün içinde geç
  saatlerde yayımlanabilir. Her taramada **bugün + dün** (`lookback_days: 1`) yeniden kontrol edilir.
- Aynı dakikaya düşen taramalar paralel koşar (en fazla 3), fazlası havuzda sırada bekler. Ör. hafta içi 10:00'da
  BDDK + Ticaret + Rekabet + TKBB, 17:00'de RG + BDDK + Rekabet + TKBB tetiklenir.
- Bir kaynağın önceki taraması hâlâ sürüyorsa yeni tetikleme **atlanır**, kuyruğa alınmaz (02 §2).

## 2. Kaynak → kanal → strateji

| Kaynak | İstemci | Host başına bekleme | Kanallar (strateji) | Yerinde güncelleme kontrolü |
|---|---|---|---|---|
| RESMI_GAZETE | `impersonate` | **3 sn** | `fihrist` (resmi_gazete): asıl + ≤4 mükerrer, bugün+dün; İLÂN BÖLÜMÜ'nden yalnızca ilgili kurum ilanları | — |
| BDDK | `impersonate` | 1,5 sn | `duyurular`, `mevzuat_duyurulari`, `kurulus_duyurulari` (Duyuru/Liste 39/40/48, ekler `/Duyuru/EkGetir/`), `rg_kurul_kararlari` (55), `kurul_kararlari` (56), `duzenleme_taslaklari` (58), `duzenlemeler` (49–52, akordeon grupları) — hepsi link_pattern | `duzenlemeler`: 14 gün |
| SPK | `httpx` | 1,5 sn | `bultenler` (css_list, `{year}`), `basin_duyurulari` (css_list, `{year}`), `mevzuat` (json_api, mevzuat.spk.gov.tr) | `mevzuat`: 14 gün |
| TCMB | `httpx` | 1,5 sn | `basin_duyurulari` (feed/Atom), `mevzuat` (link_pattern, 6 sayfa, sürüm = `CACHEID`) | — (CACHEID sürüm anahtarı yeter) |
| KVKK | `httpx` | 1,5 sn | `duyurular` (css_list, PDF ekleri), `kurul_kararlari` (css_list), `mevzuat` (css_list: yönetmelik, tebliğ, rehber) | — |
| MASAK | `httpx` | 1,5 sn | `icerik` (wordpress_api: posts + pages, artımlı, PDF/DOC ekleri) | — (`modified` sürüm anahtarı) |
| TICARET | `impersonate` | 1,5 sn | `duyurular` (css_list, 2 sayfa), `tuketici_mevzuati` (link_pattern → mevzuat.gov.tr) | `tuketici_mevzuati`: 14 gün |
| REKABET | `httpx` | 1,5 sn | `duyurular` (link_pattern), `kurul_kararlari` (css_list, karar sayısı/türü alanları) | — |
| TKBB | `httpx` | 1,5 sn | `duyurular` (link_pattern), `birlik_duzenlemeleri` (link_pattern, PDF/DOC ekleri) | — |

Kanal yapıları canlı sitede en son **25.09.2026**'da doğrulandı (`verified` alanı).

### Değişiklik yokken bir tarama kaç istek atar? (yaklaşık)

Değişiklik yoksa yalnızca liste sayfaları istenir (02 §5.3). Aşağıdaki sayılar yapılandırmadan türetilmiş
**tahminlerdir**. Gerçek değerler `fetch_runs.requests` alanında, gerçek süreler `finished_at − started_at`'tadır
(ölçüm sorgusu 05 §4).

| Kaynak | Liste isteği | En az süre (yalnızca bekleme) | Ek yük |
|---|---|---|---|
| RESMI_GAZETE | ~4–6 (2 gün × asıl + boş mükerrer denemesi + ilan dizinleri) | ~15 sn (3 sn aralık) | Yeni sayı günü: sayıdaki madde sayısı kadar detay (×3 sn) |
| BDDK | 10 | ~15 sn | Yenileme: ≤20 detay/tarama |
| SPK | 3 (1–15 Ocak'ta 5) | ~3 sn | Yenileme: ≤20 dosya/tarama |
| TCMB | 7 | ~9 sn | |
| KVKK | 5 | ~6 sn | |
| MASAK | 2–6 (WP sayfalama) | ~3 sn | İçerik API yanıtında gelir, detay isteği yok |
| TICARET | 3 | ~3 sn | Yenileme: ≤20 detay/tarama |
| REKABET | 2 | ~1,5 sn | |
| TKBB | 3 | ~3 sn | |

İlk kurulumda durum farklıdır: BASELINE birikimi tarama başına 200 detayla sınırlıdır ve birkaç taramaya yayılır
(02 §5.2, §5.4).

## 3. "Bir yayın en geç ne zaman yakalanır?"

Toplam gecikme = **(yayın → bir sonraki tarama)** + **tarama süresi** + **aktarım (≤1 dk)** + **işleme ve YZ**
(04 §5). İlk terim takvimle belirlenir:

| Kaynak | Hafta içi gün içinde en uzun boşluk | Hafta içi gece | En kötü durum (hafta sonu) |
|---|---|---|---|
| RESMI_GAZETE | 5 sa (02:30→07:30, 12:00→17:00) | 21:00, 23:45, 00:10 pencereleri (en uzun 2 sa 45 dk) | Hafta sonu da aynı |
| BDDK | **1 sa** (08–20 arası) | 12 sa (20:00→08:00) | Cuma 20:00 → Pazartesi 08:00 = **60 sa** |
| SPK | 5 sa 30 dk (14:00→19:30) | 13 sa 30 dk | Hafta sonu günde bir (19:30): en uzun 24 sa |
| TCMB | 5 sa 30 dk | 14 sa | Cuma 19:00 → Pazartesi 09:00 = **62 sa** |
| KVKK / MASAK | 6 sa | 13 sa | Hafta sonu günde bir (20:00): en uzun 24 sa |
| TICARET | 6 sa | 18 sa | Cuma 16:00 → Pazartesi 10:00 = **66 sa** |
| REKABET / TKBB | 7 sa | 17 sa | Cuma 17:00 → Pazartesi 10:00 = **65 sa** |

**Değerlendirme:** Takvim, keşifte gözlenen yayın alışkanlıklarına göre seçildi (`kaynak-kesif-raporu.md`). Kurum
siteleri hafta sonu nadiren yayın yapar. Resmî Gazete'de yayımlanan düzenlemeler zaten RG kaynağından gün içinde
yakalanır, kurum sitesi ikinci kaynak olur. Yine de hafta sonu boşlukları bir **iş kararıdır**. Başkanlıkla
teyit edilmesi önerilir (06 §3).

## 4. Takvimi değiştirmek

1. `mevzuat-core/app/config/sources.yaml` içinde ilgili kaynağın `schedule` listesini düzenleyin. Kaynak core'dur.
2. `python tools/shared.py sync && python tools/shared.py check`
3. `cd mevzuat-crawler/app && pytest -q` (`test_sources_config.py` YAML'ı doğrular)
4. Crawler imajını derleyin. Varsayılan olarak imajdaki `config/sources.yaml` kullanılır (`SOURCES_FILE` ile
   başka dosya gösterilebilir ama DMZ kurulumunda bağlanmış bir dosya yok). DMZ'de imajı güncelleyip
   `systemctl restart mevzuat-crawler` çalıştırın.
5. Core da `sources.yaml` okur: izleme toleransları, `content_selector`, yerel mod zamanlaması. Core imajını da
   güncelleyin.

Kibar tarama ilkesi: Kurum sitelerine yük bindirmemek için takvimi sıklaştırmadan önce `request_delay_s`'i ve
sitenin yanıt sürelerini göz önüne alın. Daha sık tarama genellikle gerekmez. Değişiklik yoksa tarama ucuzdur ama
sitenin gözünde yine bir ziyarettir.

## 5. Beklenen davranış (izleme toleransları)

`expected` alanı izlemenin "normal"i bilmesini sağlar (04 §6):

| Kaynak | Sessizlik toleransı | Takvim | Asgari öğe/tarama |
|---|---|---|---|
| RESMI_GAZETE | 30 sa | Takvim günü | 1 |
| BDDK | 120 sa | İş günü | 1 |
| SPK | 192 sa | İş günü | 1 |
| TCMB | 240 sa | İş günü | 1 |
| KVKK | 240 sa | İş günü | 1 |
| MASAK | 336 sa | İş günü | **0** (artımlı API; sessiz günde boş liste normal) |
| TICARET | 168 sa | İş günü | 1 |
| REKABET | 168 sa | İş günü | 1 |
| TKBB | 336 sa | İş günü | 1 |

Veri biriktikçe (≥28 gün ve ≥8 yayın aralığı) tolerans, son 90 günün yayın aralıklarının **p95**'inden öğrenilir
(alt sınır 12 sa). Yukarıdaki değerler başlangıç değerleridir.

Sıradaki bölüm: [04 — Crawler sonrası uçtan uca akış](04-uctan-uca-akis.md)
