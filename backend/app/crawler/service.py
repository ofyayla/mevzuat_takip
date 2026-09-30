"""DMZ crawler servisi: zamanlama + LAN'dan gelen tarama talepleri → kaynak tarama → MongoDB.

DMZ'den Redis'e ve PostgreSQL'e erişim yoktur; bu yüzden Celery kullanılmaz. Servis tek süreçtir:

- Zamanlama ``config/sources.yaml`` içindeki ``schedule`` (cron, Europe/Istanbul) alanlarından hesaplanır. Servis
  kapalıyken kaçan tetiklemeler telafi edilmez (Celery Beat'teki ``expires`` davranışıyla aynı); kaçan içerik bir
  sonraki taramada sitede durduğu için toplanır.
- ``crawl_requests`` koleksiyonu yoklanır (portal "şimdi tara", ``mevzuat-monitor backfill``).
- Kaynaklar ``CRAWLER_MAX_PARALLEL`` iş parçacığında paralel taranır; aynı kaynak asla aynı anda iki kez taranmaz
  (süreç içi takip + Mongo kilidi; ``mevzuat-crawler run`` elle çalıştırılsa bile).
- Her turda ``crawler_status`` güncellenir ve sağlık dosyasına dokunulur; LAN izlemesi sinyal gelmezse alarm açar.
"""
from __future__ import annotations

import contextlib
import logging
import signal
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime
from typing import Iterator
from zoneinfo import ZoneInfo

from app.collectors.config import SourceConfig, load_sources
from app.collectors.runner import SourceCollector
from app.crawler.mongo_store import MongoCrawlStore
from app.mongo import (
    acquire_lock,
    claim_crawl_request,
    ensure_indexes,
    finish_crawl_request,
    release_lock,
    requeue_orphaned_requests,
    utcnow,
    worker_id,
    write_heartbeat,
)
from app.settings import Settings

log = logging.getLogger("mevzuat.crawler")


class SourceBusy(Exception):
    pass


def next_fire(source: SourceConfig, after: datetime, tz: str) -> datetime | None:
    from croniter import croniter

    local = after.astimezone(ZoneInfo(tz))
    times = [croniter(expr, local).get_next(datetime) for expr in source.schedule]
    return min(times) if times else None


