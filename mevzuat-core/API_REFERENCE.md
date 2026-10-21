# mevzuat-core — Portal API Referansı

Portal (`/`) ve Albatros bu uç noktaları kullanır. Kimlik doğrulama yoktur: herkes tüm rollere sahiptir, kullanıcı adı `X-Demo-User` başlığından (URL kodlamalı) gelir. Tüm yanıtlar JSON. Etkileşimli şema: `/docs`, `/openapi.json`.

Bu dosya `app/scripts/gen_api_reference.py` ile OpenAPI şemasından üretilir; elle düzenlenmez.

## admin

| Yöntem | Yol | Parametreler | Gövde | Açıklama |
|---|---|---|---|---|
| `GET` | `/api/v1/admin/settings` | `detail` |  | Admin Settings |
| `PUT` | `/api/v1/admin/settings` | `detail` | evet | Admin Settings Put |
| `GET` | `/api/v1/admin/settings/history` | `key`, `limit` |  | Admin Settings History |
| `POST` | `/api/v1/admin/settings/preview` |  | evet | Admin Settings Preview |
| `GET` | `/api/v1/admin/metrics` |  |  | Admin Metrics |
| `GET` | `/api/v1/admin/scans` | `source`, `status`, `limit` |  | Admin Scans |
| `POST` | `/api/v1/admin/regulations/{reg_id}/split` | `reg_id` | evet | Admin Split |
| `POST` | `/api/v1/admin/regulations/{reg_id}/regenerate` | `reg_id` |  | Admin Regenerate |
| `POST` | `/api/v1/admin/retry-failed` |  | evet | Admin Retry Failed |
| `GET` | `/api/v1/admin/rebaseline` | `cutoff` |  | Admin Rebaseline Preview |
| `POST` | `/api/v1/admin/rebaseline` |  | evet | Admin Rebaseline |
| `GET` | `/api/v1/admin/alerts` | `status`, `limit` |  | Admin Alerts |
| `POST` | `/api/v1/admin/alerts/{alert_id}/resolve` | `alert_id` | evet | Admin Alert Resolve |
| `GET` | `/api/v1/admin/audit/verify` | `regulation_id` |  | Admin Audit Verify |
| `POST` | `/api/v1/admin/monitor/run` |  |  | Admin Monitor Run |
| `POST` | `/api/v1/admin/reprocess` | `dry_run` | evet | Admin Reprocess |
| `POST` | `/api/v1/admin/sources/{code}/backfill` | `code` | evet | Admin Backfill |
| `POST` | `/api/v1/admin/sources/{code}/run` | `code` |  | Admin Run Source |

## archive

| Yöntem | Yol | Parametreler | Gövde | Açıklama |
|---|---|---|---|---|
| `GET` | `/api/v1/archive/regulations` | `kind`, `source`, `q`, `from`, `to`, `page`, `page_size`, `sort`, `direction` |  | Archive Regulations |
| `GET` | `/api/v1/archive/regulations/{reg_id}` | `reg_id` |  | Archive Regulation Detail |

## evaluation

| Yöntem | Yol | Parametreler | Gövde | Açıklama |
|---|---|---|---|---|
| `POST` | `/api/v1/evaluation/manual-detections` |  | evet | Manual Detection |
| `GET` | `/api/v1/evaluation/manual-detections` | `from`, `to` |  | Manual Detections |
| `POST` | `/api/v1/evaluation/manual-detections/upload` |  | evet | Manual Detection Upload |
| `POST` | `/api/v1/evaluation/excel-compare` | `format`, `title_threshold`, `text_threshold`, `from`, `to` | evet | Excel Compare |
| `GET` | `/api/v1/evaluation/report` | `from`, `to` |  | Evaluation Report |
| `GET` | `/api/v1/evaluation/filtered-out` | `from`, `to` |  | Evaluation Filtered Out |

## me

| Yöntem | Yol | Parametreler | Gövde | Açıklama |
|---|---|---|---|---|
| `GET` | `/api/v1/me` |  |  | Me |

## pipeline

| Yöntem | Yol | Parametreler | Gövde | Açıklama |
|---|---|---|---|---|
| `GET` | `/api/v1/pipeline` | `days`, `format` |  | Pipeline |
| `GET` | `/api/v1/pipeline/nodes/{node_id}` | `node_id`, `days`, `limit` |  | Pipeline Node |

## regulations

Excel dışa aktarmada isteğe bağlı `ids`, virgülle ayrılmış pozitif 64 bit kayıt numaralarıdır.
Verilmezse filtrelenmiş listenin tamamı, verilirse yalnızca bu numaralarla ve mevcut süzgeçlerle eşleşen
görünür kayıtlar aktarılır. Tekrarlanan numaralar tekilleştirilir; boş veya geçersiz değerler
`400 invalid_ids` döndürür. Sıralama ve 5.000 kayıt sınırı her iki kapsamda da korunur.

| Yöntem | Yol | Parametreler | Gövde | Açıklama |
|---|---|---|---|---|
| `GET` | `/api/v1/regulations` | `source`, `severity`, `status`, `unit`, `date_range`, `q`, `metric`, `page`, `page_size`, `sort`, `direction`, `queue`, `effective`, `needs_unit`, `unit_changed`, `edited` |  | Regulations |
| `GET` | `/api/v1/regulations/stats` |  |  | Regulation Stats |
| `GET` | `/api/v1/regulations/export` | `source`, `severity`, `status`, `unit`, `date_range`, `q`, `metric`, `sort`, `direction`, `queue`, `effective`, `needs_unit`, `ids` |  | Regulations Export |
| `GET` | `/api/v1/regulations/irrelevant-sample` | `page`, `page_size` |  | Irrelevant Sample List |
| `POST` | `/api/v1/regulations/{reg_id}/irrelevant-review` | `reg_id` | evet | Irrelevant Review |
| `POST` | `/api/v1/regulations/bulk-decision` |  | evet | Regulation Bulk Decision |
| `GET` | `/api/v1/regulations/decision-stats` |  |  | Regulation Decision Stats |
| `GET` | `/api/v1/regulations/{reg_id}` | `reg_id` |  | Regulation Detail |
| `PATCH` | `/api/v1/regulations/{reg_id}/content` | `reg_id` | evet | Regulation Content |
| `GET` | `/api/v1/regulations/{reg_id}/document` | `reg_id` |  | Regulation Document |
| `POST` | `/api/v1/regulations/{reg_id}/views` | `reg_id` |  | Regulation Viewed |
| `POST` | `/api/v1/regulations/{reg_id}/decision` | `reg_id` | evet | Regulation Decision |
| `POST` | `/api/v1/regulations/{reg_id}/decision/revise` | `reg_id` | evet | Regulation Decision Revise |
| `POST` | `/api/v1/regulations/{reg_id}/decision/reopen` | `reg_id` | evet | Regulation Decision Reopen |
| `PUT` | `/api/v1/regulations/{reg_id}/units` | `reg_id` | evet | Regulation Units |

## sistem

| Yöntem | Yol | Parametreler | Gövde | Açıklama |
|---|---|---|---|---|
| `GET` | `/healthz` |  |  | Healthz |
| `GET` | `/readyz` |  |  | Readyz |

## sources

| Yöntem | Yol | Parametreler | Gövde | Açıklama |
|---|---|---|---|---|
| `GET` | `/api/v1/sources` |  |  | Sources |
| `GET` | `/api/v1/sources/health` |  |  | Health Of Sources |

## units

| Yöntem | Yol | Parametreler | Gövde | Açıklama |
|---|---|---|---|---|
| `GET` | `/api/v1/units` |  |  | Units |
