# 09 — API kılavuzu: backend servislerinin UI tarafından kullanımı

**Hedef okuyucu:** portala yeni ekran/özellik ekleyecek ya da Albatros gibi başka bir istemciyi bağlayacak geliştirici.
**Süre:** 1–1,5 saat. **Sonunda:** hangi ekranın hangi uç noktayı, hangi başlık ve gövdeyle çağırdığını, hangi
hatanın ne anlama geldiğini ve UI'nın bu hatayı nasıl gösterdiğini koda bakmadan anlatabilirsiniz.

> Bu belgedeki yanıt örnekleri `mevzuat-core` test verisiyle çalıştırılan gerçek uygulamadan alındı (uzun alanlar
> `…` ile kısaltıldı, kimlik/tarih değerleri örnektir). Kod değişirse kod esastır. Uç nokta listesinin
> makine üretimli kısa özeti için `mevzuat-core/API_REFERENCE.md`, etkileşimli şema için `/docs` ve `/openapi.json`.

İçindekiler: [1 Mimari](#1-mimari-ve-genel-kurallar) · [2 Kimlik ve roller](#2-kimlik-ve-roller) ·
[3 Hata biçimi](#3-hata-biçimi-ve-durum-kodları) · [4 İyimser kilit](#4-iyimser-kilit-etag--if-match) ·
[5 Ekran–uç nokta haritası](#5-ekran--uç-nokta-haritası) · [6 Portal ana ekranı](#6-portal-ana-ekranı-) ·
[7 Kayıt ayrıntısı ve karar](#7-kayıt-ayrıntısı-ve-karar-akışı) · [8 Yönetim](#8-yönetim-sayfası-admin) ·
[9 Arşiv](#9-arşiv-arsiv) · [10 Süreç haritası](#10-süreç-haritası-surec) ·
[11 Karşılaştırma ve paralel çalışma](#11-karşılaştırma-karsilastirma-ve-paralel-çalışma) ·
[12 Sistem uçları](#12-sistem-uçları) · [13 Yeni ekran eklerken](#13-yeni-ekran-eklerken-kontrol-listesi)

---

## 1. Mimari ve genel kurallar

```
Tarayıcı (portal sayfaları, statik HTML + React/vanilla JS)
   │  fetch('/api/v1/...')   JSON · aynı origin
   ▼
FastAPI  (mevzuat-core/app/app/api/main.py)  ── uvicorn/gunicorn, port 8080
   │  Session (SQLAlchemy)            ┌─ PostgreSQL: regulation, raw_document, audit_event, job_run, …
   ├─────────────────────────────────►│
   │  BackgroundTasks / Celery        └─ (CRAWL_MODE=remote) MongoDB: crawl_requests, crawler_status
   ▼
services/*  (iş kuralları)  ·  ai/*  ·  collectors/*
```

| Konu | Kural | Kaynak |
|---|---|---|
| Önek | Her şey `/api/v1` altında; yönetim `/api/v1/admin`, paralel çalışma `/api/v1/evaluation` | `main.py` |
| Sayfalar | `/` → portal (tek sayfa, `/assets/*`); `/admin`, `/surec`, `/arsiv`, `/karsilastirma` → `/#yonetim`, `/#hat`, `/#arsiv`, `/#karsilastirma` yönlendirmesi; `/eski/*` yedekteki eski arayüz (`/support.js`, `/vendor/*`: yalnızca o sayfalar). Dış CDN yok | `create_app` |
| Aynı origin | Sayfalar API ile aynı sunucudan gelir; `window.MTP_API_BASE` ile başka bir adrese yönlendirilebilir. CORS yalnız `CORS_ORIGINS` (varsayılan `http://localhost:8080`) için açılır, `ETag` dışa açıktır | `settings.py`, `create_app` |
| İçerik türü | İstek ve yanıt JSON (UTF-8). İstisnalar: Excel indirme/yükleme (`.xlsx`, `multipart/form-data`) ve `GET /pipeline?format=mermaid` (`text/plain`) | |
| Alan adları | Yanıtlarda **camelCase** (`publishDate`, `suggestedUnits`); istek gövdesi ve sorgu parametrelerinde **snake_case** (`unit_codes`, `page_size`, `rejection_reason`) | |
| Tarihler | Yalnız tarih: `2026-09-19`. Zaman damgası: ISO-8601 + saat dilimi (`2026-10-20T10:36:52+00:00`); UI `tr-TR` biçimine çevirir | |
| Sayfalama | Sunucu tarafı: `page` (≥1), `page_size` (1–100). Süzgeç ve sıralama da sunucuda yapılır, UI yalnız geçerli sayfayı tutar | |
| Güvenlik başlıkları | Her yanıtta `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `frame-ancestors 'none'` | `SECURITY_HEADERS` |
| Yazma | Her yazma tek transaction'dır ve **denetim izi** olayı (hash zincirli) üretir; karar ayrıca `outbox` kaydı bırakır | `services/regulations.py` |

Uzun süren işler (yeniden işleme, tarama, geçmiş tarama…) **asenkrondur**: uç nokta `202 Accepted` ve bir `jobId`
(ya da `requestId`) döner; ilerleme `GET /admin/scans` ile izlenir (bkz. §8.2).

---

## 2. Kimlik ve roller

Şu an **kimlik doğrulama yoktur**: herkes `mevzuat_viewer`, `mevzuat_expert` ve `mevzuat_admin` rollerinin hepsine sahiptir.
Rol denetimi kodda durur (`app/api/auth.py: require`), ileride bir kimlik sağlayıcı eklenirse yalnız `current_actor` değişir.

Kullanıcı adı **`X-Demo-User`** başlığından gelir (yoksa `DEMO_USER_NAME`, varsayılan `Demo Kullanıcı`; unvan
`DEMO_USER_TITLE`, varsayılan `Uyum Uzmanı`). HTTP başlıkları Türkçe karakter taşıyamadığından değer **URL kodlamalı** gönderilir.

```http
GET /api/v1/me HTTP/1.1
X-Demo-User: Ay%C5%9Fe%20Kaya
```
```json
{ "name": "Ayşe Kaya", "title": "Uyum Uzmanı",
  "roles": ["mevzuat_admin", "mevzuat_expert", "mevzuat_viewer"],
  "canDecide": true, "isAdmin": true }
```

| Rol | Hiyerarşi | Gereken uçlar |
|---|---|---|
| `mevzuat_viewer` | en düşük | Tüm `GET`'ler, `POST /views`, `excel-compare` |
| `mevzuat_expert` | viewer ⊂ expert | `decision`, `units`, `content`, `manual-detections` (yazma) |
| `mevzuat_admin` | expert ⊂ admin | `/api/v1/admin/*` (tamamı) |

**UI davranışı** (`portal/assets/*.js`; eski React sayfasında da aynıydı):

- Kullanıcı adı tarayıcıda `localStorage['mtp_user']` içinde tutulur ve **her** `fetch`'e `X-Demo-User` olarak eklenir.
  Başlıktaki "adınız" düğmesi (`changeUser`) değeri değiştirir ve meta + listeyi yeniden yükler.
- `/me` yanıtındaki `canDecide` ve `isAdmin`, onay/red düğmelerinin ve **Yönetim** sekmesinin görünürlüğünü belirler.
  Yetki yine de sunucuda denetlenir; yetkisiz çağrı `403 forbidden` döner.
- Ad **denetim izine** yazılır ve "okunmamış" işareti kullanıcıya özeldir (`viewed` olayı, §7.1). Ad değişince okunmamışlar yeniden hesaplanır.
- Yan sayfalar (`/arsiv`, `/surec`, `/karsilastirma`) başlık göndermez; salt okunur oldukları için varsayılan kullanıcıyla çalışırlar.

---

## 3. Hata biçimi ve durum kodları

Tüm hatalar aynı zarfla döner:

```json
{ "error": { "code": "invalid_status", "message": "status: Bekliyor, Onaylandı, Reddedildi", "details": {} } }
```

`message` kullanıcıya gösterilebilir Türkçe metindir; `code` makine okunur; `details` duruma göre doludur.

| HTTP | `code` (örnekler) | Ne zaman | UI tepkisi |
|---|---|---|---|
| 400 | `invalid_status`, `invalid_metric`, `invalid_kind`, `invalid_range`, `invalid_cutoff`, `llm_required`, `unknown_unit`, `units_required`, `empty_update`, `empty_content`, `missing_rejection_reason`, `missing_rejection_details`, `unsupported_file`, `invalid_file`, `empty_file`, `page_too_deep` | İş kuralı ihlali | Toast: `Hata: <message>` |
| 400 | `invalid_setting` | Ayar doğrulaması; `details.fields = {anahtar: ileti}` | Alan bazlı hata, form kalır |
| 403 | `forbidden` | Rol yetersiz | Toast |
| 404 | `not_found` | Kayıt/kaynak/düğüm yok ya da portalda görünmüyor | Toast; ayrıntı açıksa liste |
| 409 | `already_decided`, `summary_unavailable`, `rebaseline_running`, `record_busy` | Durum çakışması | Toast + kayıt **yeniden yüklenir** |
| 412 | `version_mismatch` | `If-Match` sürümü bayat; `details.current_version` | Toast + kayıt **yeniden yüklenir** |
| 413 | `too_large` | Yüklenen dosya > 5 MB | Uyarı şeridi |
| 422 | `validation_error` | Şema/doğrulama (`details.errors[]`: `loc`, `msg`, `type`) | Toast |
| 503 | — | Yalnız `/readyz`: veritabanına erişilemiyor | — |

Örnek 422 (sorgu parametresi sınırı aşıldı):

```json
{ "error": { "code": "validation_error", "message": "geçersiz istek",
  "details": { "errors": [ { "type": "less_than_equal", "loc": ["query", "page_size"],
                             "msg": "Input should be less than or equal to 100", "ctx": "{'le': 100}" } ] } } }
```

> **Dikkat:** reddetme nedeni gibi gövde doğrulamaları `DecisionIn` doğrulayıcısında yakalandığı için `400` değil
> **`422 validation_error`** döner (ör. `reject` + `rejection_reason` yok → `message: "geçersiz istek"`,
> `details.errors[0].msg: "Value error, reddetme nedeni zorunludur"`). UI bunu zaten gönderimden önce engeller.

UI'daki ortak sarmalayıcı (`api()` fonksiyonu, her sayfada benzer) hatayı `Error`'a çevirir: `err.status`, `err.code`
ve `err.fields` (= `details.fields`). Gövde JSON değilse `HTTP <kod>` metnine düşer.

---

## 4. İyimser kilit: `ETag` / `If-Match`

İki uzman aynı kaydı aynı anda değiştirmesin diye kayıtta bir **`row_version`** sayacı vardır (yanıtta `version`).

1. `GET /regulations/{id}` yanıtı `ETag: "1"` başlığını da döner. UI bunu `detailEtag` olarak saklar.
2. Yazan uçlar (`decision`, `units`, `content`) bu değeri **`If-Match: "1"`** olarak ister ve başarılı yazmadan sonra
   **yeni `ETag`** döner (`"2"`). UI ayrıntıyı bu yanıttan yeniler.
3. Sürüm uyuşmazsa `412 version_mismatch` + `details.current_version`. `If-Match` hiç gönderilmezse denetim atlanır
   (ör. betikler için), yalnız "kayıt zaten karara bağlandı" denetimi (409) çalışır.
4. Aynı anda gelen iki yazma satır kilidiyle (`SELECT … FOR UPDATE`) sıraya girer: ikincisi birincisini bekler, güncel
   kaydı görür ve 409/412 alır. YZ işçisi satırı tutuyorsa 5 sn sonra `409 record_busy` ("birkaç saniye sonra yeniden deneyin").

UI'nın hata yolu (`writeFailed`): 409/412 ise `"<message> Kayıt yenilendi."` toast'ı gösterir ve ayrıntıyı yeniden yükler;
kullanıcı güncel durumu görüp kararını yeniden verir.

---

## 5. Ekran → uç nokta haritası

| Ekran (adres) | Dosya | Çağırdığı uçlar |
|---|---|---|
| **Gelen kutusu** (`/#gelen`) | `portal/assets/{api,inbox,app}.js` | `GET /me`, `/sources`, `/units`, `/sources/health` (açılışta) · `GET /regulations/stats` · `GET /regulations?queue=…` · `GET /regulations/export` · `GET /regulations/irrelevant-sample` |
| **Ayrıntı bölmesi** | aynı | `GET /regulations/{id}` · `GET …/document` · `POST …/views` · `POST …/decision` · `PUT …/units` · `PATCH …/content` · `POST /regulations/bulk-decision` · `POST …/irrelevant-review` · `GET /archive/regulations/{id}` |
| **Arşiv** (`/#arsiv`) | `portal/assets/views.js` | `GET /regulations?status=…` · `GET /regulations/decision-stats` · `POST …/decision/revise`, `…/decision/reopen` · `GET /archive/regulations?kind=irrelevant` |
| **Yönetim** (`/#yonetim`, `/#hat`, `/#kaynaklar`, `/#ayarlar`, `/#bakim`) | `portal/assets/views.js` | `GET /pipeline`, `/pipeline/nodes/{id}` · `GET /admin/metrics`, `/admin/scans`, `/admin/alerts` + `…/resolve` · `GET/PUT /admin/settings` (+`/history`, `/preview`) · `POST /admin/sources/{kod}/run`, `…/backfill` · `POST /admin/reprocess` (`dry_run`), `/admin/retry-failed` · `GET/POST /admin/rebaseline` · `GET /admin/audit/verify` |
| **Excel karşılaştırma** (`/#karsilastirma`) | `portal/assets/views.js` | `POST /evaluation/excel-compare` (JSON ve `format=xlsx`) |
| *UI'sı yok (API/CLI)* | — | `POST/GET /evaluation/manual-detections`, `POST …/upload`, `GET /evaluation/report`, `GET /evaluation/filtered-out`, `POST /admin/regulations/{id}/split`, `…/regenerate`, `POST /admin/monitor/run` |
| Altyapı | — | `GET /healthz`, `GET /readyz` |

---

## 6. Portal ana ekranı (`/`)

### 6.1 Açılış: meta verisi

`componentDidMount` → `loadMeta` dört isteği **paralel** atar (`Promise.all`) ve ardından `stats` ile `regulations` yüklenir.
Süzgeç/alan adresten (`?source=BDDK&severity=Kritik&q=…`) okunur, böylece bağlantı paylaşılabilir.

| İstek | Kullanım |
|---|---|
| `GET /sources` | Kaynak süzgeci açılır listesi (`code`, `name`; yalnız `sources.yaml`'daki **etkin** kaynaklar) |
| `GET /units` | Birim süzgeci ve "birim değiştir" penceresi (`code`, `name`, `group`; yalnız aktif birimler, ada göre) |
| `GET /sources/health` | Üst durum çubuğu / uyarı şeridi (§6.5) |
| `GET /me` | Yetkiye göre düğmeler (§2) |

```http
GET /api/v1/sources
```
```json
[ { "code": "RESMI_GAZETE", "name": "Resmî Gazete" },
  { "code": "BDDK", "name": "Bankacılık Düzenleme ve Denetleme Kurumu" }, … ]
```
```http
GET /api/v1/units
```
```json
[ { "code": "AKTIF_PASIF", "name": "Aktif-Pasif Yönetimi Müdürlüğü", "group": "Hazine ve Uluslararası Bankacılık GMY" },
  { "code": "RISK_YONETIMI", "name": "Risk Yönetimi Başkanlığı", "group": "…" }, … ]
```

### 6.2 Sayaç kartları — `GET /regulations/stats`

Portal üstündeki dört kart. Kart tıklanınca aynı kavram **`metric`** parametresiyle listeye süzgeç olur (§6.3).
UI sayaçları 2 dakikada bir tazeler (sekme görünürken); liste kendiliğinden değişmez ki satırlar kaymasın.

```json
{ "pending": 1, "criticalHighPending": 0, "today": 1, "upcoming": 0 }
```

| Alan | Anlam | Karşılık gelen `metric` |
|---|---|---|
| `pending` | Karar bekleyen (`Bekliyor`) | `pending` |
| `criticalHighPending` | Bekleyen **ve** önemi Kritik/Yüksek | `critical_high` (`criticalHigh` de kabul edilir) |
| `today` | Bugün (UTC değil **`TIMEZONE`** gününe göre) tespit edilen | `today` |
| `upcoming` | Yürürlüğe girmesine `EFFECTIVE_SOON_DAYS` (30) gün veya daha az kalan | `upcoming` |

### 6.3 Liste — `GET /regulations`

Portalda yalnız **ilgili** (`is_relevant`) ve birleştirilmemiş kayıtlar görünür. Analizi tamamlanmamış olanlar
(`SUMMARIZED`, `AI_FAILED` …) da gösterilir; durum `analysisStatus` alanında yazar ("yüksek duyarlılık" ilkesi:
analiz aşamasında takılan bir kayıt Başkanlıktan gizlenmez).

**Sorgu parametreleri**

| Parametre | Değer | Açıklama |
|---|---|---|
| `source` | kaynak kodu (`BDDK`) | Kaydın bağlı olduğu ham belgelerden **herhangi** biri bu kaynaktan |
| `severity` | `Kritik` · `Yüksek` · `Orta` · `Düşük` | |
| `status` | `Bekliyor` · `Onaylandı` · `Reddedildi` | Başka değer `400 invalid_status` |
| `unit` | birim kodu | Aktif birim önerisi bu birim olan kayıtlar |
| `date_range` | `7` · `30` | Son N gün içinde **yayımlanan** |
| `q` | serbest metin | Başlıkta ve güncel özette arar; Türkçe büyük/küçük harf (İ/ı) katlaması uygulanır, `%`/`_` joker sayılmaz |
| `metric` | `pending` · `critical_high` · `today` · `upcoming` | Sayaç kartı süzgeci (§6.2) |
| `page`, `page_size` | 1…, 1–100 (varsayılan 10) | UI 10/25/50 sunar |
| `sort` | `issuer` · `unit` · `title` · `publish_date` · `effective_date` · `severity` · `status` | Verilmezse: önem (Kritik→Düşük), sonra yayım tarihi (yeni→eski) |
| `direction` | `asc` · `desc` (varsayılan `desc`) | |

```http
GET /api/v1/regulations?severity=Orta&sort=publish_date&direction=desc&page=1&page_size=10
X-Demo-User: Ay%C5%9Fe%20Kaya
```
```json
{
  "items": [
    {
      "id": 1,
      "title": "Bankacılık Düzenleme ve Denetleme Kurulunun 18/09/2026 Tarihli ve 11572 Sayılı Kararı",
      "issuer": "Bankacılık Düzenleme ve Denetleme Kurumu",
      "issuerCode": "BDDK",
      "sources": ["BDDK", "RESMI_GAZETE"],
      "type": "Kurul Kararı",
      "publishDate": "2026-09-19",
      "effectiveDate": null,
      "effectiveSoon": false,
      "isToday": true,
      "severity": "Orta",
      "status": "Bekliyor",
      "confidence": "Yüksek",
      "sourceUrl": "https://www.resmigazete.gov.tr/eskiler/2026/09/20260919-5.pdf",
      "summary": "BDDK, Bank Mellat İstanbul şubesinin faaliyet iznini kaldırdı.",
      "topics": ["Faaliyet izni"],
      "suggestedUnits": [
        { "code": "RISK_YONETIMI", "unit": "Risk Yönetimi Başkanlığı", "reason": "Karşı taraf.",
          "matchedResponsibility": "Banka ve finansal kuruluş karşı taraf risk limitlerinin belirlenmesi ve izlenmesi",
          "score": 0.9, "origin": "ai", "isFinal": false } ],
      "decidedBy": null,
      "analysisStatus": "hazır",
      "detectedAt": "2026-10-20T10:36:52.007563+00:00",
      "version": 1,
      "viewed": false
    }
  ],
  "total": 1, "page": 1, "pageSize": 10
}
```

**Alan sözlüğü (liste ve ayrıntı ortak)**

| Alan | Tür | Açıklama |
|---|---|---|
| `id` | int | Düzenleme kaydı kimliği; adresteki `#kayit=<id>` |
| `title`, `issuer`, `issuerCode` | string | `issuer` görünen ad, `issuerCode` kısa kod (`BDDK`) |
| `sources` | string[] | Bağlı ham belgelerin kaynak kodları (sıralı, tekil) |
| `type` | string\|null | YZ'nin atadığı tür, yoksa kaynak türü |
| `publishDate`, `effectiveDate` | `YYYY-MM-DD`\|null | Yayım / yürürlük |
| `effectiveSoon` | bool | Yürürlük `EFFECTIVE_SOON_DAYS` içinde → UI satırı vurgular |
| `isToday` | bool | Bugün tespit edildi |
| `severity` | `Kritik`\|`Yüksek`\|`Orta`\|`Düşük`\|null | YZ önem derecesi |
| `status` | `Bekliyor`\|`Onaylandı`\|`Reddedildi` | **İnceleme** durumu (YZ işleme durumundan ayrıdır) |
| `confidence` | `Yüksek`\|`Orta`\|`Düşük` | YZ güven bandı (eşikler Yönetim → Ayarlar'da) |
| `sourceUrl` | string\|null | Birincil bağlantı; Resmî Gazete varsa o tercih edilir |
| `summary`, `topics` | string\|null, string[] | Kaynak alıntılarıyla doğrulanmış güncel özet ve konular |
| `suggestedUnits[]` | | `origin`: `ai` (YZ) veya `manual` (uzman); `isFinal`: onayla kesinleşti; `score`: manuelde `null` |
| `decidedBy` | object\|null | `{name, title, at, decision}` — karar verilmişse |
| `analysisStatus` | string | `hazır` · `analiz sürüyor` · `metin bekleniyor (OCR)` · `YZ analizi başarısız, yeniden denenecek` |
| `detectedAt` | ISO | Sistemin kaydı oluşturduğu an |
| `version` | int | `row_version` — `ETag` ile aynı sayı (§4) |
| `viewed` | bool | **Yalnız listede.** Bu kullanıcı ayrıntıyı açtı mı (okunmamış satır kalın yazılır) |

UI notları (`toRecord`, `filterParams`, `loadList`):

- Filtre değişince `page=1`'e dönülür; aramada her tuş yerine **300 ms** durulunca istek atılır.
- Aynı anda birden fazla liste isteği uçabileceği için sıra numarası (`_listSeq`) tutulur; **sırasız gelen eski yanıt yok sayılır**.
- `summary` boşsa kartta `Özet henüz hazır değil (<analysisStatus>).` yazılır; `severity` yoksa `—`.
- Sütun başlığı tıklaması sıralamayı döndürür: tarihte ilk tık yeniden eskiye, metinde A→Z; üçüncü tık sıralamayı kaldırır.

### 6.4 Excel dışa aktarma — `GET /regulations/export`

Liste ile **aynı süzgeç/sıralama parametrelerini** alır (sayfalama hariç). Yanıt `.xlsx`'tir, en fazla **5000** satır.

```http
GET /api/v1/regulations/export?status=Bekliyor&severity=Kritik
```
```http
HTTP/1.1 200 OK
Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
Content-Disposition: attachment; filename="mevzuat-2026-10-20.xlsx"
X-Export-Total: 1
X-Export-Truncated: false
Access-Control-Expose-Headers: Content-Disposition, X-Export-Total, X-Export-Truncated
```

Sütunlar: Kurum, Başlık, Tür, Yayım tarihi, Yürürlük tarihi, Önem, Durum, Önerilen birimler, Özet, Konular, Kaynak,
Bağlantı, Karar veren. `=`, `+`, `-`, `@` ile başlayan hücreler başına `'` konarak **formül enjeksiyonu** engellenir.

UI (`exportExcel`): `fetch` → `res.blob()` → gizli `<a download>` ile indirir; dosya adını `Content-Disposition`'dan alır.
`X-Export-Truncated: true` ise toast: *"Excel'e yalnızca ilk 5000 kayıt aktarıldı (toplam N); süzgeçle daraltın."*

### 6.5 Kaynak sağlığı — `GET /sources/health`

Üst durum çubuğunu ve uyarı şeridini besler (2 dakikada bir tazelenir). Göstergeleme **asimetriktir**: sorun yoksa sade,
varsa açıklayıcı bir şerit çıkar.

```json
{
  "overall": "down",
  "bannerText": "Resmî Gazete: Henüz tarama yapılmadı · BDDK: Henüz tarama yapılmadı",
  "sources": [
    { "code": "BDDK", "name": "Bankacılık Düzenleme ve Denetleme Kurumu", "status": "down",
      "lastSuccessAt": null, "lastItemAt": "2026-09-25T18:00:00+00:00", "lastFetch": "hiç",
      "message": "Henüz tarama yapılmadı", "toleranceHours": 120.0, "toleranceBasis": "yapılandırma" } ],
  "warnings": [ { "type": "structure_change", "source": "SPK", "message": "…" } ]
}
```

| Alan | Değerler |
|---|---|
| `overall` | `healthy` · `delayed` · `down` (en kötü kaynak; crawler/aktarım sorunları da dahil) |
| `sources[].status` | `healthy` · `delayed` · `down` |
| `toleranceHours` / `toleranceBasis` | Bu süre aşılırsa kaynak gecikmiş sayılır; dayanak `yapılandırma` (sources.yaml) ya da gözlenen yayın sıklığı |
| `warnings[]` | Durumu bozmayan bilgilendirmeler (`structure_change`, `volume_high`, `ai_failure`…) |

---

## 7. Kayıt ayrıntısı ve karar akışı

Bir satıra tıklanınca (`openDetail`) iki istek atılır: ayrıntı **ve** "okundu" işareti. Adres `/?…#kayit=<id>` olur; Geri
düğmesi listeye döner, önceki/sonraki okları (`stepRecord`) adresi `replace` ile günceller.

### 7.1 Ayrıntı — `GET /regulations/{id}`

Liste alanlarına ek olarak şunlar gelir. Yanıt başlığında **`ETag: "<version>"`** vardır (§4).

```json
{
  "id": 1, "title": "…", "status": "Bekliyor", "version": 1,
  "auditTrail": [
    { "action": "YZ tarafından tespit edildi", "actor": "Mevzuat Takip Motoru",
      "timestamp": "2026-10-20T10:36:52+00:00", "icon": "bot", "note": null,
      "eventType": "detected", "rejectionReason": null } ],
  "aiGeneratedFields": ["type", "severity", "confidence", "summary", "topics", "suggestedUnits", "effectiveDate"],
  "sourceLinks": [
    { "label": "Resmî Gazete", "url": "https://www.resmigazete.gov.tr/eskiler/2026/09/20260919-5.pdf" },
    { "label": "Yayım sayfası (BDDK)", "url": "https://www.bddk.org.tr/Duyuru/Detay/3304" } ],
  "effectiveDateText": null,
  "relevance": { "score": 0.89, "rationale": "Bankayı doğrudan ilgilendiriyor.", "uncertain": false, "criteria": [] },
  "severityRationale": "…",
  "evidence": { "summary": [ { "sentence": "…", "evidence": [ { "method": "exact", "…": "…" } ] } ],
                "topics": [], "effectiveDate": null },
  "groundingScore": 1.0,
  "removedClaims": [],
  "summaryVersion": 1,
  "unitMatch": {},
  "decisionNote": null
}
```

| Ek alan | UI'da |
|---|---|
| `auditTrail[]` | "Denetim izi" sekmesi; `icon` bir lucide ikon adıdır (`bot`, `eye`, `check-circle-2`, `x-circle`, `shuffle`, `pencil`, `refresh-cw`, `rotate-cw`) |
| `aiGeneratedFields` | Hangi alanlarda "YZ üretti" rozeti gösterileceği |
| `relevance`, `severityRationale` | "Neden ilgili / neden bu önem?" — uzman YZ çıktısını gerekçesini görerek onaylar |
| `evidence`, `groundingScore` | Özet cümlelerinin kaynak alıntıları; `removedClaims[]` kaynakta doğrulanamadığı için özetten **çıkarılmış** ifadelerdir (`kind`: `sentence` · `topic` · `effective_date`) |
| `sourceLinks[]` | Kaynak bağlantıları listesi |
| `decisionNote` | Karar notu |

Ayrıntı açılırken, listedeki `viewed` bayrağı UI'da hemen iyimser olarak `true` yapılır ve sunucuya yazılır:

```http
POST /api/v1/regulations/1/views
```
```json
{ "created": true }
```

`created` yalnız kullanıcının **ilk** açışında `true` olur (denetim izine "İncelemeye alındı" yazılır); sonrakilerde
`false` ve yeni olay yok. Hata sessizce yutulur (`.catch(() => {})`) — okundu işareti kritik değildir.

Portalda görünmeyen (ilgisiz, birleştirilmiş, baseline) bir kimlik `404 not_found` döner; bunlar için §9'daki arşiv uçları kullanılır.

### 7.2 Karar — `POST /regulations/{id}/decision` *(expert)*

**İstek gövdesi**

| Alan | Zorunlu | Kural |
|---|---|---|
| `decision` | evet | `approve` · `reject` |
| `note` | hayır | ≤ 2000 karakter; `rejection_reason=other` ise **zorunlu** |
| `rejection_reason` | `reject`'te evet | `not_banking_related` · `outdated` · `duplicate` · `other`; `approve` ile gönderilemez |

Sunucu davranışı: sürüm denetimi (`If-Match`) → kayıt `Bekliyor` mu? (değilse `409 already_decided`) → durumu günceller →
onayda birim önerilerini `isFinal=true` yapar ve birim kararını öğrenme havuzuna (few-shot) yazar → denetim olayı
(`approved`/`rejected`) → `outbox` kaydı (`record.approved` / `record.rejected`; ilgili birimlere iletim için).

```http
POST /api/v1/regulations/1/decision
Content-Type: application/json
If-Match: "2"
X-Demo-User: Ay%C5%9Fe%20Kaya

{ "decision": "approve", "note": "Takip edilecek." }
```
```http
HTTP/1.1 200 OK
ETag: "3"
```
```json
{
  "id": 1, "status": "Onaylandı", "version": 3, "decisionNote": "Takip edilecek.",
  "decidedBy": { "name": "Ayşe Kaya", "title": "Uyum Uzmanı", "at": "2026-10-20T10:36:52.631246+00:00",
                 "decision": "Onaylandı" },
  "suggestedUnits": [ { "code": "RISK_YONETIMI", "unit": "Risk Yönetimi Başkanlığı", "isFinal": true, "…": "…" } ],
  "auditTrail": [ …,
    { "action": "Onaylandı", "actor": "Ayşe Kaya · Uyum Uzmanı", "icon": "check-circle-2",
      "note": "Risk Yönetimi Başkanlığı birimine yönlendirildi. Not: Takip edilecek.", "eventType": "approved" } ]
}
```

Red örneği:

```json
{ "decision": "reject", "rejection_reason": "other", "note": "Kapsam dışı: yalnızca kurum içi duyuru." }
```

| Durum | Yanıt |
|---|---|
| Aynı kayda ikinci karar | `409 already_decided` — `"kayıt zaten 'Onaylandı' durumunda"` |
| Bayat `If-Match` | `412 version_mismatch`, `details.current_version` |
| `reject` + neden yok / `other` + not yok / `approve` + neden var | `422 validation_error` |

**UI akışı** (`doConfirm`): Onayla/Reddet düğmesi bir onay penceresi açar (red için neden seçimi + gerekirse not). `busy` bayrağı
çift tıklamayı engeller. Başarıda `afterWrite`: ayrıntıyı yanıttan ve yeni `ETag`'den yeniler, toast gösterir (*"Kayıt onaylandı."*),
sonra `stats` ve listeyi yeniden çeker. Hatada `writeFailed` (§4).

### 7.3 Birim değişikliği — `PUT /regulations/{id}/units` *(expert)*

Kayıt `Bekliyor` iken önerilen birimler değiştirilir. Liste **sıralıdır** (ilk eleman asıl birim).

```http
PUT /api/v1/regulations/1/units
If-Match: "2"

{ "unit_codes": ["RISK_YONETIMI", "HAZINE"], "note": "Hazine de bilgilendirilmeli." }
```

- `unit_codes`: 1–10 adet; tekrarlar atılır. Bilinmeyen/pasif kod → `400 unknown_unit` ve `details.unit_codes: ["YOK"]`.
- Yeni eklenenler `origin: "manual"`, `reason: "Uzman tarafından manuel olarak atandı."` ile gelir; mevcut YZ gerekçeleri korunur.
- Kodlar mevcutla aynıysa hiçbir şey yazılmaz (sürüm artmaz). Aksi halde denetim olayı `units_changed` ve öğrenme havuzuna
  ağırlığı **2** olan düzeltme örneği yazılır.
- Yanıt: güncel ayrıntı + yeni `ETag`.

### 7.4 İçerik düzeltme — `PATCH /regulations/{id}/content` *(expert)*

```http
PATCH /api/v1/regulations/1/content
If-Match: "1"

{ "title": "Yeni başlık" }
```

- `title` (1–1000) ve/veya `summary` (1–20000); ikisi de yoksa `400 empty_update`, boşluktan ibaret ise `400 empty_content`.
- Özet değişirse eskisi `is_current=false` olur, `method: "manual"` yeni bir özet sürümü yazılır (`summaryVersion` artar).
  Henüz YZ özeti yoksa `409 summary_unavailable`.
- Yanıt: ayrıntı, `ETag: "2"`; denetim olayı `content_edited` (*"Başlık güncellendi."*).
- UI (`saveContent`) yalnız **değişen** alanları gönderir; hiçbiri değişmediyse istek atmaz.

---

## 8. Yönetim sayfası (`/admin`)

`/admin` ve `/` aynı dosyayı sunar; adres `/admin` ise Yönetim görünümü açılır. Sekme yalnız `me.isAdmin` iken görünür.
`openAdmin` beş veriyi birlikte yükler: ayarlar, uyarılar, metrikler, taramalar, baseline sınırı.

### 8.1 Çalışma zamanı ayarları — `GET/PUT /admin/settings`

Yeniden dağıtım gerektirmeden değiştirilebilen YZ eşikleridir (veritabanında saklanır, `by` alanıyla kim değiştirdiği yazılır).

```http
GET /api/v1/admin/settings?detail=true
```
```json
{
  "values": { "relevance_threshold": 0.3, "confidence_high": 0.75, "confidence_medium": 0.45,
              "relevance_samples": 3, "dedupe_auto_threshold": 0.9, "dedupe_review_threshold": 0.7,
              "relevance_weight_model": 0.6, "relevance_weight_votes": 0.3, "relevance_weight_rules": 0.1 },
  "specs": {
    "relevance_threshold": { "type": "float", "min": 0, "max": 1,
                             "description": "Bu skorun altındaki kayıtlar portala düşmez (IRRELEVANT)" },
    "relevance_samples":   { "type": "int", "min": 1, "max": 9,
                             "description": "İlgililikte self-consistency örnek sayısı" } }
}
```

`?detail=true` olmadan yalnız düz `{anahtar: değer}` döner. `specs`, UI'nın formu (tip, alt/üst sınır, açıklama) **sunucudan
okuyarak** çizmesini sağlar: yeni ayar eklemek UI değişikliği gerektirmez.

```http
PUT /api/v1/admin/settings?detail=true
{ "values": { "relevance_threshold": "0.45" } }
```

- Değerler **dize** gönderilir; sunucu tipe çevirir. Başarıda `200` ve güncel `GET` gövdesi döner.
- Hata: tümü-ya-hiç (rollback). Geçersiz değer → `400 invalid_setting` ve **alan bazlı** ileti:

```json
{ "error": { "code": "invalid_setting",
  "message": "relevance_threshold: 0 ile 1 arasında olmalı (verilen: '5')",
  "details": { "fields": { "relevance_threshold": "relevance_threshold: 0 ile 1 arasında olmalı (verilen: '5')" } } } }
```

- Birbirine bağlı eşikler (`confidence_medium < confidence_high`, `dedupe_review_threshold < dedupe_auto_threshold`) hepsi yazıldıktan sonra denetlenir; ihlal ilgili alana bağlanır, bağlanamıyorsa `message`'a düşer.
- UI (`saveSettings`) yalnız **değişen** alanları gönderir (dokunulmayan eşik başka bir ayarın kısıtına takılmasın); `details.fields`'ı ilgili giriş kutusunun altına yazar.

### 8.2 Taramalar ve işler — `GET /admin/scans`

Yönetimin canlı panosu: etkin taramalar, crawler kuyruğu, geçmiş ve arka plan işleri. Çağrı başında takılı kalmış işleri
`interrupted` işaretler (hayalet satır kalmasın).

| Sorgu | Değer |
|---|---|
| `source` | kaynak kodu (geçmişi süzer) |
| `status` | `success` · `partial` · `failed` (geçmişi süzer) |
| `limit` | 1–100 (varsayılan 30) |

```json
{
  "generatedAt": "2026-10-20T10:36:53.021878+00:00",
  "crawlMode": "local",
  "aiQueue": { "waiting": 0, "stages": [
    { "status": "NEW", "stage": "classify", "label": "Sınıflandırma bekliyor", "count": 0, "oldestAt": null },
    { "status": "RELEVANT", "stage": "summarize", "label": "Özet bekliyor", "count": 0, "oldestAt": null },
    { "status": "SUMMARIZED", "stage": "match", "label": "Birim eşleme bekliyor", "count": 0, "oldestAt": null },
    { "status": "AI_FAILED", "stage": "failed", "label": "YZ hatası", "count": 0, "oldestAt": null } ] },
  "crawler": null,
  "active": [], "queued": [], "recentRequests": [],
  "recent": [ { "id": 41, "source": "BDDK", "sourceName": "Bankacılık Düzenleme ve Denetleme Kurumu",
                "status": "success", "startedAt": "…", "finishedAt": "…", "durationSec": 12.4, "elapsedSec": null,
                "backfillDay": null, "requests": 9, "itemsListed": 30, "itemsNew": 2, "itemsChanged": 0,
                "structureAlert": false, "errorCount": 0, "errors": [], "pendingIngest": false,
                "channels": [ { "name": "duyurular", "items": 30, "new": 2, "changed": 0, "error": null,
                                "warnings": 0, "stopped": false, "incomplete": false } ] } ],
  "jobs": { "active": [], "recent": [
    { "id": 7, "kind": "reprocess", "status": "success", "via": "inline", "source": null,
      "params": { "stage": "classify", "from": "2026-09-01", "to": "2026-09-30", "source": null },
      "requestedBy": "Ayşe Kaya", "createdAt": "…", "startedAt": "…", "finishedAt": "…", "heartbeatAt": "…",
      "elapsedSec": null, "durationSec": 31.2, "result": { "…": "…" }, "error": null } ] }
}
```

| Bölüm | Anlam |
|---|---|
| `crawlMode` | `local` (API'nin kendi taraması) veya `remote` (DMZ'deki ayrı crawler). `crawler` ve `queued`/`recentRequests` **yalnız `remote`** modda dolar |
| `crawler` | `{heartbeatAt, stale, hosts[], running[], error}` — `stale`: son sinyal `CRAWLER_STALE_MINUTES`'tan eski. Mongo okunamazsa yalnız `error` dolar, sayfa yine yüklenir |
| `active` | Çalışan taramalar. `pendingIngest: true` satır: crawler "çalışıyor" diyor ama kayıt henüz PostgreSQL'e aktarılmadı (≤ 1 dk) |
| `queued` | Crawler kuyruğundaki `pending`/`running` talepler (`kind`: `run`/`backfill`, `waitSec`) |
| `recent` | Biten taramalar (en yeni üstte). `backfillDay` doluysa geçmiş gün taramasıdır ve süresi gösterilmez |
| `jobs.*` | `job_run` kayıtları. `status`: `queued` · `running` · `success` · `skipped` · `failed` · `interrupted`; `via`: `inline` · `celery` · `cli` |
| `aiQueue` | YZ hattında bekleyen kayıtlar; `waiting` `AI_FAILED` hariç toplam |

**Yenileme (`scheduleScans`):** yalnız Yönetim açık ve sekme görünürken; bir şey sürerken (`active`, `queued`, `jobs.active`
veya `aiQueue.waiting`) **10 sn**, sakinken **60 sn**'de bir. Bir tarama/iş kaybolduysa "Kaynaklar" tablosu için
`/admin/metrics` de yeniden çekilir. Süzgeç değişirse eski yanıt atılır.

### 8.3 Kaynak metrikleri — `GET /admin/metrics`

Kaynak başına son tarama, bir sonraki planlı tarama ve LLM gecikmesi.

```json
{
  "generatedAt": "2026-10-20T10:36:52.911068+00:00", "crawlMode": "local", "pendingIngest": null,
  "sources": [
    { "code": "BDDK", "name": "…", "nextDue": "2026-10-20T14:00:00+03:00",
      "lastRun": { "status": "success", "startedAt": "…", "finishedAt": "…", "durationSec": 12.4,
                   "requests": 9, "itemsNew": 2, "itemsListed": 30 } } ],
  "llm": { "windowHours": 24, "calls": 118, "failed": 2, "p50Ms": 2100, "p95Ms": 6400 }
}
```

`lastRun` hiç taranmamışsa `null`; `pendingIngest` yalnız `remote` modda sayıdır (aktarılmayı bekleyen belge). Mongo okunamazsa
ilgili alanlar `null` kalır, kalan metrikler döner.

### 8.4 Uyarılar — `GET /admin/alerts?status=open|closed|all&limit=100`

İzleme servisinin (`monitor` görevi) açtığı uyarılar. UI sekmelerle `status` değiştirir (varsayılan `open`).

```json
[ { "id": 3, "key": "fetch_error:BDDK", "type": "fetch_error", "severity": "down", "source": "BDDK",
    "message": "Henüz tarama yapılmadı", "details": {},
    "openedAt": "2026-10-20T10:36:52+00:00", "lastSeenAt": "2026-10-20T10:36:52+00:00", "resolvedAt": null } ]
```

`severity`: `down` · `delayed` · `warning`. `type`: `fetch_error`, `structure_change`, `volume_low`, `volume_high`,
`crawler_down`, `ingest_lag`, `crawl_request`, `ai_failure` … Koşul ortadan kalkınca uyarı otomatik kapanır (`resolvedAt` dolar).
Elle denetim için `POST /admin/monitor/run` (aşağıda).

### 8.5 Şimdi tara — `POST /admin/sources/{code}/run` *(admin)*

Gövde yoktur. `code` büyük/küçük harfe duyarsız aranır, kanonik koda çevrilir. Bilinmeyen kaynak → `404 not_found`.
Yanıt `202`; **biçim kuruluma göre değişir** (`via` alanı UI'ya hangi iletiyi göstereceğini söyler):

```json
{ "status": "queued", "via": "crawler", "requestId": "66f1c0…" }     // CRAWL_MODE=remote: DMZ crawler'a talep (Mongo)
{ "status": "queued", "via": "celery",  "jobId": 12 }                // REDIS_URL var: Celery görevi
{ "status": "queued", "via": "inline",  "jobId": 12 }                // yerel: FastAPI arka plan görevi
```

`remote` modda talep ≤ `CRAWLER_POLL_S` (varsayılan 15 sn) içinde alınır; kaynak o sırada taranıyorsa bekler.
UI (`runSource`): düğmeyi hemen kilitler, başarıda toast (*"…tarama talebi crawler'a iletildi; kaynak meşgul değilse kısa süre içinde başlar."*)
ve Yönetim açıksa `loadScans()`; düğme `RUN_COOLDOWN_MS` = **60 sn** kapalı kalır.

### 8.6 Geçmişi tara — `POST /admin/sources/{code}/backfill` *(admin)*

```http
POST /api/v1/admin/sources/RESMI_GAZETE/backfill
{ "from": "2026-09-01", "to": "2026-09-30" }
```
```json
{ "status": "queued", "days": 30, "jobId": 15 }
```

`remote` modda `{"status":"queued","via":"crawler","requestId":"…","days":30}`. Kural: `from ≤ to` ve **en fazla 62 gün**,
aksi halde `400 invalid_range`. Yerel çalışma, zamanlanmış taramayla çakışmamak için kaynak kilidi alır (kilitliyse iş `skipped`).

### 8.7 Yeniden işleme ve başarısızları yeniden deneme *(admin, 202)*

```http
POST /api/v1/admin/reprocess
{ "stage": "classify", "from": "2026-09-01", "to": "2026-09-30", "source": null }
```
```json
{ "status": "queued", "stage": "classify", "jobId": 21 }
```

| `stage` | Anlam | LLM gerekli mi |
|---|---|---|
| `extract` | Belgeden metin çıkarma | hayır |
| `classify` | İlgililik + önem | evet |
| `summarize` | Özet | evet |
| `match` | Birim eşleme | evet |

`to < from` → `400 invalid_range`; LLM kapalıyken `extract` dışı aşama → `400 llm_required`.

```http
POST /api/v1/admin/retry-failed
{}                                  // ya da { "stage": "summarize", "source": "BDDK" }
```
```json
{ "status": "queued", "count": 4, "jobId": 22 }
```

Hiç `AI_FAILED` kayıt yoksa `{"status":"nothing_to_do","count":0}` (UI: *"Yeniden denenecek başarısız kayıt yok."*). Gövde isteğe bağlıdır.

### 8.8 Baseline sınırı — `GET` / `POST /admin/rebaseline` *(admin)*

**BASELINE** = bir kanalın ilk taramasında sitede zaten duran, YZ'ye girmemiş kayıtlar. Sınır tarihi ve sonrası YZ hattına alınır.

1. `GET /admin/rebaseline` → `{"current": null}` ya da `{"current": "2026-09-01"}` (kayıtlı sınır).
2. `GET /admin/rebaseline?cutoff=2026-09-01` → **önizleme** (hiçbir şey değişmez):

```json
{
  "cutoff": "2026-09-01", "current": null, "converted": 0,
  "sources": [
    { "source": "RESMI_GAZETE", "name": "Resmî Gazete", "action": "crawl", "converted": 0,
      "earliest": "2026-09-19", "range": { "from": "2026-09-01", "to": "2026-09-18" } },
    { "source": "BDDK", "name": "…", "action": "shallow", "converted": 0, "earliest": "2026-09-18", "range": null } ],
  "crawl": ["RESMI_GAZETE", "SPK", "…"]
}
```

| `action` | Anlamı |
|---|---|
| `covered` | Arşiv sınıra kadar uzanıyor; yalnız mevcut BASELINE kayıtlar dönüştürülür |
| `crawl` | Sınıra kadar geriye taranabilir (Resmî Gazete gün gün, sayfalı kaynaklar) |
| `crawl_shallow` | Hiç taranmamış; sitenin gösterdiği kadarı alınır |
| `shallow` | Taranmış ama liste daha geriye gitmiyor; yalnız mevcut kayıtlar dönüştürülür |

3. `POST /admin/rebaseline` `{ "cutoff": "2026-09-01" }` → `202 { "status":"queued", "count": 0, "crawl": [...], "jobId": 30 }`.
   Hatalar: `400 llm_required`, `400 invalid_cutoff` (gelecek tarih), `409 rebaseline_running` (zaten sürüyor).

UI, **önizlenmemiş tarihin uygulanmasına izin vermez**: "Uygula" önce önizleme ister (`rebaseline.cutoff === form.cutoff`),
sonra `confirm("… N kayıt YZ hattına alınacak. M kaynak taranacak. Devam edilsin mi?")` gösterir.

### 8.9 Denetim izi bütünlüğü — `GET /admin/audit/verify[?regulation_id=]`

```json
{ "ok": true, "problems": [] }
```
Sorun varsa `problems[] = {id, regulation_id, problem}`; `problem`: `zincir kopuk` · `içerik değiştirilmiş` · `hash yok (zincir öncesi)`.
Son tür eski (hash zincirinden önceki) kayıtlar içindir ve `ok`'u bozmaz.

### 8.10 UI'sı olmayan yönetim uçları

| Uç | Gövde | Yanıt | Not |
|---|---|---|---|
| `POST /admin/monitor/run` | — | `200 {"statuses":{"BDDK":"down",…},"opened":14,"resolved":0}` | İzleme kontrolünü hemen çalıştırır (normalde Beat tetikler) |
| `POST /admin/regulations/{id}/regenerate` | — | `202 {"status":"queued","jobId":9}` | Özet + birim önerisini yeniden üretir; LLM yoksa iş `skipped` |
| `POST /admin/regulations/{id}/split` | `{"raw_document_id": 123}` | `200 {"new_regulation_id": 77}` | Yanlış birleşmiş ham belgeyi yeni kayda ayırır; belge bu düzenlemeye bağlı değilse `404` |

---

## 9. Arşiv (`/arsiv`)

Portalda **görünmeyen** iki tür kaydı sorgular: `irrelevant` (YZ'nin ilgisiz bulduğu — yanlış elemeyi yakalamak için) ve
`baseline` (ilk taramada sitede duran). Binlerce kayıt olabileceğinden her şey sunucuda sayfalanır.

**Liste — `GET /archive/regulations`**

| Parametre | Değer |
|---|---|
| `kind` | `irrelevant` (varsayılan) · `baseline` · `all` |
| `source`, `q` | kaynak kodu; başlıkta/kurum adında arama (Türkçe harf katlamalı) |
| `from`, `to` | yayım tarihi aralığı (`YYYY-MM-DD`) |
| `page`, `page_size` | 1…`2000`, 1–100 (varsayılan 25). `page > 2000` → `400 page_too_deep` (`details.max_page`): süzgeçle daraltın |
| `sort` | `publish_date` · `detected_at` · `title` · `issuer` · `score`; `direction` |

```http
GET /api/v1/archive/regulations?kind=all&q=sermaye&page=1&page_size=25
```
```json
{
  "items": [
    { "id": 812, "title": "…", "issuer": "Rekabet Kurumu", "issuerCode": "REKABET",
      "kind": "irrelevant", "sources": ["REKABET"], "sourceUrl": "https://…",
      "publishDate": "2026-09-12", "detectedAt": "2026-09-13T06:02:11+00:00",
      "classifiedAt": "2026-09-13T06:30:40+00:00",
      "relevanceScore": 0.12, "rationale": "Bankacılıkla doğrudan ilişkisi yok… (320 karakterde kesilir)", "uncertain": false } ],
  "total": 1840, "counts": { "irrelevant": 1500, "baseline": 340, "all": 1840 },
  "page": 1, "pageSize": 25, "pages": 74
}
```

`counts`, **tür dışındaki** süzgeçlere (kaynak, arama, tarih) göre üç sekmenin sayısını verir; UI sekme rozetlerini bundan çizer.
`baseline` kayıtlarının skoru yoktur (`relevanceScore: null`), UI skor sütununu gizler.

**Ayrıntı — `GET /archive/regulations/{id}`:** listedeki alanlara ek olarak tam `rationale` (kesilmemiş), `criteria[]`
(değerlendirilen ölçütler), `documents[]` (`source`, `url`, `title`, `role`, `publishedAt`, `fetchedAt`, `isBaseline`) ve baseline için açıklayıcı `note`.
Arşivde olmayan kimlik (ör. portalda görünen kayıt) → `404 not_found`.

**UI (`arsiv.html`):** durum **adres çubuğundadır** (`?tur=ilgisiz|ilk-tarama|tumu&q=&kaynak=&bas=&bit=&sirala=publish_date:desc&boyut=25&sayfa=3`);
Türkçe adres parametreleri API parametrelerine çevrilir (`tur`→`kind`, `kaynak`→`source`, `bas`/`bit`→`from`/`to`, `boyut`→`page_size`).
Satıra tıklanınca ayrıntı satır içinde açılır. Süzgeç sonrası sayfa aralık dışında kalırsa (`page > pages`) son sayfaya gidip yeniden yükler.
Yarış koşulunu önlemek için `reqSeq` kullanılır.

---

## 10. Süreç haritası (`/surec`)

Belgenin taramadan portala kadar geçtiği durumları ve her durumdaki canlı sayıyı gösterir.

**`GET /pipeline?days=7`** (`days`: 1–365, boşsa tümü; `format=json|mermaid`)

```json
{
  "generatedAt": "2026-10-20T10:36:52.757836+00:00", "days": 7,
  "layers": [ { "id": "toplama", "label": "Toplama (İK-1)" }, { "id": "belge", "label": "Ham belge (İK-2)" },
              { "id": "duzenleme", "label": "Düzenleme kaydı (İK-2)" }, { "id": "yz", "label": "YZ analizi (İK-3..5)" },
              { "id": "portal", "label": "Portal incelemesi (İK-6)" } ],
  "nodes": [ { "id": "reg:READY", "label": "Hazır", "layer": "yz", "kind": "ok",
               "description": "Özet ve birim önerileri tamam; uzman incelemesine hazır.",
               "module": "app/ai/unit_matching.py", "count": 1 }, … ],
  "edges": [ { "source": "reg:NEW", "target": "reg:RELEVANT", "label": "ilgililik + önem", "queue": "ai" }, … ],
  "fetchRuns": { "success": 12, "failed": 1 },
  "openAlerts": { "down": 2, "warning": 1 },
  "unknownStatuses": [],
  "visibleStatuses": ["RELEVANT", "SUMMARIZED", "READY", "AI_FAILED"]
}
```

- `nodes[].id` kuralı: `sources`, `fetch`, **`raw:<DURUM>`** (ham belge), **`reg:<DURUM>`** (düzenleme), **`review:<DURUM>`** (portal incelemesi).
  `kind`: `flow` · `ok` · `wait` · `fail` · `terminal` — UI renk/ikon seçer. `queue`: edge'i işleyen Celery kuyruğu.
- `unknownStatuses`: grafikte karşılığı olmayan bir durum çıktıysa (yeni durum eklendi, haritaya eklenmedi) burada görünür.
- Yerleşim (sütun/satır) **UI'dadır** (`POS`); yeni düğüm eklenirse hem `services/pipeline.py: NODES` hem `portal/assets/views.js: HAT_COLS` (işleme hattı sütunları) güncellenmelidir.

**Düğüm paneli — `GET /pipeline/nodes/{node_id}?days=&limit=20`** (`limit` 1–100)

```http
GET /api/v1/pipeline/nodes/review:Onaylandı
```
```json
{ "node": "review:Onaylandı",
  "items": [ { "id": 1, "title": "Yeni başlık", "source": "BDDK", "at": "2026-10-20T10:36:52.007563+00:00",
               "severity": "Orta", "regulationId": 1, "visible": true, "error": null } ] }
```

Öğe biçimi düğüm türüne göre değişir: `raw:*` → `{id,title,source,url,at,regulationId,error}`; `reg:*`/`review:*` →
yukarıdaki örnek; `fetch` → `{id,title,source,at,detail,error}`; `sources` → boş liste (UI bunun yerine `/sources/health` çeker).
Bilinmeyen düğüm → `404 not_found`. Binlerce kaydı olan `reg:IRRELEVANT` ve `reg:BASELINE` panelleri yalnız en yeni 20'yi
gösterir ve **Arşiv**'e bağlantı verir. Portalda görünen bir kayda `#kayit=<regulationId>` bağlantısı verilir.

**`GET /pipeline?format=mermaid`** düz metin `flowchart LR` döner (belgeye yapıştırmak için); UI'da "Mermaid kopyala" düğmesi panoya kopyalar.

---

## 11. Karşılaştırma (`/karsilastirma`) ve paralel çalışma

Paralel çalışma dönemi: Başkanlığın elle tuttuğu liste ile sistemin bulduklarını karşılaştırıp kaçırılanları ve gereksiz uyarıları ölçer.

### 11.1 Excel karşılaştırma — `POST /evaluation/excel-compare` *(viewer)*

**Salt okunurdur** (veritabanına hiçbir şey yazmaz, sunucu durum tutmaz). `multipart/form-data`, alan adı **`file`**;
`.csv`, `.txt`, `.xlsx`, `.xlsm`, en fazla **5 MB**.

| Sorgu | Değer |
|---|---|
| `format` | `json` (varsayılan) · `xlsx` (aynı sonucu çalışma kitabı olarak indirir) |
| `title_threshold` | 50–100, varsayılan **85** — başlık benzerliği eşiği |
| `text_threshold` | 50–100, varsayılan **65** — metin benzerliği eşiği |
| `from`, `to` | "Fazla uyarılar" için dönem; verilmezse listedeki tarihlerden türetilir |

Sütun başlıkları esnektir (büyük/küçük harf ve Türkçe karakter fark etmez): `Referans No`, `Konu`/`Başlık`, `Talep Tipi`,
`Kurum`, `Risk Seviyesi`/`Önem`, `Alıcılar`, `Oluşturulma Tarihi`/`Tarih`, `Metin`, `URL`/`Bağlantı`. `Konu` ya da `Metin`
dolu satır yoksa `400 empty_file`.

Metin eşiğinin 65 olması, farklı biçimde özetlenmiş açıklamaların da eşleşmesini sağlar; benzerlik skoru
doğruluk olasılığı değildir. Düşük skorlu eşleşmeler belgeyle doğrulanmalı; daha sıkı karşılaştırma için
`text_threshold=80` gibi bir değer gönderilebilir. Başlık eşiğinin varsayılanı 85 olarak korunur.

Numaralı yayınlar önce kurum, belge türü ve tarih bağlamıyla doğrulanır (`exact_reference`).
Satırın `reference` alanı `{kind, number, date, status, candidateIds}` biçimindedir; numara okunmadıysa `null` olur.
`status`: `matched`, `ambiguous`, `citation`, `conflict` veya `unverified`. İlkinde kimlik doğrulanmıştır; diğerlerinde
`category=reference_review`, `group=missing` döner ve otomatik bulanık eşleşme yapılmaz. Skor bu yöntemde `null`'dır,
doğruluk olasılığı değildir. Portal ve Excel çıktısı kimliği, durumu ve aday kayıt numaralarını gösterir.
Kimlikler ham belgenin başlığında/ilk 1600 karakterinde aranır; eski sürümler ve liste satırları dışarıda kalır.
Bülten numarası eşleşmesi yalnızca bültenin bulunduğunu doğrular; her kararın ayrı analizi tamamlandı demek değildir.
Kimliği doğrulanan ham belge henüz düzenleme kaydına bağlanmamışsa `category=reference_raw_only` döner;
bu durum crawl yokluğu değildir. Başlıkla yapılandırılmış karar başlığı çelişirse otomatik bağlanmaz.

`summary.notFound`, `summary.review`, `summary.unprocessed` ayrı sayaçlardır; `missing` eski birleşik
sayaçtır. `matched` düzenleme/ham belgeyle doğrulanan veya kullanıcı onaylı satır sayısı,
`matchRate=matched/total` oranıdır (ilk tarama dahil). Eski `foundRate` YZ ilgili+ilgisiz oranını korur.
Satırdaki `matchState`: `matched`, `review`, `unmatched`, `unprocessed`. `candidates` her adayın
`regulation`, `rawDocumentId`, `textExcerpt`, `evidence` alanlarını içerir. `evidence=similarity` yalnız
öneridir; belge kimliğinin doğrulandığı anlamına gelmez.

Multipart formda isteğe bağlı `selections` JSON metni gönderilebilir:
`[{"row":6,"regulation_id":2675}]`. Satır numarası 1 tabanlıdır. Yalnız güncel aday kabul edilir;
yinelenen/geçersiz satır veya aday `400 invalid_selection`, bozuk JSON/alan türleri `422` döndürür.
Seçilen satır `method=manual_selection`, `selectedRegulationId=2675` ile döner; benzerlik skoru verilmez.
`format=xlsx` için aynı seçimler yeniden gönderilmelidir. Onay veritabanına yazılmaz, YZ etiketini
değiştirmez ve gönderilmediği sonraki isteğe taşınmaz. Portal bunu mevcut sayfa oturumunda saklar;
farklı dosya yüklenince temizler.

```bash
curl -X POST 'http://localhost:8080/api/v1/evaluation/excel-compare?title_threshold=85' \
     -F 'file=@liste.xlsx'
```
```json
{
  "summary": { "total": 1, "relevant": 0, "irrelevant": 0, "missing": 1,
               "missingByReason": { "not_collected": 1 }, "recall": 0.0, "foundRate": 0.0 },
  "precision": { "period": { "from": null, "to": null }, "shown": 0, "inList": 0, "extra": 0, "precision": null },
  "severityAgreement": { "n": 0, "exact": null },
  "byIssuer": [ { "issuer": "BDDK", "total": 1, "relevant": 0, "irrelevant": 0, "missing": 1 } ],
  "thresholds": { "title": 85.0, "text": 65.0 },
  "items": [
    { "no": 1, "ref": "R1", "title": "Bank Mellat sermaye yeterliliği", "issuer": "BDDK", "requestType": null,
      "severity": "Orta", "recipients": null, "createdAt": null,
      "category": "not_collected", "categoryLabel": "Toplanmadı", "group": "missing",
      "method": null, "methodLabel": null, "score": null, "rawDocumentId": null, "regulation": null } ],
  "extra": []
}
```

| `group` | `category` | Anlam |
|---|---|---|
| `relevant` | `detected` | Sistem yakaladı ve portalda gösteriyor |
| `irrelevant` | `below_threshold` | Bulundu ama YZ eledi (eşik altı) |
| `missing` | `not_collected` · `not_processed` · `baseline` · `dedupe_lost` | Portalda yok (toplanmadı / işlenmedi / ilk tarama / birleştirmede kayboldu) |

`method`: `url` · `exact_key` (karar/sıra no) · `fuzzy_title` · `fuzzy_title_raw` · `fuzzy_text`. `recall = relevant/total`,
`foundRate = (relevant+irrelevant)/total`; `precision.precision` = dönemde portalda görünen kayıtların kaçı listede var.
`extra[]` = portalda görünen ama listede karşılığı olmayan kayıtlar.

**UI (`karsilastirma.html`):** dosya seçilince "Karşılaştır" → `post()` JSON döner ve sekmeler (*Tüm satırlar / Relevant /
Irrelevant / Bulunamadı / Fazla uyarılar*) istemcide süzülür. **"Excel indir"**, sunucu durum tutmadığı için **aynı dosyayı yeniden
yükleyerek** `?format=xlsx` ile çağırır, `Content-Disposition`'dan `karsilastirma-<tarih>.xlsx` adını alır.
Eşik kutuları sorgu parametresi olur.

### 11.2 Elle tespit kayıtları ve rapor *(UI yok — API/CLI)*

| Uç | Rol | Açıklama |
|---|---|---|
| `POST /evaluation/manual-detections` | expert | Başkanlığın elle bulduğu tek kaydı ekler → `201` |
| `POST /evaluation/manual-detections/upload` | expert | CSV/Excel'den toplu ekler → `201 {added, matched, items[]}` |
| `GET /evaluation/manual-detections?from=&to=` | viewer | Kayıtlar (en çok 1000, yayım tarihine göre yeniden eskiye) |
| `GET /evaluation/report?from=&to=` | viewer | Dönem raporu (**`from`,`to` zorunlu**) |
| `GET /evaluation/filtered-out?from=&to=` | viewer | YZ'nin elediği kayıtlar (en çok 500) |

```http
POST /api/v1/evaluation/manual-detections
{ "title": "Bank Mellat sermaye yeterliliği", "issuer": "BDDK", "publish_date": "2026-09-19", "severity": "Orta",
  "url": null, "unit_codes": [], "note": null }
```
```json
{ "id": 1, "title": "Bank Mellat sermaye yeterliliği", "issuer": "BDDK", "publishDate": "2026-09-19",
  "url": null, "matchedRegulationId": null, "matchMethod": null, "matchScore": null }
```

`title` 3–1000 karakter; `severity` `Kritik|Yüksek|Orta|Düşük`. Her eklenen kayıt sistem kayıtlarıyla otomatik eşleştirilir
(`matchedRegulationId` doluysa bulundu). Yükleme dosyası sütunları: `Başlık`, `Kurum`, `Yayım Tarihi`/`Tarih`, `Bağlantı`, `Önem`,
`Birimler`, `Not`. Hatalar: `400 unsupported_file|invalid_file`, `413 too_large`.

`GET /evaluation/report` yanıtının ana bölümleri: `manual` (toplam, yakalanan, kaçırılan, `miss_rate`, nedene göre dağılım),
`system` (gösterilen, karara bağlanan, reddedilen, `unnecessary_rate`, bekleyen), `severity_agreement`, `unit_accuracy`
(`top1`, `any`), `latency_hours` (tespit gecikmesi, `median`, `p90`) ve `threshold_sweep[]` (ilgililik eşiğini 0.2–0.6 oynatırsak kaçırma/gereksiz oranı nasıl değişir — eşik ayarı için).
`snake_case` kullanır (bu grup portal sözleşmesinden önce yazıldığı için).

---

## 12. Sistem uçları

| Uç | Yanıt | Kullanım |
|---|---|---|
| `GET /healthz` | `200 {"status":"ok"}` | Süreç ayakta mı (liveness) |
| `GET /readyz` | `200 {"status":"ok","checks":{"db":"ok","llm":"kapalı","ocr":"kapalı"}}`; veritabanı yoksa `503 {"status":"hata", …}` | Hazır mı (readiness). `llm`/`ocr`: `yapılandırılmış` \| `kapalı`. Kimlik doğrulamasız olduğundan istisna metni yanıta konmaz, yalnız loga yazılır |
| `GET /docs`, `/openapi.json` | Swagger UI / şema | Geliştirici keşfi; `API_REFERENCE.md` bundan üretilir |

---

## 13. Yeni ekran eklerken kontrol listesi

1. **Sözleşme:** yanıt alanları camelCase, sorgu/gövde snake_case; sayfalıysa `items/total/page/pageSize` (arşivde ek `counts/pages`).
2. **Rol:** `require(actor, ROLE_…)` ekleyin (okuma `viewer`, karar/düzeltme `expert`, yönetim `admin`).
3. **Hata:** `ApiError(status, code, message, details)` fırlatın; UI `message`'ı gösterir, `code`'a göre dallanır. Alan bazlı hata için `details.fields`.
4. **Yazma:** `get_visible(..., for_update=True)` → `_check_version(If-Match)` → değişiklik → `add_event` (denetim) → `commit` → yeni `ETag`. Bayat veri riski varsa `row_version += 1`.
5. **Uzun iş:** `create_job` + `BackgroundTasks`/Celery → `202 {status, jobId}`; ilerleme `GET /admin/scans` içindeki `jobs`'ta görünür.
6. **UI:** `api()` sarmalayıcısını kullanın (kullanıcı başlığı + hata zarfı); sırasız yanıtlar için istek sıra numarası; arama için 300 ms debounce;
   durumu adres çubuğuna yazın (paylaşılabilir bağlantı); yazma sonrası `stats` ve listeyi tazeleyin.
7. **Belgeler:** `python scripts/gen_api_reference.py` (`mevzuat-core/app` içinde) ile `API_REFERENCE.md`'yi yeniden üretin (`--check` CI'da fark yakalar) ve bu kılavuzu güncelleyin.
8. **Test:** `mevzuat-core/app/tests/test_api.py` örnekleri (`TestClient`) şablon olarak kullanılır.

### Hızlı deneme (komut satırı)

```bash
# Liste (kullanıcı adı Türkçe karakter içeriyorsa URL kodlayın)
curl -s 'http://localhost:8080/api/v1/regulations?status=Bekliyor&page_size=5' -H 'X-Demo-User: Ay%C5%9Fe%20Kaya'

# Ayrıntı + ETag
curl -si 'http://localhost:8080/api/v1/regulations/1' | grep -i -E '^(HTTP|etag)'

# Onay (ETag'i If-Match olarak geri gönderin)
curl -s -X POST 'http://localhost:8080/api/v1/regulations/1/decision' \
     -H 'Content-Type: application/json' -H 'If-Match: "1"' \
     -d '{"decision":"approve","note":"Takip edilecek."}'

# Şimdi tara (admin)
curl -s -X POST 'http://localhost:8080/api/v1/admin/sources/BDDK/run'
```
