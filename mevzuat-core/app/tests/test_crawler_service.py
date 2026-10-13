"""DMZ crawler: Mongo/GridFS'e yazım, tarama talepleri, kilit, canlılık sinyali ve zamanlama.

Gerçek MongoDB gerekir (``mdb`` fixture'ı, tests/conftest.py); erişilemezse atlanır.
"""
from __future__ import annotations

from concurrent.futures import wait
from datetime import datetime, timedelta, timezone

import pytest

from app.collectors.config import SourceConfig
from app.collectors.runner import SourceCollector
from app.crawler.mongo_store import MongoCrawlStore
from app.crawler.service import CrawlerService, SourceBusy, next_fire
from app.mongo import (
    DOCS,
    LOCKS,
    RUNS,
    acquire_lock,
    claim_crawl_request,
    get_crawl_request,
    submit_crawl_request,
    write_heartbeat,
)
from app.storage import GridFsStorage
from tests.conftest import DictFetcher
from tests.test_runner import LIST, listing, make_source


def pages_v1():
    return {
        LIST: (listing(("/d/1", "Birinci Duyuru", "24.09.2026"), ("/d/2", "İkinci Duyuru", "25.09.2026")), "text/html"),
        "https://ornek.gov.tr/d/1": ("<html><body>bir <a href='/ek/1.pdf'>ek</a></body></html>", "text/html"),
        "https://ornek.gov.tr/d/2": ("<html><body>iki</body></html>", "text/html"),
        "https://ornek.gov.tr/ek/1.pdf": (b"%PDF-1.4 ek", "application/pdf"),
    }


def test_crawl_writes_documents_versions_and_gridfs(remote, mdb):
    settings, _ = remote
    src, pages = make_source(), pages_v1()
    col = SourceCollector(src, settings, MongoCrawlStore(mdb), fetcher=DictFetcher(src, settings, pages))
    assert col.run().new == 2
    assert mdb[DOCS].count_documents({}) == 3 and mdb[DOCS].count_documents({"synced": False}) == 3
    att = mdb[DOCS].find_one({"role": "attachment"})
    main = mdb[DOCS].find_one({"external_id": "/d/1"})
    assert att["parent_id"] == main["_id"] and main["published_at"] == datetime(2026, 9, 24, tzinfo=timezone.utc)
    assert GridFsStorage(mdb).get(att["storage_key"]) == b"%PDF-1.4 ek"

    # 2. tarama: liste aynı → yeni belge yazılmaz, yalnızca liste istenir
    col.fetcher.requested.clear()
    assert col.run().new == 0 and col.fetcher.requested == [LIST]
    assert [r["status"] for r in mdb[RUNS].find(sort=[("started_at", 1)])] == ["success", "success"]

    # İçerik değişti → sürüm 2; eski sürüm is_latest=False ve yeniden aktarılmak üzere synced=False
    mdb[DOCS].update_many({}, {"$set": {"synced": True}})
    pages[LIST] = (listing(("/d/1", "Birinci Duyuru (düzeltme)", "24.09.2026"),
                           ("/d/2", "İkinci Duyuru", "25.09.2026")), "text/html")
    pages["https://ornek.gov.tr/d/1"] = ("<html><body>bir — düzeltilmiş</body></html>", "text/html")
    assert col.run().changed == 1
    v = [(d["version"], d["is_latest"], d["synced"]) for d in mdb[DOCS].find({"external_id": "/d/1"}, sort=[("version", 1)])]
    assert v == [(1, False, False), (2, True, False)]


def test_service_handles_requests_lock_and_heartbeat(remote, mdb):
    settings, _ = remote
    src = make_source()
    svc = CrawlerService(settings, mdb, sources=[src], fetcher_factory=lambda s: DictFetcher(s, settings, pages_v1()))
    ok = submit_crawl_request(mdb, "run", "ORNEK", requested_by="test")
    bad = submit_crawl_request(mdb, "run", "YOK")
    svc.tick()
    wait(list(svc.running.values()))
    assert get_crawl_request(mdb, ok)["status"] == "done"
    assert get_crawl_request(mdb, ok)["result"]["new"] == 2
    assert get_crawl_request(mdb, bad)["status"] == "failed"
    assert mdb["crawler_status"].find_one({"_id": svc.owner})["sources"] == 1
    assert settings.crawler_heartbeat_file.exists()

    # Başka bir süreç (ör. elle `mevzuat-crawler run`) kaynağı kilitlemişse tarama yapılmaz
    assert acquire_lock(mdb, "collect:ORNEK", "baska-host", 3600)
    with pytest.raises(SourceBusy):
        svc.run_source("ORNEK")
    svc.pool.shutdown()


