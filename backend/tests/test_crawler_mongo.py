"""DMZ crawler → MongoDB → ingest → PostgreSQL/SQLite hattı (CRAWL_MODE=remote).

Gerçek MongoDB gerekir (GridFS, find_one_and_update, kısmi indeksler). ``MONGO_TEST_URL`` (varsayılan
mongodb://localhost:27018) erişilemezse testler atlanır:

  docker run -d --name mevzuat-mongo-test -p 27018:27017 mongo:7
"""
from __future__ import annotations

import os
import uuid
from concurrent.futures import wait
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.collectors.config import SourceConfig
from app.collectors.runner import SourceCollector
from app.crawler.mongo_store import MongoCrawlStore
from app.crawler.service import CrawlerService, SourceBusy, next_fire
from app.db import FetchRun, RawDocument, make_sessionmaker
from app.ingest import CrawlIngestor
from app.mongo import (
    DOCS,
    REQUESTS,
    acquire_lock,
    ensure_indexes,
    get_crawl_request,
    submit_crawl_request,
    write_heartbeat,
)
from app.monitoring.crawler import crawler_findings
from app.storage import GridFsStorage
from tests.conftest import DictFetcher
from tests.test_runner import LIST, listing, make_source

MONGO_URL = os.environ.get("MONGO_TEST_URL", "mongodb://localhost:27018")


@pytest.fixture
def mdb():
    pymongo = pytest.importorskip("pymongo")
    client = pymongo.MongoClient(MONGO_URL, tz_aware=True, serverSelectionTimeoutMS=1500)
    try:
        client.admin.command("ping")
    except pymongo.errors.PyMongoError:
        pytest.skip(f"MongoDB erişilemiyor: {MONGO_URL}")
    name = f"mevzuat_test_{uuid.uuid4().hex[:8]}"
    db = client[name]
    ensure_indexes(db)
    yield db
    client.drop_database(name)
    client.close()


@pytest.fixture
def remote(settings, mdb):
    s = settings.model_copy(update={"crawl_mode": "remote", "raw_storage": "gridfs", "mongo_url": MONGO_URL,
                                    "mongo_db": mdb.name})
    return s, make_sessionmaker(s.database_url)


def pages_v1():
    return {
        LIST: (listing(("/d/1", "Birinci Duyuru", "24.09.2026"), ("/d/2", "İkinci Duyuru", "25.09.2026")), "text/html"),
        "https://ornek.gov.tr/d/1": ("<html><body>bir <a href='/ek/1.pdf'>ek</a></body></html>", "text/html"),
        "https://ornek.gov.tr/d/2": ("<html><body>iki</body></html>", "text/html"),
        "https://ornek.gov.tr/ek/1.pdf": (b"%PDF-1.4 ek", "application/pdf"),
    }


def sql_docs(sf):
    with sf() as s:
        return s.scalars(select(RawDocument).order_by(RawDocument.id)).all()


def test_crawl_to_mongo_then_ingest(remote, mdb):
    settings, sf = remote
    src, pages = make_source(), pages_v1()
    col = SourceCollector(src, settings, MongoCrawlStore(mdb), fetcher=DictFetcher(src, settings, pages))
    assert col.run().new == 2
    assert mdb[DOCS].count_documents({}) == 3 and mdb[DOCS].count_documents({"synced": False}) == 3
    att = mdb[DOCS].find_one({"role": "attachment"})
    assert GridFsStorage(mdb).get(att["storage_key"]) == b"%PDF-1.4 ek"

    # 2. tarama: liste aynı → Mongo'ya yeni belge yazılmaz, yalnızca liste istenir
    col.fetcher.requested.clear()
    assert col.run().new == 0 and col.fetcher.requested == [LIST]

    rep = CrawlIngestor(settings, sf, mdb).run()
    assert (rep.sources, rep.runs, rep.documents_new, rep.deferred) == (1, 2, 3, 0)
    assert rep.new_by_source == {"ORNEK": 3}
    rows = sql_docs(sf)
    main1 = next(r for r in rows if r.external_id == "/d/1")
    att_row = next(r for r in rows if r.role == "attachment")
    assert att_row.parent_id == main1.id and main1.published_at.isoformat() == "2026-09-24"
    assert all(r.processing_status == "FETCHED" and r.fetch_run_id for r in rows)
    assert mdb[DOCS].count_documents({"synced": False}) == 0
    with sf() as s:   # izleme (İK-7) aynı tabloları okur: taramalar da aktarılır
        assert [r.status for r in s.scalars(select(FetchRun).order_by(FetchRun.started_at))] == ["success"] * 2
    # işleme hattı içeriği GridFS'ten okur
    from app.storage import get_storage
    assert get_storage(settings).get(att_row.storage_key) == b"%PDF-1.4 ek"

    # İdempotent: tekrar aktarımda değişiklik yok
    assert CrawlIngestor(settings, sf, mdb).run().documents_new == 0

    # İçerik değişti → Mongo'da sürüm 2; aktarımda eski sürümün is_latest'i de güncellenir
    pages[LIST] = (listing(("/d/1", "Birinci Duyuru (düzeltme)", "24.09.2026"),
                           ("/d/2", "İkinci Duyuru", "25.09.2026")), "text/html")
    pages["https://ornek.gov.tr/d/1"] = ("<html><body>bir — düzeltilmiş</body></html>", "text/html")
    assert col.run().changed == 1
    rep = CrawlIngestor(settings, sf, mdb).run()
    assert rep.documents_new == 1 and rep.documents_updated == 1
    v = [(r.version, r.is_latest) for r in sql_docs(sf) if r.external_id == "/d/1"]
    assert v == [(1, False), (2, True)]


