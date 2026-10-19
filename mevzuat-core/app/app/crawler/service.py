"""DMZ crawler servisi: zamanlama + LAN'dan gelen tarama talepleri → kaynak tarama → MongoDB.

DMZ'den Redis'e ve PostgreSQL'e erişim yoktur; bu yüzden Celery kullanılmaz. Servis tek süreçtir:

- Zamanlama ``config/sources.yaml`` içindeki ``schedule`` (cron, Europe/Istanbul) alanlarından hesaplanır. Servis
  kapalıyken kaçan tetiklemeler telafi edilmez (Celery Beat'teki ``expires`` davranışıyla aynı); kaçan içerik bir
  sonraki taramada sitede durduğu için toplanır.
- ``crawl_requests`` koleksiyonu yoklanır (portal "şimdi tara", ``mevzuat-monitor backfill``).
- Kaynaklar ``CRAWLER_MAX_PARALLEL`` iş parçacığında paralel taranır; aynı kaynak asla aynı anda iki kez taranmaz
  (süreç içi takip + Mongo kilidi; ``mevzuat-crawler run`` elle çalıştırılsa bile).
- Her turda ``crawler_status`` güncellenir ve sağlık dosyasına dokunulur; LAN izlemesi sinyal gelmezse alarm açar.
- Süreç SIGKILL/OOM ile ölürse kilit, ``running`` talep ve ``running`` tarama kaydı geride kalır. Açılışta kendi
  kalıntıları, ``crawler_sweep_s`` aralığıyla da sinyali bayatlamış (ölü) sahiplerin kalıntıları temizlenir; host
  adı yeniden başlatmada değişse de (Podman/K8s) kaynak en geç birkaç dakika içinde yeniden taranır.
- SIGTERM'de yeni iş alınmaz; süren taramalar bayrağı öğe aralarında kontrol edip temiz biçimde (``partial``) durur.
"""
from __future__ import annotations