def test_schedule_fires_when_due(remote, mdb):
    settings, _ = remote
    src = SourceConfig.model_validate({**make_source().model_dump(), "schedule": ["0 9 * * 1-5"]})
    friday = datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc)                   # Cuma 10:00 TR
    assert next_fire(src, friday, "Europe/Istanbul") == datetime(2026, 9, 28, 6, 0, tzinfo=timezone.utc)
    svc = CrawlerService(settings, mdb, sources=[src], fetcher_factory=lambda s: DictFetcher(s, settings, pages_v1()))
    svc.tick(friday)
    assert not svc.running                                                    # ilk tur yalnızca zamanı hesaplar
    svc.tick(datetime(2026, 9, 28, 6, 0, 30, tzinfo=timezone.utc))
    assert "ORNEK" in svc.running
    wait(list(svc.running.values()))
    assert mdb[DOCS].count_documents({}) == 3
    svc.pool.shutdown()


def test_interrupted_write_is_completed_by_next_scan_and_old_attachments_retire(remote, mdb, monkeypatch):
    """Ana belge yazımı ekleri kaybettirmemeli: kesinti sonrası ikinci tarama ekleri de yazar (çift kayıt olmadan)."""
    settings, _ = remote
    src, pages = make_source(), pages_v1()
    store = MongoCrawlStore(mdb)
    col = SourceCollector(src, settings, store, fetcher=DictFetcher(src, settings, pages))

    coll_type, calls = type(mdb[DOCS]), {"n": 0}
    real_insert = coll_type.insert_one

    def flaky_insert(self, doc, *a, **k):   # ilk ana belge yazımı Mongo kopmasıyla kesilir
        if self.name == DOCS:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("mongo koptu")
        return real_insert(self, doc, *a, **k)

    monkeypatch.setattr(coll_type, "insert_one", flaky_insert)
    rep = col.run()
    assert rep.status == "failed" and "mongo koptu" in rep.channels[0].error
    assert mdb[RUNS].count_documents({"status": "running"}) == 0
    monkeypatch.undo()

    rep = col.run()
    assert rep.status == "success" and rep.new == 2
    assert mdb[DOCS].count_documents({"role": "attachment"}) == 1          # kalıntı ek aynı kayıtla değiştirildi
    main = mdb[DOCS].find_one({"external_id": "/d/1"})
    assert mdb[DOCS].find_one({"role": "attachment"})["parent_id"] == main["_id"]

    # yeni sürüm: eski sürümün eki de is_latest=False olur
    pages["https://ornek.gov.tr/d/1"] = ("<html><body>bir v2 <a href='/ek/1.pdf'>ek</a></body></html>", "text/html")
    pages[LIST] = (listing(("/d/1", "Birinci Duyuru (2)", "24.09.2026"), ("/d/2", "İkinci Duyuru", "25.09.2026")),
                   "text/html")
    assert col.run().changed == 1
    att = [(d["version"], d["is_latest"]) for d in mdb[DOCS].find({"role": "attachment"}, sort=[("version", 1)])]
    assert att == [(1, False), (2, True)]


