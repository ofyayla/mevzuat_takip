# Mevzuat Takip — geliştirme deposu için iki hedefli Dockerfile (yerel compose, backend/docker). Kurumdaki repoların
# kendi Dockerfile'ları kurum/mevzuat-core ve kurum/mevzuat-crawler altındadır (kurum/export.py). Varsayılanlar kurum
# ayarlarıdır:
#   docker build .                          → mevzuat-core    (son aşama; LAN Kubernetes / ArgoCD)
#   docker build --target crawler .         → mevzuat-crawler (DMZ, RedHat/podman)
#
# core   : api / worker / beat / migrate komutla ayrılır (chart: kurum/ai-uat-charts/mevzuat-core). İşleme, OCR, YZ, portal.
# crawler: yalnızca toplama + MongoDB (LLM, OCR, FastAPI, Celery, PDF kütüphanesi yok → küçük saldırı yüzeyi).
#
# Kurum: taban imaj Nexus'tan, paketler pip.conf'taki kurum aynasından, kurum CA'ları Nexus raw deposundan.
# Kurum ağı dışında (yerel geliştirme) bu üçü ezilir — backend/docker/docker-compose.yml ve `make docker-build`:
#   --build-arg BASE_IMAGE=python:3.12-slim-bullseye --build-arg PIP_INDEX_URL=https://pypi.org/simple
#   --build-arg CA_CERT_URLS=
ARG BASE_IMAGE=nexus.alb.albarakatech.com:9099/com.albaraka.python:3.12-slim-bullseye
FROM ${BASE_IMAGE} AS base

ARG CA_CERT_URLS="https://nexus.alb.albarakatech.com/repository/atg-raw-file/alb_ca/albaraka-root-ca-2042.crt https://nexus.alb.albarakatech.com/repository/atg-raw-file/alb_ca/albaraka-sub-ca-2041.crt"
# PYTHONPATH: konsol komutları (celery, mevzuat-crawler …) da uygulamayı /app/backend'den yüklesin; yoksa
# site-packages'taki kopya yüklenir ve BACKEND_DIR/config (sources.yaml, taxonomy.yaml …) bulunamaz.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Istanbul PYTHONPATH=/app/backend \
    RAW_STORAGE_DIR=/data/raw LOCK_DIR=/data/locks CA_CACHE_DIR=/data/ca DB_AUTO_CREATE=false \
    PIP_CONFIG_FILE=/etc/pip.conf
# Kurum pip aynası (repo kökündeki pip.conf). PIP_INDEX_URL build-arg'ı verilirse ortam değişkeni olarak onu ezer.
COPY pip.conf /etc/pip.conf

# tzdata ve ca-certificates python slim imajlarında hazırdır; apt yalnızca eksikse çalışır (kurum ağında Debian
# deposuna erişim gerekmez; bullseye güvenlik deposu desteği bittiği için paketler zaten 404 dönebilir)
RUN (dpkg -s tzdata ca-certificates >/dev/null 2>&1 || \
      (apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates && \
       rm -rf /var/lib/apt/lists/*)) && \
    useradd --create-home --uid 10001 mevzuat && mkdir -p /data/raw /data/locks && chown -R mevzuat /data
# Kurum kök/ara CA'ları (kurum Dockerfile düzeni: Nexus raw deposundan). SSL denetimi yapan proxy ve iç HTTPS
# servisleri (Keycloak, OCR) için gerekir.
RUN if [ -n "$CA_CERT_URLS" ]; then \
      for u in $CA_CERT_URLS; do \
        python -c "import ssl,sys,urllib.request as r; n=sys.argv[1].rsplit('/',1)[-1].rsplit('.',1)[0]+'.crt'; open('/usr/local/share/ca-certificates/'+n,'wb').write(r.urlopen(sys.argv[1],context=ssl._create_unverified_context()).read())" "$u"; \
      done && update-ca-certificates; \
    fi

WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app

# ------------------------------------------------------------------------------------------------ DMZ crawler
FROM base AS crawler
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
RUN pip install .
# Güncel certifi kökleri + sistem deposu (kurum CA'ları dahil) tek pakette; httpx/curl_cffi bunu kullanır. Yalnızca
# sistem deposu yetmez: bullseye'ın ca-certificates paketi 2021 tarihli. (pip kurulumundan sonra tanımlanır.)
RUN cat "$(python -m certifi)" /etc/ssl/certs/ca-certificates.crt > /etc/ssl/certs/mevzuat-bundle.pem
ENV SSL_CERT_FILE=/etc/ssl/certs/mevzuat-bundle.pem REQUESTS_CA_BUNDLE=/etc/ssl/certs/mevzuat-bundle.pem
COPY backend/config ./config
ENV CRAWL_MODE=remote RAW_STORAGE=gridfs CRAWLER_HEARTBEAT_FILE=/data/crawler.alive
USER mevzuat
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s CMD mevzuat-crawler healthcheck || exit 1
CMD ["mevzuat-crawler", "serve"]

# ------------------------------------------------------------------------------------------------ LAN ana servis
FROM base AS core
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
RUN pip install ".[core,postgres]"
RUN cat "$(python -m certifi)" /etc/ssl/certs/ca-certificates.crt > /etc/ssl/certs/mevzuat-bundle.pem
ENV SSL_CERT_FILE=/etc/ssl/certs/mevzuat-bundle.pem REQUESTS_CA_BUNDLE=/etc/ssl/certs/mevzuat-bundle.pem
COPY backend/config ./config
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
COPY ["Mevzuat Takip Portali.dc.html", "support.js", "surec-haritasi.html", "/app/portal/"]
ENV PORTAL_DIR=/app/portal
USER mevzuat
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