import contextlib
import dataclasses
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
    DOCS,
    REQUESTS,
    RUNS,
    acquire_lock,
    claim_crawl_request,
    ensure_indexes,
    finish_crawl_request,
    interrupt_orphaned_runs,
    release_lock,
    release_locks_of,
    requeue_orphaned_requests,
    requeue_request,
    requeue_requests_of,
    stale_owners,
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
        self._last_sweep: datetime | None = None

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
            col = SourceCollector(source, self.settings, self.store, should_stop=self.stop.is_set,
                                  fetcher=self.fetcher_factory(source) if self.fetcher_factory else None)
            try:
                rep = col.run(channels=channels, max_details=max_details)
            finally:
                col.fetcher.close()
        out = {"source": code, "status": rep.status, "run_id": str(rep.run_id), "new": rep.new,
               "changed": rep.changed, "requests": rep.requests,
               "errors": rep.errors}
        log.info("tarama bitti %s", out)
        return out

    def repair_attachments(self, code: str, **options) -> dict:
        """Arşivde eksik kalan ekleri yeniden getirir (bkz. ``SourceCollector.repair_attachments``)."""
        source = self.sources[code]
        with self._lock(code):
            col = SourceCollector(source, self.settings, self.store, should_stop=self.stop.is_set,
                                  fetcher=self.fetcher_factory(source) if self.fetcher_factory else None)
            try:
                rep = col.repair_attachments(**options)
            finally:
                col.fetcher.close()
        out = dataclasses.asdict(rep)
        log.info("ek onarımı bitti %s", out)
        return out

    def reset_source(self, code: str, *, dry_run: bool = True) -> dict:
        """Kaynağın Mongo kayıtlarını (belgeler, taramalar, talepler) siler; sonraki tarama sıfırdan, BASELINE olarak başlar.

        Kaynak kilidi alınır: bu kaynağı tarayan (zamanlanmış ya da elle) bir koşu varken reddedilir (``SourceBusy``).
        Böylece koşunun ortasında silinip baseline durumunun eski veriden kalması (kayıtların baseline'sız yeni sayılması)
        önlenir. Yalnızca crawler'ın Mongo'sunu temizler: ana servisin veritabanı (PostgreSQL) ve GridFS içeriği
        (içerik hash'iyle paylaşıldığı için bırakılır) ayrıca ele alınmalıdır. ``dry_run``: yalnızca sayar.
        """
        if code not in self.sources:
            raise KeyError(code)
        flt = {"source_code": code}
        with self._lock(code):
            counts = {"documents": self.db[DOCS].count_documents(flt),
                      "attachments": self.db[DOCS].count_documents({**flt, "role": "attachment"}),
                      "runs": self.db[RUNS].count_documents(flt),
                      "requests": self.db[REQUESTS].count_documents(flt),
                      "unsynced_documents": self.db[DOCS].count_documents({**flt, "synced": False})}
            out = {"source": code, "dry_run": dry_run, **counts, "deleted": False}
            if dry_run:
                return out
            # Sıra önemli: baseline bilgisi tarama kayıtlarında (``fetch_runs.channels``) tutulur. Önce onlar silinirse
            # yarıda kalan bir silme "belgeler duruyor, baseline yeniden açık" ile sonuçlanır (zararsız); tersi sırada
            # "belgeler yok ama baseline kapalı" kalır ve tüm içerik baseline'sız yeni sayılırdı.
            self.db[RUNS].delete_many(flt)
            self.db[DOCS].delete_many(flt)
            self.db[REQUESTS].delete_many(flt)
            out["deleted"] = True
        log.warning("%s kaynağının Mongo kayıtları silindi: %s", code, out)
        return out

    def run_backfill(self, code: str, date_from: date, date_to: date, history_from: date | None = None) -> dict:
        from app.monitoring.reprocess import backfill

        with self._lock(code):
            rep = backfill(self.store, self.settings, code, date_from, date_to, fetcher_factory=self.fetcher_factory,
                           should_stop=self.stop.is_set, history_from=history_from)
        out = {"source": code, "days": rep.days, "new": rep.new, "runs": rep.runs}
        if rep.interrupted_at:
            out["interrupted_at"] = rep.interrupted_at.isoformat()
        return out

    def handle_request(self, req: dict) -> None:
        p = req.get("params") or {}
        try:
            if req["kind"] == "run":
                result = self.run_source(req["source_code"], channels=p.get("channels"),
                                         max_details=p.get("max_details", 200))
            elif req["kind"] == "backfill":
                result = self.run_backfill(req["source_code"], date.fromisoformat(p["date_from"]),
                                           date.fromisoformat(p["date_to"]),
                                           date.fromisoformat(p["history_from"]) if p.get("history_from") else None)
                if resume := result.get("interrupted_at"):   # servis kapanıyor: talep kaldığı günden sürdürülür
                    requeue_request(self.db, req["_id"], {"date_from": resume})
                    log.info("backfill %s günden sürdürülmek üzere kuyruğa alındı", resume)
                    return
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

    def sweep(self, now: datetime) -> None:
        """Ölü bir crawler'ın kilit/talep/tarama kalıntılarını temizler (sahip kimliği değişmiş olsa bile)."""
        self._last_sweep = now
        stale = stale_owners(self.db, self.owner, now, max(4 * self.settings.crawler_poll_s, 120))
        locks, reqs = release_locks_of(self.db, stale), requeue_requests_of(self.db, stale)
        runs = interrupt_orphaned_runs(self.db, now)
        if locks or reqs or runs:
            log.warning("ölü süreç kalıntıları temizlendi: sahipler=%s kilit=%d talep=%d tarama=%d",
                        stale, locks, reqs, runs)

    def _maybe_sweep(self, now: datetime) -> None:
        if self._last_sweep is None or (now - self._last_sweep).total_seconds() >= self.settings.crawler_sweep_s:
            try:
                self.sweep(now)
            except Exception:  # noqa: BLE001 — temizlik başarısızsa tarama ve sağlık sinyali aksamasın
                log.exception("kalıntı temizliği başarısız")

    def tick(self, now: datetime | None = None) -> None:
        now = now or utcnow()
        self._maybe_sweep(now)
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
        if n := release_locks_of(self.db, [self.owner]):
            log.warning("önceki sürecin %d kilidi bırakıldı", n)
        self._maybe_sweep(utcnow())
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
        self.pool.shutdown(wait=True, cancel_futures=True)