def test_ingest_does_not_mark_synced_if_changed_meanwhile(remote, mdb):
    settings, sf = remote
    src = make_source()
    SourceCollector(src, settings, MongoCrawlStore(mdb), fetcher=DictFetcher(src, settings, pages_v1())).run()
    ing = CrawlIngestor(settings, sf, mdb)
    rows = list(mdb[DOCS].find({"synced": False}))
    mdb[DOCS].update_one({"_id": rows[0]["_id"]}, {"$set": {"title": "yeni"}, "$inc": {"rev": 1}})
    ing._mark_synced(DOCS, rows)                     # okunan rev artık eski → işaretlenmez
    assert [d["_id"] for d in mdb[DOCS].find({"synced": False})] == [rows[0]["_id"]]


def test_ingest_in_small_batches_keeps_attachment_links(remote, mdb):
    settings, sf = remote
    src = make_source()
    SourceCollector(src, settings, MongoCrawlStore(mdb), fetcher=DictFetcher(src, settings, pages_v1())).run()
    ing = CrawlIngestor(settings, sf, mdb)
    for _ in range(5):
        ing.run(limit=1)
    rows = sql_docs(sf)
    assert len(rows) == 3
    att = next(r for r in rows if r.role == "attachment")
    assert att.parent_id == next(r.id for r in rows if r.external_id == "/d/1")


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


def test_monitoring_crawler_findings(remote, mdb):
    settings, _ = remote
    now = datetime.now(timezone.utc)
    assert {f.alert_type for f in crawler_findings(settings, now, mdb)} == {"crawler_down"}
    write_heartbeat(mdb, "dmz-1", {"running": []})
    assert crawler_findings(settings, now, mdb) == []
    mdb[DOCS].insert_one({"synced": False, "updated_at": now - timedelta(hours=1)})
    submit_crawl_request(mdb, "run", "BDDK")
    mdb[REQUESTS].update_many({}, {"$set": {"created_at": now - timedelta(hours=1)}})
    assert {f.alert_type for f in crawler_findings(settings, now, mdb)} == {"ingest_lag", "crawl_request"}
    down = crawler_findings(settings.model_copy(update={"mongo_url": "mongodb://127.0.0.1:1",
                                                        "mongo_timeout_ms": 300}), now)
    assert down[0].alert_type == "crawler_down" and "erişilemiyor" in down[0].message


def test_api_run_and_backfill_go_to_crawler_in_remote_mode(remote, mdb):
    from app.api.main import create_app

    settings, sf = remote
    c = TestClient(create_app(settings.model_copy(update={"cors_origins": ""}), sf))
    r = c.post("/api/v1/admin/sources/BDDK/run")
    assert r.status_code == 202 and r.json()["via"] == "crawler"
    r = c.post("/api/v1/admin/sources/RESMI_GAZETE/backfill", json={"from": "2026-09-01", "to": "2026-09-03"})
    assert r.json()["via"] == "crawler" and r.json()["days"] == 3
    reqs = {d["kind"]: d for d in mdb[REQUESTS].find()}
    assert reqs["run"]["source_code"] == "BDDK" and reqs["run"]["status"] == "pending"
    assert reqs["backfill"]["params"] == {"date_from": "2026-09-01", "date_to": "2026-09-03"}


def test_beat_schedule_remote_mode_has_ingest_not_collect(remote, monkeypatch):
    import app.worker as worker

    monkeypatch.setattr(worker, "settings", remote[0])
    sched = worker.build_beat_schedule()
    assert "ingest-crawl" in sched and not any(k.startswith("collect-") for k in sched)
