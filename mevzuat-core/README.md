# mevzuat-core

Mevzuat Takip ana servisi (LAN Kubernetes). DMZ'deki **mevzuat-crawler**'ın MongoDB'ye yazdığı taramaları
PostgreSQL'e aktarır; metin çıkarma/OCR, tekilleştirme, YZ (ilgililik, önem, özet, birim önerisi), izleme ve
Mevzuat ve Uyum Başkanlığı portalını (portal + API) sağlar.

```
 mevzuat-crawler (DMZ) ──▶ MongoDB 10.155.7.195 ──▶ mevzuat-core (K8s): api · worker · beat ──▶ PostgreSQL
                                                          │ vLLM · Azure DI (OCR) · Keycloak
                                                          ▲ 443: kullanıcılar, Albatros
```

## Yapı

| Yol | İçerik |
|---|---|
| `app/` | Uygulama: `app/` paketi, `config/`, `migrations/` (Alembic), `tests/`, `requirements.txt`, `pyproject.toml` |
| `portal/` | Portal arayüzü (`Mevzuat Takip Portali.dc.html`, `support.js`, süreç haritası `surec-haritasi.html`); API ile aynı imajda sunulur |
| `Dockerfile`, `pip.conf`, `gunicorn_config.py` | İmaj (Nexus taban imajı, kurum pip aynası, kurum CA'ları); API gunicorn + UvicornWorker |
| `Jenkinsfile` | Nexus'a derle-gönder + SonarQube |
| `API_REFERENCE.md` | Portal API uç noktaları |
| `deploy/` | Secret örneği, ArgoCD Application örneği, MongoDB kullanıcı/rolleri (`mongo/init-users.js`) |
| `docs/` | Devreye alma, işletim (runbook), mimari plan, kaynak keşif raporu |

Chart: kurum chart reposunda `ai-uat-charts/mevzuat-core` (`values-albaraka-{uat,dev}.yaml`).

## Aynı imajdan çalışan süreçler

| Süreç | Komut | Chart |
|---|---|---|
| Portal + API | `gunicorn app.api.main:app -c gunicorn_config.py` (imaj varsayılanı) | `mevzuat-core` |
| Celery worker | `celery -A app.worker worker -Q ingest,process,ai,monitor,default` | `mevzuat-core-worker` |
| Celery beat | `celery -A app.worker beat` (tek örnek) | `mevzuat-core-beat` |
| Migrasyon | `alembic upgrade head` | `mevzuat-core-migrate` (PreSync) |

Yönetim komutları pod içinde: `mevzuat-ai units-load | llm-check`, `mevzuat-process ocr-check`,
`mevzuat-monitor alerts | backfill | reprocess | audit-verify` (bkz. `docs/runbook.md`).

## Yerel geliştirme

```bash
cd app
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest respx && pip install --no-deps -e .
cp .env.example .env
pytest -q          # Mongo testleri için: docker run -d -p 27018:27017 mongo:7
```

Kurum ağı dışında imaj: `docker build --build-arg BASE_IMAGE=python:3.12-slim-bullseye --build-arg CA_CERT_URLS=
--build-arg PIP_INDEX_URL=https://pypi.org/simple -t mevzuat-core .`

## Belgeler

- Kurumda devreye alma: `docs/kurum-devreye-alma.md`
- İşletim ve olay müdahalesi: `docs/runbook.md`
- Mimari: `docs/backend-gelistirme-plani.md` · modül ayrıntıları: `app/README.md`
