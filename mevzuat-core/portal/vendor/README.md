# portal/vendor — üçüncü taraf betikler

> Yeni arayüz (`assets/`) hiçbir üçüncü taraf betik kullanmaz. Bu klasör yalnızca yedekteki eski React arayüzü (`portal/eski/`, `/eski`) içindir.

Portal kurum ağında dış CDN'e (unpkg.com, Google Fonts) çıkmaz; bu dosyalar API tarafından `/vendor/<ad>` adresinden
sunulur (`app/api/main.py` → `vendor_file`; yalnızca düz dosya adı, `.js`/`.css`/`.map`). Sürümler sabittir, dosya
içeriği değiştirilmez.

| Dosya | Paket | Sürüm | Kaynak | Doğrulama |
|---|---|---|---|---|
| `react.production.min.js` | react | 18.3.1 | `https://unpkg.com/react@18.3.1/umd/react.production.min.js` | `sha384-DGyLxAyjq0f9SPpVevD6IgztCFlnMF6oW/XQGmfe+IsZ8TqEiDrcHkMLKI6fiB/Z` (support.js'teki SRI ile aynı) |
| `react-dom.production.min.js` | react-dom | 18.3.1 | `https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js` | `sha384-gTGxhz21lVGYNMcdJOyq01Edg0jhn/c22nsx0kyqP0TxaV5WVdsSH1fSDUf5YJj1` (support.js'teki SRI ile aynı) |
| `lucide.min.js` | lucide | 1.58.0 | `https://unpkg.com/lucide@1.58.0/dist/umd/lucide.min.js` | `sha384-ZemqnyvuKtPJISxDW78AAL1B4QhqxJ0SeEj42qO/4SLmpu+yFCcErD98G2F5vov3` (indirme anında hesaplandı) |

React dosyaları `support.js`'in `window.__resources` desteğiyle yönlendirilir (`Mevzuat Takip Portali.dc.html` başındaki
betik; anahtarlar `support.js` içindeki unpkg adresleriyle birebir aynı olmalı). Lucide, portalın `<helmet>` bölümünden
yüklenir. Eskiden `lucide@latest` kullanılıyordu (sabitlenmemiş dış betik).

Güncelleme: yeni sürümü indirin, özetini hesaplayın (`openssl dgst -sha384 -binary <dosya> | openssl base64 -A`), bu tabloyu
ve gerekirse `window.__resources` anahtarlarını güncelleyin. Lucide'ı yükseltirken portalda kullanılan simge adlarının
(`data-lucide="…"`, ayrıca API'nin `auditTrail[].icon` değerleri) yeni sürümde bulunduğunu doğrulayın.
