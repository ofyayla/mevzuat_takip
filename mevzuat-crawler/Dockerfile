# mevzuat-crawler — kaynak siteleri tarar, sonucu MongoDB'ye yazar (DMZ; internete çıkan tek bileşen).
# Tek süreç: zamanlama (config/sources.yaml cron) + LAN tarama talepleri. Web sunucusu yoktur (gunicorn kullanılmaz:
# birden çok worker zamanlayıcıyı çoğaltırdı). LLM, OCR, API, Celery kütüphaneleri imajda yoktur.
# Kurum ağı dışında derleme: --build-arg BASE_IMAGE=python:3.12-slim-bullseye --build-arg CA_CERT_URLS= \
#   --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG BASE_IMAGE=nexus.alb.albarakatech.com:9099/com.albaraka.python:3.12-slim-bullseye
FROM ${BASE_IMAGE}

ARG CA_CERT_URLS="https://nexus.alb.albarakatech.com/repository/atg-raw-file/alb_ca/albaraka-root-ca-2042.crt https://nexus.alb.albarakatech.com/repository/atg-raw-file/alb_ca/albaraka-sub-ca-2041.crt"
ARG PIP_INDEX_URL

# Kurum kök/ara CA sertifikaları (SSL denetimi yapan çıkış proxy'si için). Python ile indirilir (taban imajda curl yok).
RUN if [ -n "$CA_CERT_URLS" ]; then \
      for u in $CA_CERT_URLS; do \
        python -c "import ssl,sys,urllib.request as r; n=sys.argv[1].rsplit('/',1)[-1].rsplit('.',1)[0]+'.crt'; open('/usr/local/share/ca-certificates/'+n,'wb').write(r.urlopen(sys.argv[1],context=ssl._create_unverified_context()).read())" "$u"; \
      done && update-ca-certificates; \
    fi

COPY pip.conf /etc/pip.conf
ENV PIP_CONFIG_FILE=/etc/pip.conf PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN pip install --upgrade pip

WORKDIR /app

COPY app/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY /app /app
# Komut satırı araçları (mevzuat-crawler, mevzuat-collect); bağımlılıklar yukarıda kuruldu
RUN pip install --no-cache-dir --no-deps .

# Güncel certifi kökleri + sistem deposu (kurum CA'ları dahil) tek pakette. Bullseye'ın sistem deposu 2021 tarihli.
RUN cat "$(python -m certifi)" /etc/ssl/certs/ca-certificates.crt > /etc/ssl/certs/mevzuat-bundle.pem && \
    useradd --create-home --uid 10001 mevzuat && mkdir -p /data && chown -R mevzuat /data

ENV PYTHONPATH=/app TZ=Europe/Istanbul CRAWL_MODE=remote RAW_STORAGE=gridfs \
    CRAWLER_HEARTBEAT_FILE=/data/crawler.alive LOCK_DIR=/data/locks CA_CACHE_DIR=/data/ca RAW_STORAGE_DIR=/data/raw \
    SSL_CERT_FILE=/etc/ssl/certs/mevzuat-bundle.pem REQUESTS_CA_BUNDLE=/etc/ssl/certs/mevzuat-bundle.pem

USER mevzuat
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s CMD mevzuat-crawler healthcheck || exit 1
CMD ["mevzuat-crawler", "serve"]
