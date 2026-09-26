# Mevzuat Takip backend — tek imaj; api / worker / beat / migrate komutla ayrılır (plan §11).
# Derleme bağlamı: depo kökü (portal dosyaları da imaja girer).
#   docker build -f backend/docker/Dockerfile -t mevzuat-backend .
# Air-gapped kurulum: imaj kurum registry'sine alınır; çalışma zamanında internetten paket indirilmez.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Istanbul \
    PORTAL_DIR=/app/portal RAW_STORAGE_DIR=/data/raw LOCK_DIR=/data/locks DB_AUTO_CREATE=false

RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates && \
    rm -rf /var/lib/apt/lists/* && useradd --create-home --uid 10001 mevzuat && \
    mkdir -p /data/raw /data/locks && chown -R mevzuat /data

WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install ".[postgres]"
COPY backend/config ./config
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
COPY ["Mevzuat Takip Portali.dc.html", "support.js", "/app/portal/"]

USER mevzuat
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
