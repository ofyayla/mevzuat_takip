# portal — Mevzuat Takip Portalı arayüzü

Tek sayfalık, derleme gerektirmeyen (düz HTML/CSS/JS) arayüz. `app/api/main.py` sunar; dış CDN, yazı tipi ya da paket
yoktur.

| Yol | Ne |
|---|---|
| `Mevzuat Takip Portali.dc.html` | Sayfa kabuğu (`/`): simge sprite'ı + `assets/` betikleri |
| `assets/api.js` | **Tek sunucu katmanı**: `API_BASE=/api/v1`, `X-Demo-User` (URL kodlamalı; ad `localStorage['mtp_user']`), `If-Match`/`ETag`, `{error:{code,message,details}}` hata biçimi, API ↔ ekran kayıt dönüşümü (`upsert`) |
| `assets/inbox.js` | Gelen kutusu: kuyruklar, süzgeçler, liste, ayrıntı bölmesi, bilgi düzenleme formu |
| `assets/views.js` | Uygulama çubuğu, Arşiv, Excel karşılaştırma, Yönetim sekmeleri |
| `assets/app.js` | Çizim, tıklama/klavye olayları, API'ye yazma işlemleri, yönlendirme (`#gelen`, `#arsiv`, `#hat`, …) |
| `assets/styles.css` | Stiller |
| `eski/` | Önceki React arayüzü (bir sürüm yedek): `/eski`, `/eski/surec`, `/eski/arsiv`, `/eski/karsilastirma`. `support.js` ve `vendor/` yalnızca bunlar içindir |

Eski adresler yeni sayfaya yönlenir: `/surec → /#hat`, `/arsiv → /#arsiv`, `/karsilastirma → /#karsilastirma`,
`/admin → /#yonetim`.

Yerelde: `cd app && uvicorn app.api.main:app` → <http://localhost:8000/>. Uç noktalar için `API_REFERENCE.md`.

## Baseline sınırı

- **Yönetim → Bakım ve denetim → Baseline sınırı → Yeni sınır** alanında tarih, takvim simgesinden
  seçilebilir veya klavyeyle girilebilir. Görünen tarih biçimi tarayıcının dil ayarını izler.
- **Önizle** seçilen tarihin etkisini hesaplar; **Uygula** mevcut onay penceresinden sonra işlemi kuyruğa alır.
  Tarih değiştirilir veya temizlenirse eski önizleme kaldırılır; uygulamadan önce yeniden önizleme gerekir.

Baseline tarih alanı regresyon testleri (depo kökünden, ek paket gerektirmez):
`node --test mevzuat-core/portal/tests/baseline-date.test.cjs`

## Bildirimler

- Sağ üstteki zil menüsünde bir bildirime tıklamak, açık alarmların listelendiği **Yönetim → Genel bakış**
  sayfasına (`#yonetim`) yönlendirir. Bildirim bağlantıları klavyeyle de açılabilir.
- Bildirime veya **Tümünü gör** bağlantısına basınca menü kapanır; aynı sayfadayken de açık kalmaz.

Bildirim arayüzü regresyon testleri (depo kökünden, Node.js ile, ek paket gerektirmez):
`node --test mevzuat-core/portal/tests/notifications.test.cjs`

## Hata ve eşzamanlılık davranışı

- Karar, birim ve içerik yazmaları `If-Match: "<version>"` gönderir. **412 `version_mismatch`**: kayıt başkası tarafından
  değişmiştir; arayüz güncel kaydı yükler ve uzmandan kararı yeniden vermesini ister.
- **409** (`not_ready`, `already_decided`, `record_busy`, `already_reviewed`, …) ve 400 iş kuralı hataları sunucunun Türkçe
  iletisiyle gösterilir; kayıt/liste yenilenir. **403** yetki iletisi, ağ hatası "sunucuya ulaşılamadı" olarak gösterilir.
- Onayda birim seçimi değiştiyse önce `PUT /units`, sonra `POST /decision` çağrılır (ikisi de güncel sürümle).
- Toplu işlem en fazla 100 kayıt alır; sonuçta `done`/`skipped` nedenleriyle özetlenir.

