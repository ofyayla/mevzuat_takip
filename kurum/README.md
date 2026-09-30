# Kurum repoları ve chart'lar

Kurumda iki uygulama reposu ve kurum chart reposunda iki chart bulunur (kurum örneği: `repo-docs` +
`ai-uat-charts/<servis>`). Hepsi bu klasördeki iskeletler ve geliştirme deposundaki kod birleştirilerek üretilir:

```bash
backend/.venv/bin/python kurum/export.py [ÇIKIŞ] [--force]     # varsayılan: ~/Downloads/mevzuat-kurum
```

| Klasör (iskelet) | Üretilen | İçerik |
|---|---|---|
| `mevzuat-core/` | `mevzuat-core/` reposu | Dockerfile, pip.conf, gunicorn_config.py, Jenkinsfile, README, `deploy/` (Secret, ArgoCD, Mongo rolleri) + `app/` (backend), `portal/`, `docs/`, `API_REFERENCE.md` (OpenAPI'den) |
| `mevzuat-crawler/` | `mevzuat-crawler/` reposu | Dockerfile, pip.conf, Jenkinsfile, README, API_REFERENCE.md, `deploy/dmz` (podman) + `app/` (yalnızca toplama + crawler modülleri ve testleri) |
| `ai-uat-charts/` | `ai-uat-charts/mevzuat-{core,crawler}` | Helm chart'ları (kurum `common` 1.0.4), `values-albaraka-{uat,dev}.yaml` |

**requirements.txt** (`mevzuat-*/requirements.txt` → üretilen repoda `app/requirements.txt`): Python 3.12 imajında
testleri geçen sabit sürümler. Bağımlılık değişince yeniden üretilir: geliştirme deposunda imajı derleyip
`docker run --rm <imaj> pip freeze` (proje satırı hariç).

**Doğrulama** (üretilen klasörde, kurum ağı dışında):

```bash
docker build --build-arg BASE_IMAGE=python:3.12-slim-bullseye --build-arg CA_CERT_URLS= \
  --build-arg PIP_INDEX_URL=https://pypi.org/simple -t mevzuat-core ./mevzuat-core
helm lint ai-uat-charts/mevzuat-core -f ai-uat-charts/mevzuat-core/values-albaraka-uat.yaml
```

Kurulum sırası: `docs/kurum-devreye-alma.md`.
