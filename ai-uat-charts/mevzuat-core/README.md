# mevzuat-core

Mevzuat Takip ana servisi (LAN Kubernetes). ArgoCD bu klasörü ortam values dosyasıyla senkronlar.

| Kaynak | Ad (UAT) | Not |
|---|---|---|
| Deployment + Service | `mevzuat-core` | Portal + API (gunicorn, port 5000), kurum `common` şablonu |
| Deployment | `mevzuat-core-worker` | Celery: ingest, metin çıkarma/OCR, YZ, izleme; ek worker'lar `extraWorkers` |
| Deployment | `mevzuat-core-beat` | Celery zamanlayıcısı, **tek** örnek (Recreate) |
| Job (PreSync) | `mevzuat-core-migrate` | `alembic upgrade head`, pod'lardan önce |
| Ingress | `mevzuat-core` | `ingress.host` (UAT: `mevzuat-takip.artinttest.albarakaturk.local`) |

| Dosya | Ortam | Namespace | İmaj etiketi |
|---|---|---|---|
| `values-albaraka-uat.yaml` | UAT | `artint` | `latest` |
| `values-albaraka-dev.yaml` | DEV | `dev` | `dev` |

**Önkoşul:** `mevzuat-core-secrets` (dev: `mevzuat-core-dev-secrets`) Secret'ı namespace'te olmalı: `DATABASE_URL`,
`MONGO_URL` (`mevzuat_core` kullanıcısı), `LLM_API_KEY`, `OCR_API_KEY`. Örnek: mevzuat-core reposunda
`deploy/secret.example.yaml`. Values dosyalarına parola yazılmaz.

**Önkoşul (Keycloak):** `mevzuat-core-keycloak-keys` ConfigMap'i, realm'in RS256 açık anahtarını `<kid>.pem` adıyla
içermeli (`kubectl -n artint create configmap mevzuat-core-keycloak-keys --from-file=<kid>.pem=realm-public-key.pem`).
`/app/config/keycloak_keys` dizinine bağlanır; yoksa `AUTH_MODE=keycloak` iken her kimlikli istek 500
`auth_misconfigured` döner. Anahtar rotasyonunda yeni `.pem` eklenir.

**TEYİT bekleyen değerler:** Redis DB numarası (UAT 5, dev 6), `OCR_ENDPOINT`, `KEYCLOAK_CLIENT_ID`.

**Yeni imaj:** ArgoCD'de **Sync** yapılır (Deployment → Restart yetmez: Restart senkron değildir, PreSync migrasyon
Job'ı çalışmaz ve yeni imaj eski şemayla açılır). Pipeline her derlemede `latest` (veya dev dalında `dev`) etiketine
ek olarak commit etiketini de gönderir; `image.tag` o etikete güncellenirse ArgoCD senkronu kendiliğinden başlar,
imaj değişmez olur ve geri alma gerçekten önceki imaja döner. `latest` etiketiyle ArgoCD "History and Rollback"
yalnızca chart revizyonunu döndürür, imajı geri getirmez.

```bash
helm lint . -f values-albaraka-uat.yaml
helm template mevzuat-core . -n artint -f values-albaraka-uat.yaml
```
