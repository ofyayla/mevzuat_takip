# mevzuat-core — Portal API Referansı

Portal (`/`) ve Albatros bu uç noktaları kullanır. Kimlik doğrulama: Keycloak (`narui` realm) Bearer JWT; `AUTH_MODE=disabled` yalnızca demo içindir. Tüm yanıtlar JSON. Etkileşimli şema: `/docs`, `/openapi.json`.

Bu dosya `app/scripts/gen_api_reference.py` ile OpenAPI şemasından üretilir; elle düzenlenmez.

## admin

| Yöntem | Yol | Parametreler | Gövde | Açıklama |
|---|---|---|---|---|
| `GET` | `/api/v1/admin/settings` |  |  | Admin Settings |
| `PUT` | `/api/v1/admin/settings` |  | evet | Admin Settings Put |
| `POST` | `/api/v1/admin/regulations/{reg_id}/split` | `reg_id` | evet | Admin Split |
| `POST` | `/api/v1/admin/regulations/{reg_id}/regenerate` | `reg_id` |  | Admin Regenerate |
| `GET` | `/api/v1/admin/alerts` | `status`, `limit` |  | Admin Alerts |
| `GET` | `/api/v1/admin/audit/verify` | `regulation_id` |  | Admin Audit Verify |
| `POST` | `/api/v1/admin/monitor/run` |  |  | Admin Monitor Run |
| `POST` | `/api/v1/admin/reprocess` |  | evet | Admin Reprocess |
| `POST` | `/api/v1/admin/sources/{code}/backfill` | `code` | evet | Admin Backfill |
| `POST` | `/api/v1/admin/sources/{code}/run` | `code` |  | Admin Run Source |

## evaluation

| Yöntem | Yol | Parametreler | Gövde | Açıklama |
|---|---|---|---|---|
| `POST` | `/api/v1/evaluation/manual-detections` |  | evet | Manual Detection |
| `GET` | `/api/v1/evaluation/manual-detections` | `from`, `to` |  | Manual Detections |
| `POST` | `/api/v1/evaluation/manual-detections/upload` |  | evet | Manual Detection Upload |
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

| Yöntem | Yol | Parametreler | Gövde | Açıklama |
|---|---|---|---|---|
| `GET` | `/api/v1/regulations` | `source`, `severity`, `status`, `unit`, `date_range`, `q`, `metric`, `page`, `page_size` |  | Regulations |
| `GET` | `/api/v1/regulations/stats` |  |  | Regulation Stats |
| `GET` | `/api/v1/regulations/{reg_id}` | `reg_id` |  | Regulation Detail |
| `POST` | `/api/v1/regulations/{reg_id}/views` | `reg_id` |  | Regulation Viewed |
| `POST` | `/api/v1/regulations/{reg_id}/decision` | `reg_id` | evet | Regulation Decision |
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