## İşleme hattı

- Konumu değişmez: **Yönetim → İşleme hattı**, doğrudan `/#hat`.
- Eski süreç haritasının beş katmanlı SVG görünümü kullanılır: toplama, ham belge, düzenleme kaydı,
  YZ analizi ve portal incelemesi. Düğümler, canlı sayılar ve oklar `GET /pipeline` yanıtından çizilir;
  kesikli oklar yeniden deneme / işleme yollarını gösterir.
- **Teknik adlar** ve **Geçiş etiketleri** ayrı ayrı açılabilir. Bir düğüm seçilince bağlantılı geçişler
  vurgulanır; sağ panelde açıklama, ölçümler, geçişler ve en yeni kayıtlar görünür.
- Düğümler fare, Tab ve Enter / Boşluk ile seçilir. Dar ekranda yalnız harita yatay kayar, panel alta geçer.
  Yenilemede seçim, klavye odağı ve haritanın yatay kaydırma konumu korunur.
- Mevcut 30 saniyelik otomatik yenileme, Mermaid kopyalama ve yönetim eylemleri korunur.
  Kayıt listesi yüklenemezse hata ve yeniden deneme düğmesi gösterilir; boş liste gibi sunulmaz.

Regresyon testleri (depo kökünden, Node.js ile, ek paket gerektirmez):
`node --test mevzuat-core/portal/tests/pipeline.test.cjs`

## Excel'e aktarma

- **Excel'e aktar** penceresinde varsayılan **Filtrelenmiş liste** veya **Yalnızca seçilenler** kapsamı seçilir.
  Seçili kayıt yoksa ikinci seçenek pasiftir.
- Filtrelenmiş liste yalnızca açık sayfayı değil, kuyruk ve süzgeçlere uyan tüm sayfaları kapsar.
  Yalnızca seçilenler, sayfalar arasında biriken seçimi aynı kuyruk ve süzgeçlerle sınırlar.
  Her iki kapsamda mevcut sıralama ve en fazla 5.000 kayıt sınırı korunur.
- İndirme hatası pencerede gösterilir ve yeniden denenebilir; sınır nedeniyle kesilen dosya için uyarı gösterilir.

Dışa aktarma regresyon testleri (depo kökünden, ek paket gerektirmez):
`node --test mevzuat-core/portal/tests/export.test.cjs`

## Sıralama

- Gelen kutusunun Yeni, Birikim ve Tümü kuyruklarında öncelik, tespit tarihi, yürürlük tarihi ve önem için
  **Küçükten büyüğe / Büyükten küçüğe** yönü seçilebilir. Tarihlerde küçükten büyüğe eskiden yeniye sıralar.
- Yürürlük veya Önem sütun başlığına tekrar tıklamak aynı sütunun sıralama yönünü tersine çevirir;
  ok ve erişilebilir sütun bilgisi geçerli yönü gösterir.
- Ölçüt değiştiğinde mevcut varsayılanlar korunur: yürürlük tarihi artan, diğer ölçütler azalan.
  Sıralama değişince ilk sayfaya dönülür; Excel'e aktarım aynı ölçüt ve yönü kullanır.
- YZ hazırlıyor kuyruğunun sabit bekleme sırası ve ilgisiz örnekleminin sunucu sırası değişmez.

Sıralama arayüzü regresyon testleri (depo kökünden, Node.js ile, ek paket gerektirmez):
`node --test mevzuat-core/portal/tests/sorting.test.cjs`

## Prototipte olup API'de karşılığı olmayan alanlar (arayüzden çıkarıldı ya da sadeleştirildi)

Uydurma veri bırakılmadı; aşağıdakiler ya hiç gösterilmez ya da API'nin verdiği kadarıyla gösterilir.

