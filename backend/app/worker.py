"""Celery uygulaması ve Beat zamanlaması.

Zamanlama config/sources.yaml içindeki ``schedule`` (cron, Europe/Istanbul) alanlarından üretilir; kaynak
başına ayrı görev olduğu için bir kaynaktaki sorun diğerlerini etkilemez. Toplama görevleri ``collect``, belge
işleme görevleri ``process`` kuyruğunda çalışır (DMZ'de ayrı worker olarak da konuşlandırılabilir — plan §2.1).

  celery -A app.worker worker -Q collect -c 4
  celery -A app.worker worker -Q process -c 2      # İK-2: metin çıkarma + tekilleştirme
  celery -A app.worker worker -Q ai -c 2           # İK-3: LLM (GPU yükü nedeniyle düşük eşzamanlılık)
  celery -A app.worker beat
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.collectors.config import load_sources
from app.settings import get_settings

settings = get_settings()

app = Celery("mevzuat", broker=settings.redis_url or "memory://", backend=None, include=["app.tasks.collect", "app.tasks.process", "app.tasks.ai"])
app.conf.update(
    timezone=settings.timezone,
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={"app.tasks.collect.*": {"queue": "collect"}, "app.tasks.process.*": {"queue": "process"},
                 "app.tasks.ai.*": {"queue": "ai"}},
    task_default_queue="default",
)


def _crontab(expr: str) -> crontab:
    minute, hour, day_of_month, month_of_year, day_of_week = expr.split()
    return crontab(minute=minute, hour=hour, day_of_month=day_of_month, month_of_year=month_of_year,
                   day_of_week=day_of_week)


def build_beat_schedule() -> dict:
    schedule = {}
    for source in load_sources(settings.sources_file).enabled():
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
    return schedule


app.conf.beat_schedule = build_beat_schedule()
