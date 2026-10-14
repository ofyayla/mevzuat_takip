"""Ana servis tarafı (CRAWL_MODE=remote): MongoDB → PostgreSQL/SQLite aktarımı, GridFS okuma, izleme, API ve Beat.

Mongo'ya veri crawler'ın kayıt katmanıyla (MongoCrawlStore) yazılır; Mongo sözleşmesi iki servis arasında ortaktır.
Gerçek MongoDB gerekir (``mdb`` fixture'ı); erişilemezse atlanır.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.collectors.runner import SourceCollector
from app.crawler.mongo_store import MongoCrawlStore
from app.db import FetchRun, RawDocument
from app.ingest import CrawlIngestor
from app.mongo import DOCS, REQUESTS, submit_crawl_request, write_heartbeat
from app.monitoring.crawler import crawler_findings
from app.storage import get_storage
from tests.conftest import DictFetcher
from tests.test_crawler_service import pages_v1
from tests.test_runner import LIST, listing, make_source


def sql_docs(sf):
    with sf() as s:
        return s.scalars(select(RawDocument).order_by(RawDocument.id)).all()


def crawl(settings, mdb, pages=None):
    src = make_source()
    col = SourceCollector(src, settings, MongoCrawlStore(mdb), fetcher=DictFetcher(src, settings, pages or pages_v1()))
    col.run()
    return col


def test_ingest_documents_runs_and_versions(remote, mdb):
    settings, sf = remote
    pages = pages_v1()
    col = crawl(settings, mdb, pages)
    col.run()                                          # ikinci tarama (değişiklik yok)
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
    assert get_storage(settings).get(att_row.storage_key) == b"%PDF-1.4 ek"   # işleme hattı GridFS'ten okur

    assert CrawlIngestor(settings, sf, mdb).run().documents_new == 0            # idempotent

    pages[LIST] = (listing(("/d/1", "Birinci Duyuru (düzeltme)", "24.09.2026"),
                           ("/d/2", "İkinci Duyuru", "25.09.2026")), "text/html")
    pages["https://ornek.gov.tr/d/1"] = ("<html><body>bir — düzeltilmiş</body></html>", "text/html")
    assert col.run().changed == 1
    rep = CrawlIngestor(settings, sf, mdb).run()
    assert rep.documents_new == 1 and rep.documents_updated == 1
    assert [(r.version, r.is_latest) for r in sql_docs(sf) if r.external_id == "/d/1"] == [(1, False), (2, True)]


def test_ingest_does_not_mark_synced_if_changed_meanwhile(remote, mdb):
    settings, sf = remote
    crawl(settings, mdb)
    ing = CrawlIngestor(settings, sf, mdb)
    rows = list(mdb[DOCS].find({"synced": False}))
    mdb[DOCS].update_one({"_id": rows[0]["_id"]}, {"$set": {"title": "yeni"}, "$inc": {"rev": 1}})
    ing._mark_synced(DOCS, rows)                     # okunan rev artık eski → işaretlenmez
    assert [d["_id"] for d in mdb[DOCS].find({"synced": False})] == [rows[0]["_id"]]


def test_ingest_in_small_batches_keeps_attachment_links(remote, mdb):
    settings, sf = remote
    crawl(settings, mdb)
    ing = CrawlIngestor(settings, sf, mdb)
    for _ in range(5):
        ing.run(limit=1)
    rows = sql_docs(sf)
    assert len(rows) == 3
    att = next(r for r in rows if r.role == "attachment")
    assert att.parent_id == next(r.id for r in rows if r.external_id == "/d/1")


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
    # küçük harfli kod kanonik hale getirilir; aksi halde crawler talebi "kaynak tanımlı değil" diye reddeder
    assert c.post("/api/v1/admin/sources/bddk/run").status_code == 202
    assert {d["source_code"] for d in mdb[REQUESTS].find({"kind": "run"})} == {"BDDK"}


def test_beat_schedule_remote_mode_has_ingest_not_collect(remote, monkeypatch):
    import app.worker as worker

    monkeypatch.setattr(worker, "settings", remote[0])
    sched = worker.build_beat_schedule()
    assert "ingest-crawl" in sched and not any(k.startswith("collect-") for k in sched)


def test_late_attachment_is_ingested_under_the_existing_parent(remote, mdb):
    """CDN erişilemezken ek alınamaz; sonradan alınınca LAN tarafı ekı mevcut ana belgeye bağlar (yeni sürüm yok)."""
    from app.collectors.http import TransientFetchError
    from tests.test_runner import FlakyFetcher

    settings, sf = remote
    src, ek = make_source(), "https://ornek.gov.tr/ek/1.pdf"
    fetcher = FlakyFetcher(src, settings, pages_v1(), {ek: TransientFetchError(ek, "reset")})
    col = SourceCollector(src, settings, MongoCrawlStore(mdb), fetcher=fetcher)
    assert col.run().status == "partial"
    ing = CrawlIngestor(settings, sf, mdb)
    ing.run()
    assert not [r for r in sql_docs(sf) if r.role == "attachment"]
    main1 = next(r for r in sql_docs(sf) if r.external_id == "/d/1")
    assert [m["url"] for m in main1.extra["missing_attachments"]] == [ek]

    del fetcher.errors[ek]
    assert col.run(now=datetime.now(timezone.utc) + timedelta(hours=2)).status == "success"
    rep = ing.run()
    assert rep.deferred == 0
    rows = sql_docs(sf)
    att = [r for r in rows if r.role == "attachment"]
    main1 = next(r for r in rows if r.external_id == "/d/1")
    assert len(att) == 1 and att[0].parent_id == main1.id and att[0].version == main1.version == 1
    assert main1.extra["missing_attachments"] == [] and main1.is_latest
    assert mdb[DOCS].count_documents({"synced": False}) == 0


def test_repair_attachments_with_mongo_store_and_ingest(remote, mdb):
    """Onarım komutu Mongo'da da çalışır: eksik ek GridFS içeriğinden yeniden çıkarılır, LAN tarafı ekı alır."""
    from app.collectors.http import TransientFetchError
    from tests.test_runner import FlakyFetcher

    settings, sf = remote
    src, ek = make_source(), "https://ornek.gov.tr/ek/1.pdf"
    fetcher = FlakyFetcher(src, settings, pages_v1(), {ek: TransientFetchError(ek, "reset")})
    col = SourceCollector(src, settings, MongoCrawlStore(mdb), fetcher=fetcher)
    col.run()
    mdb[DOCS].update_many({"role": "main"}, {"$unset": {"extra.missing_attachments": ""}})   # eski hasar
    del fetcher.errors[ek]
    rep = col.repair_attachments()
    assert rep.docs_with_gaps == 1 and rep.recovered == 1
    att = list(mdb[DOCS].find({"role": "attachment"}))
    main = mdb[DOCS].find_one({"external_id": "/d/1"})
    assert len(att) == 1 and att[0]["parent_id"] == main["_id"] and att[0]["fetch_run_id"] is None
    assert main["extra"]["missing_attachments"] == []
    assert col.repair_attachments().docs_with_gaps == 0                                      # idempotent

    CrawlIngestor(settings, sf, mdb).run()
    rows = sql_docs(sf)
    assert [r.role for r in rows if r.role == "attachment"] == ["attachment"]
    assert mdb[DOCS].count_documents({"synced": False}) == 0