| Prototip | Durum |
|---|---|
| Sayısal güven skoru (`0,93`) | API yalnızca bandı verir (`confidence`: Yüksek/Orta/Düşük); yalnızca band gösterilir |
| **Önemi uzmanın değiştirmesi** (`sevUser`) | Önem için yazma ucu yok; seçici kaldırıldı (önem salt okunur, gerekçesi ayrıntıda) |
| Olası mükerrer **Birleştir / Farklı kayıtlar** | Birleştirme ucu yok; uyarı ve benzer kaydı açma bağlantısı kalır, mükerrerse "Mükerrer kayıt" gerekçesiyle ret önerilir |
| Karar / bilgi düzenleme **Geri al** (toast) | Karşılığı yok; arşivde "Kararı düzenle" ve "Gelen kutusuna geri al" (gerekçeli) kullanılır |
| **Okundu** durumu | Yalnızca kullanıcı başına "görüntülendi" olayı (`viewed`); toplu okundu = her kayda `POST /views` |
| Birime iletme / takip ekranı | Kapsam dışı (yalnızca YZ birim önerisini onayla/reddet) |
| İlgisiz arşivinde **"İlgili say"** (kararı geri alma) | Yalnızca örneklemde (`irrelevant-review`) var; incelenmiş ilgisiz kayıtlar arşivde salt okunur |
| Arşivde **"Aynen onaylanan"** süzgeci | Sunucuda yalnız `unit_changed=true` süzgeci var; "Birimi değiştirilerek onaylanan" olarak sunulur, aynen onaylanan sayısı `decision-stats`'tan gelir |
| Arşiv listesinde ret gerekçesi | Liste yanıtında yok; kayıt açılınca denetim izinden gösterilir |
| Arşivde "Tümü" (onay + ret birlikte) | `status` tek değer alır; Onaylanan / Reddedilen ayrı sekmeler |
| Süzgeç açılırlarında seçenek başına kayıt sayısı | Çok boyutlu sayım ucu yok; seçenekler sayısız listelenir |
| KPI "Birim atanmalı / 30 gün içinde" | Kuyruk için `needs_unit` / `effective` süzgeçli `page_size=1` çağrılarının `total` değeri |
| Yönetim: LLM gecikmesi **hedef çizgisi ve zaman serisi grafikleri** | Zaman serisi ve hedef değeri yok; P95/ortanca/çağrı/hata sayısı gösterilir |
| Yönetim: **tarama başarı oranı**, "bekleyen aktarım" geçmişi | Oran ucu yok; kaynak sağlığı (`/sources/health`) ve `pendingIngest` gösterilir |
| İşleme hattı: yeniden deneme yolları anahtarı, hatta kayıt başına "sonraki deneme saati" | Yok; hat `GET /pipeline` düğümleri ve `reviewFlow` ile çizilir, düğüm renkleri **arayüz eşiğidir** (en eski bekleyen ≥ 3 gün dikkat, ≥ 7 gün darboğaz; `HAT_WARN_DAYS`/`HAT_CRIT_DAYS`) |
| Yönetim: son denetim olayları listesi | Genel denetim listesi ucu yok; kayıt bazında Geçmiş sekmesi ve "Zinciri doğrula" var |
| Arka plan işi **"Devam ettir"** | Yok; başarısızlar "Yeniden dene" ile tekrar çalıştırılır |
| Ayar etki önizlemesindeki "412 kayıt" gibi sabit sayılar | Önizleme `POST /admin/settings/preview` sonucudur (son 30 gün) |
| Excel karşılaştırma: eşikleri sayfada anında uygulama | Karşılaştırma sunucuda yapılır; eşik değişince **Karşılaştır** yeniden çalıştırılır (dosya tarayıcıda tutulur) |
| Kurumsal kullanıcı bilgisi (AD, birim, rol, oturum saati) | `GET /me` (ad, unvan, roller); SSO ayrı fazda, şimdilik `X-Demo-User` |

Sunucuda ayrıca yapılan küçük bir değişiklik: karar düzeltmesinde (`decision_revised`) denetim izi öğesi artık
`rejectionReason` taşır (ret gerekçesi Geçmiş ve başlık bandında görünsün diye).
