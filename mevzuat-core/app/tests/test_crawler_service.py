"""DMZ crawler: Mongo/GridFS'e yazım, tarama talepleri, kilit, canlılık sinyali ve zamanlama.

Gerçek MongoDB gerekir (``mdb`` fixture'ı, tests/conftest.py); erişilemezse atlanır.
"""
from __future__ import annotations

from concurrent.futures import wait
from datetime import datetime, timezone

import pytest

from app.collectors.config import SourceConfig
from app.collectors.runner import SourceCollector
from app.crawler.mongo_store import MongoCrawlStore
from app.crawler.service import CrawlerService, SourceBusy, next_fire
from app.mongo import DOCS, RUNS, acquire_lock, get_crawl_request, submit_crawl_request
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