def test_sweep_cleans_dead_owner_leftovers_but_not_live_or_unknown_ones(remote, mdb):
    settings, _ = remote
    src = make_source()
    svc = CrawlerService(settings, mdb, sources=[src], fetcher_factory=lambda s: DictFetcher(s, settings, pages_v1()))
    now = datetime.now(timezone.utc)   # sinyal belgeleri TTL indeksli: sabit eski tarih kullanılamaz
    stale_at = now - timedelta(minutes=10)

    write_heartbeat(mdb, "olu-host", {})
    mdb["crawler_status"].update_one({"_id": "olu-host"}, {"$set": {"at": stale_at}})
    write_heartbeat(mdb, "canli-host", {})
    mdb["crawler_status"].update_one({"_id": "canli-host"}, {"$set": {"at": now}})
    assert acquire_lock(mdb, "collect:ORNEK", "olu-host", 3600)
    assert acquire_lock(mdb, "collect:BASKA", "canli-host", 3600)
    assert acquire_lock(mdb, "collect:ELLE", "elle-calisan", 3600)      # sinyal belgesi yok → bilinmeyen: dokunulmaz

    rid = submit_crawl_request(mdb, "run", "ORNEK")
    assert claim_crawl_request(mdb, "olu-host", [])["_id"].__str__() == rid
    live_id = submit_crawl_request(mdb, "run", "BASKA")
    claim_crawl_request(mdb, "canli-host", [])
    mdb[RUNS].insert_many([
        {"source_code": "ORNEK", "status": "running", "started_at": stale_at, "rev": 1},
        {"source_code": "BASKA", "status": "running", "started_at": stale_at, "rev": 1},
    ])

    svc.sweep(now)
    assert mdb[LOCKS].find_one({"_id": "collect:ORNEK"}) is None
    assert mdb[LOCKS].find_one({"_id": "collect:BASKA"}) and mdb[LOCKS].find_one({"_id": "collect:ELLE"})
    assert get_crawl_request(mdb, rid)["status"] == "pending" and "claimed_by" not in get_crawl_request(mdb, rid)
    assert get_crawl_request(mdb, live_id)["status"] == "running"
    runs = {r["source_code"]: r["status"] for r in mdb[RUNS].find()}
    assert runs == {"ORNEK": "failed", "BASKA": "running"}             # kilitli kaynağın taraması sürüyor sayılır
    assert mdb[RUNS].find_one({"source_code": "ORNEK"})["synced"] is False
    svc.pool.shutdown()


def test_restart_with_changed_hostname_recovers_within_a_few_minutes(remote, mdb):
    """SIGKILL sonrası yeni konteyner farklı host adıyla gelir: bayat kilit/talep sinyal bayatlayınca temizlenir."""
    settings, _ = remote
    src = make_source()
    dead = datetime.now(timezone.utc) - timedelta(seconds=30)
    write_heartbeat(mdb, "eski-konteyner", {})
    mdb["crawler_status"].update_one({"_id": "eski-konteyner"}, {"$set": {"at": dead}})
    acquire_lock(mdb, "collect:ORNEK", "eski-konteyner", 3600)
    rid = submit_crawl_request(mdb, "run", "ORNEK")
    claim_crawl_request(mdb, "eski-konteyner", [])

    svc = CrawlerService(settings, mdb, sources=[src], fetcher_factory=lambda s: DictFetcher(s, settings, pages_v1()))
    svc.sweep(datetime.now(timezone.utc))
    assert mdb[LOCKS].find_one({"_id": "collect:ORNEK"}) is not None     # sinyal henüz taze (30 sn): ölü sayılmaz

    later = datetime.now(timezone.utc) + timedelta(minutes=3)            # 3 dk sonra sinyal gelmediyse ölü
    svc.tick(later)
    wait(list(svc.running.values()))
    assert get_crawl_request(mdb, rid)["status"] == "done"               # temizlendi, yeniden alındı, tamamlandı
    assert mdb[LOCKS].find_one({"_id": "collect:ORNEK"}) is None
    svc.pool.shutdown()


def test_startup_releases_own_stale_locks_and_requests(remote, mdb):
    settings, _ = remote
    svc = CrawlerService(settings, mdb, sources=[make_source()], fetcher_factory=None)
    acquire_lock(mdb, "collect:ORNEK", svc.owner, 3600)
    rid = submit_crawl_request(mdb, "run", "ORNEK")
    claim_crawl_request(mdb, svc.owner, [])
    from app.mongo import release_locks_of, requeue_orphaned_requests

    assert requeue_orphaned_requests(mdb, svc.owner) == 1 and release_locks_of(mdb, [svc.owner]) == 1
    assert get_crawl_request(mdb, rid)["status"] == "pending" and mdb[LOCKS].count_documents({}) == 0
    svc.pool.shutdown()


def test_stop_flag_interrupts_rg_backfill_and_requeues_request_from_unfinished_day(remote, mdb):
    from app.crawler.service import CrawlerService as Svc

    settings, _ = remote
    src = SourceConfig.model_validate({**make_source().model_dump(), "code": "RESMI_GAZETE"})
    svc = Svc(settings, mdb, sources=[src], fetcher_factory=lambda s: DictFetcher(s, settings, {}))
    svc.stop.set()                                                       # servis kapanıyor
    rid = submit_crawl_request(mdb, "backfill", "RESMI_GAZETE",
                               {"date_from": "2026-09-01", "date_to": "2026-09-03"})
    req = claim_crawl_request(mdb, svc.owner, [])
    svc.handle_request(req)
    got = get_crawl_request(mdb, rid)
    assert got["status"] == "pending" and got["params"]["date_from"] == "2026-09-01"
    svc.pool.shutdown()
