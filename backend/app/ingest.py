"""DMZ crawler çıktısının ana servise aktarımı (CRAWL_MODE=remote): MongoDB → PostgreSQL.

Sıra: sources → fetch_runs → raw_documents. Her kayıt ``ext_id`` (Mongo ``_id``) ile idempotent upsert edilir;
Mongo'da ``synced=True`` işareti yalnızca ``rev`` değişmemişse konur (bkz. app.mongo). Ham içerik aktarılmaz —
işleme hattı GridFS'ten okur (RAW_STORAGE=gridfs). Aktarımdan sonra akış yerel kurulumdakiyle aynıdır:
FETCHED → metin çıkarma → tekilleştirme → YZ.

Bir ek, ana belgesi henüz aktarılmamışsa (farklı partide) bekletilir ve bir sonraki turda alınır.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db import FetchRun, RawDocument, Source
from app.mongo import DOCS, RUNS, SOURCES
from app.settings import Settings

log = logging.getLogger(__name__)

RUN_FIELDS = ("source_code", "started_at", "finished_at", "status", "items_listed", "items_new", "items_changed",
              "requests", "channels", "errors", "structure_alert")
DOC_FIELDS = ("source_code", "channel", "external_id", "version", "is_latest", "role", "url", "final_url", "title",
              "category", "listing_hash", "version_key", "content_type", "content_sha256", "size_bytes",
              "storage_key", "fetched_at", "is_baseline", "extra")
# Crawler'ın sonradan değiştirebildiği alanlar (yeni sürüm, liste satırı güncellemesi, yeniden kontrol zamanı)
DOC_MUTABLE = ("is_latest", "listing_hash", "version_key", "title", "extra")


@dataclass
class IngestReport:
    sources: int = 0
    runs: int = 0
    documents_new: int = 0
    documents_updated: int = 0
    deferred: int = 0
    new_by_source: dict[str, int] = field(default_factory=dict)


def _bson_date(v: datetime | None):
    return v.date() if isinstance(v, datetime) else v


class CrawlIngestor:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session], db):
        self.settings = settings
        self.session_factory = session_factory
        self.db = db

    def run(self, limit: int | None = None) -> IngestReport:
        limit = limit or self.settings.ingest_batch
        rep = IngestReport()
        with self.session_factory() as s:
            self._sources(s, rep)
            self._runs(s, rep, limit)
            self._documents(s, rep, limit)
        if rep.documents_new or rep.runs:
            log.info("ingest: %s", rep)
        return rep

    def _mark_synced(self, coll: str, rows: list[dict]) -> None:
        for r in rows:
            self.db[coll].update_one({"_id": r["_id"], "rev": r.get("rev")}, {"$set": {"synced": True}})

    def _sources(self, s: Session, rep: IngestReport) -> None:
        rows = list(self.db[SOURCES].find({"synced": False}))
        for r in rows:
            row = s.get(Source, r["_id"]) or Source(code=r["_id"])
            row.name, row.enabled, row.config = r["name"], r.get("enabled", True), r.get("config") or {}
            s.add(row)
        s.commit()
        self._mark_synced(SOURCES, rows)
        rep.sources = len(rows)

    def _runs(self, s: Session, rep: IngestReport, limit: int) -> None:
        rows = list(self.db[RUNS].find({"synced": False}, sort=[("started_at", 1)], limit=limit))
        known = self._ids(s, FetchRun, [str(r["_id"]) for r in rows])
        for r in rows:
            row = s.get(FetchRun, known[str(r["_id"])]) if str(r["_id"]) in known else FetchRun(ext_id=str(r["_id"]))
            for f in RUN_FIELDS:
                setattr(row, f, r.get(f))
            s.add(row)
        s.commit()
        self._mark_synced(RUNS, rows)
        rep.runs = len(rows)

    def _documents(self, s: Session, rep: IngestReport, limit: int) -> None:
        # Ana belgeler eklerden önce (ek, parent_id ile ana belgeye bağlanır)
        rows = list(self.db[DOCS].find({"synced": False}, sort=[("parent_id", 1), ("_id", 1)], limit=limit))
        rows.sort(key=lambda r: r.get("parent_id") is not None)
        oids = {str(r["_id"]) for r in rows} | {str(r["parent_id"]) for r in rows if r.get("parent_id")}
        known = self._ids(s, RawDocument, list(oids))
        runs = self._ids(s, FetchRun, list({str(r["fetch_run_id"]) for r in rows if r.get("fetch_run_id")}))
        done: list[dict] = []
        for r in rows:
            oid = str(r["_id"])
            if oid in known:
                row = s.get(RawDocument, known[oid])
                for f in DOC_MUTABLE:
                    setattr(row, f, r.get(f))
                rep.documents_updated += 1
                done.append(r)
                continue
            parent_id = None
            if r.get("parent_id") is not None:
                s.flush()
                parent_id = known.get(str(r["parent_id"]))
                if parent_id is None:
                    rep.deferred += 1
                    continue
            row = RawDocument(ext_id=oid, **{f: r.get(f) for f in DOC_FIELDS},
                              published_at=_bson_date(r.get("published_at")), parent_id=parent_id,
                              fetch_run_id=runs.get(str(r.get("fetch_run_id"))))
            s.add(row)
            s.flush()
            known[oid] = row.id
            rep.documents_new += 1
            rep.new_by_source[row.source_code] = rep.new_by_source.get(row.source_code, 0) + 1
            done.append(r)
        s.commit()
        self._mark_synced(DOCS, done)

    @staticmethod
    def _ids(s: Session, model, ext_ids: list[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in range(0, len(ext_ids), 500):
            out.update(s.execute(select(model.ext_id, model.id).where(model.ext_id.in_(ext_ids[i:i + 500]))).all())
        return out