class CrawlerService:
    def __init__(self, settings: Settings, db, *, sources: list[SourceConfig] | None = None, fetcher_factory=None):
        self.settings = settings
        self.db = db
        self.store = MongoCrawlStore(db)
        self.owner = worker_id()
        self.sources = {s.code: s for s in (sources or load_sources(settings.sources_file).enabled())}
        self.fetcher_factory = fetcher_factory
        self.pool = ThreadPoolExecutor(settings.crawler_max_parallel, thread_name_prefix="crawl")
        self.running: dict[str, Future] = {}      # yalnızca ana iş parçacığı değiştirir
        self.next_due: dict[str, datetime | None] = {}
        self.stop = threading.Event()

    # ------------------------------------------------------------------ tek kaynak / talep

    @contextlib.contextmanager
    def _lock(self, code: str) -> Iterator[None]:
        if not acquire_lock(self.db, f"collect:{code}", self.owner, self.settings.crawler_lock_ttl_s):
            raise SourceBusy(code)
        try:
            yield
        finally:
            release_lock(self.db, f"collect:{code}", self.owner)

    def run_source(self, code: str, *, channels: list[str] | None = None, max_details: int = 200) -> dict:
        source = self.sources[code]
        with self._lock(code):
            col = SourceCollector(source, self.settings, self.store,
                                  fetcher=self.fetcher_factory(source) if self.fetcher_factory else None)
            try:
                rep = col.run(channels=channels, max_details=max_details)
            finally:
                col.fetcher.close()
        out = {"source": code, "status": rep.status, "run_id": str(rep.run_id), "new": rep.new,
               "changed": rep.changed, "requests": rep.requests,
               "errors": [f"{c.name}: {c.error}" for c in rep.channels if c.error]}
        log.info("tarama bitti %s", out)
        return out

    def run_backfill(self, code: str, date_from: date, date_to: date) -> dict:
        from app.monitoring.reprocess import backfill

        with self._lock(code):
            rep = backfill(self.store, self.settings, code, date_from, date_to, fetcher_factory=self.fetcher_factory)
        return {"source": code, "days": rep.days, "new": rep.new, "runs": rep.runs}

    def handle_request(self, req: dict) -> None:
        p = req.get("params") or {}
        try:
            if req["kind"] == "run":
                result = self.run_source(req["source_code"], channels=p.get("channels"),
                                         max_details=p.get("max_details", 200))
            elif req["kind"] == "backfill":
                result = self.run_backfill(req["source_code"], date.fromisoformat(p["date_from"]),
                                           date.fromisoformat(p["date_to"]))
            else:
                raise ValueError(f"bilinmeyen talep türü: {req['kind']}")
        except Exception as e:  # noqa: BLE001 — talep başarısız işaretlenir, servis çalışmaya devam eder
            log.exception("talep başarısız: %s", req["_id"])
            finish_crawl_request(self.db, req["_id"], ok=False, error=f"{type(e).__name__}: {e}")
            return
        finish_crawl_request(self.db, req["_id"], ok=True, result=result)

    def _scheduled(self, code: str) -> None:
        try:
            self.run_source(code)
        except SourceBusy:
            log.info("%s: başka bir tarama sürüyor, atlandı", code)
        except Exception:  # noqa: BLE001
            log.exception("%s: zamanlanmış tarama başarısız", code)

    # ------------------------------------------------------------------ döngü

    def tick(self, now: datetime | None = None) -> None:
        now = now or utcnow()
        for code, fut in list(self.running.items()):
            if fut.done():
                del self.running[code]
        for code, src in self.sources.items():
            due = self.next_due.get(code)
            if code not in self.next_due:
                self.next_due[code] = next_fire(src, now, self.settings.timezone)
            elif due is not None and due <= now:
                if code in self.running:
                    log.info("%s: önceki tarama sürüyor, bu tetikleme atlandı", code)
                else:
                    self.running[code] = self.pool.submit(self._scheduled, code)
                self.next_due[code] = next_fire(src, now, self.settings.timezone)
        while len(self.running) < self.settings.crawler_max_parallel:
            req = claim_crawl_request(self.db, self.owner, list(self.running))
            if req is None:
                break
            if req["source_code"] not in self.sources:
                finish_crawl_request(self.db, req["_id"], ok=False, error="kaynak tanımlı değil veya kapalı")
                continue
            log.info("tarama talebi alındı: %s %s", req["kind"], req["source_code"])
            self.running[req["source_code"]] = self.pool.submit(self.handle_request, req)
        self.heartbeat(now)

    def heartbeat(self, now: datetime) -> None:
        write_heartbeat(self.db, self.owner, {
            "running": sorted(self.running), "sources": len(self.sources),
            "next_due": {c: d.isoformat() for c, d in self.next_due.items() if d}})
        path = self.settings.crawler_heartbeat_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(now.isoformat())

    def serve(self) -> None:
        ensure_indexes(self.db)
        if n := requeue_orphaned_requests(self.db, self.owner):
            log.warning("yarıda kalan %d tarama talebi yeniden kuyruğa alındı", n)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: self.stop.set())
        log.info("crawler başladı: %d kaynak, paralel=%d, sahip=%s", len(self.sources),
                 self.settings.crawler_max_parallel, self.owner)
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — Mongo geçici olarak erişilemezse döngü sürer
                log.exception("crawler turu başarısız")
            self.stop.wait(self.settings.crawler_poll_s)
        log.info("durduruluyor; süren taramalar bekleniyor: %s", sorted(self.running))
        self.pool.shutdown(wait=True)
