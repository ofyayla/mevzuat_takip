"""Celery uygulaması ve Beat zamanlaması (ana servis).

İki kurulum biçimi (``CRAWL_MODE``):

- ``remote`` (kurum: DMZ + Kubernetes): toplama DMZ'deki ``mevzuat-crawler``'dadır. Beat burada toplama görevi
  üretmez; her dakika ``ingest_crawl`` MongoDB'deki yeni kayıtları PostgreSQL'e aktarır ve işleme hattını tetikler.
- ``local`` (geliştirme / tek kutu): zamanlama config/sources.yaml ``schedule`` (cron, Europe/Istanbul) alanlarından
  üretilir; kaynak başına ayrı görev ``collect`` kuyruğunda çalışır.

Kuyruklar: ingest · collect · process (metin çıkarma/OCR/tekilleştirme) · ai (LLM) · monitor. Başlangıçta tek worker
hepsini dinler; yük artınca (ör. KEP) aynı imajdan kuyruk bazında ayrı worker açılır — kod değişmez:

  celery -A app.worker worker -Q ingest,process,ai,monitor -c 4       # K8s: core-worker (başlangıç)
  celery -A app.worker worker -Q ai -c 2                              # ileride: ayrı YZ worker'ı
  celery -A app.worker beat                                           # TEK örnek

Redis yalnızca tetikleyicidir: asıl durum PostgreSQL'deki durum makinesindedir (FETCHED/EXTRACTED, NEW/CLASSIFIED…)
ve Beat'in "bekleyenleri işle" görevleri güvenlik ağıdır. Redis boşalsa iş kaybolmaz, bir sonraki turda sürer; bu
yüzden görevler iki kez çalışmaya dayanıklıdır (acks_late + reject_on_worker_lost).
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.collectors.config import load_sources
from app.settings import get_settings

settings = get_settings()

app = Celery("mevzuat", broker=settings.redis_url or "memory://", backend=None,
             include=["app.tasks.collect", "app.tasks.ingest", "app.tasks.process", "app.tasks.ai", "app.tasks.monitor"])
app.conf.update(
    timezone=settings.timezone,
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,          # pod öldürülürse görev yeniden teslim edilir (görevler idempotent)
    worker_prefetch_multiplier=1,
    task_time_limit=settings.celery_task_time_limit_s,
    task_soft_time_limit=max(settings.celery_task_time_limit_s - 300, 60),
    worker_max_tasks_per_child=settings.celery_max_tasks_per_child,   # uzun ömürlü süreçte bellek birikmesin
    broker_connection_retry_on_startup=True,
    # Redis'te onaylanmamış görev bu süre sonunda başka worker'a verilir; en uzun görevden uzun tutulur
    broker_transport_options={"visibility_timeout": settings.celery_visibility_timeout_s},
    task_routes={"app.tasks.collect.*": {"queue": "collect"}, "app.tasks.ingest.*": {"queue": "ingest"},
                 "app.tasks.process.*": {"queue": "process"}, "app.tasks.ai.*": {"queue": "ai"},
                 "app.tasks.monitor.*": {"queue": "monitor"}},
    task_default_queue="default",
)


def _crontab(expr: str) -> crontab:
    minute, hour, day_of_month, month_of_year, day_of_week = expr.split()
    return crontab(minute=minute, hour=hour, day_of_month=day_of_month, month_of_year=month_of_year,
                   day_of_week=day_of_week)


def build_beat_schedule() -> dict:
    schedule = {}
    if settings.crawl_mode == "remote":   # toplama DMZ crawler'da; burada yalnızca aktarım
        schedule["ingest-crawl"] = {"task": "app.tasks.ingest.ingest_crawl", "schedule": _crontab("* * * * *"),
                                    "options": {"expires": 60}}
        sources = []
    else:
        sources = load_sources(settings.sources_file).enabled()
    for source in sources:
        for i, expr in enumerate(source.schedule):
            schedule[f"collect-{source.code}-{i}"] = {
                "task": "app.tasks.collect.collect_source",
                "schedule": _crontab(expr),
                "args": (source.code,),
                "options": {"expires": 3600},  # birikmiş eski tetiklemeler çalıştırılmaz
            }
    # Güvenlik ağı: tetiklemesi kaçan veya yeniden denenecek (EXTRACT_FAILED) belgeler
    schedule["process-pending"] = {"task": "app.tasks.process.process_pending", "schedule": _crontab("*/15 * * * *"),
                                   "options": {"expires": 900}}
    schedule["classify-pending"] = {"task": "app.tasks.ai.classify_regulations", "schedule": _crontab("7,37 * * * *"),
                                    "options": {"expires": 1800}}
    schedule["summarize-pending"] = {"task": "app.tasks.ai.summarize_regulations",
                                     "schedule": _crontab("17,47 * * * *"), "options": {"expires": 1800}}
    schedule["monitor"] = {"task": "app.tasks.monitor.run_monitor", "schedule": _crontab("*/10 * * * *"),
                           "options": {"expires": 600}}
    schedule["match-pending"] = {"task": "app.tasks.ai.match_units", "schedule": _crontab("27,57 * * * *"),
                                 "options": {"expires": 1800}}
    return schedule


app.conf.beat_schedule = build_beat_schedule()
