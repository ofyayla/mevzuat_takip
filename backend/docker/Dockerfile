# Mevzuat Takip — iki imaj, tek Dockerfile (derleme bağlamı: depo kökü):
#   docker build -f backend/docker/Dockerfile --target core    -t mevzuat-core .      # LAN / Kubernetes
#   docker build -f backend/docker/Dockerfile --target crawler -t mevzuat-crawler .   # DMZ (RedHat, podman)
#
# core   : api / worker / beat / migrate komutla ayrılır (deploy/k8s). İşleme, OCR, YZ, portal.
# crawler: yalnızca toplama + MongoDB (LLM, OCR, FastAPI, Celery, PDF kütüphanesi yok → küçük saldırı yüzeyi).
# Air-gapped kurulum: imajlar kurum registry'sine alınır; çalışma zamanında internetten paket indirilmez.
FROM python:3.11-slim AS base

# PYTHONPATH: konsol komutları (celery, mevzuat-crawler …) da uygulamayı /app/backend'den yüklesin; yoksa
# site-packages'taki kopya yüklenir ve BACKEND_DIR/config (sources.yaml, taxonomy.yaml …) bulunamaz
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Istanbul PYTHONPATH=/app/backend \
    RAW_STORAGE_DIR=/data/raw LOCK_DIR=/data/locks CA_CACHE_DIR=/data/ca DB_AUTO_CREATE=false

RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates && \
    rm -rf /var/lib/apt/lists/* && useradd --create-home --uid 10001 mevzuat && \
    mkdir -p /data/raw /data/locks && chown -R mevzuat /data

WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app

# ------------------------------------------------------------------------------------------------ DMZ crawler
FROM base AS crawler
RUN pip install .
COPY backend/config ./config
ENV CRAWL_MODE=remote RAW_STORAGE=gridfs CRAWLER_HEARTBEAT_FILE=/data/crawler.alive
USER mevzuat
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s CMD mevzuat-crawler healthcheck || exit 1
CMD ["mevzuat-crawler", "serve"]

# ------------------------------------------------------------------------------------------------ LAN ana servis
FROM base AS core
RUN pip install ".[core,postgres]"
COPY backend/config ./config
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
COPY ["Mevzuat Takip Portali.dc.html", "support.js", "/app/portal/"]
ENV PORTAL_DIR=/app/portal
USER mevzuat
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
