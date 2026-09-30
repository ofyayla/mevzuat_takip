# Portal API (FastAPI) — kurum düzeni: gunicorn + UvicornWorker. Arka plan işleri Celery'dedir (worker pod'u);
# API isteği uzun sürmez, bu yüzden zaman aşımı kısa tutulur.
bind = '0.0.0.0:5000'
workers = 2
threads = 4
timeout = 120
graceful_timeout = 30
worker_class = 'uvicorn.workers.UvicornWorker'
accesslog = '-'
forwarded_allow_ips = '*'
