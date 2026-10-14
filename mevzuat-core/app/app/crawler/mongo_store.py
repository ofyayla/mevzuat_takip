"""DMZ crawler'ın kayıt katmanı: meta veri Mongo koleksiyonlarına, ham içerik GridFS'e (bkz. app.mongo)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Iterator

from bson import ObjectId

from app.collectors.config import SourceConfig
from app.collectors.store import CHANNEL_STATS_MAX_RUNS, MainDocInfo, NewDoc, StoredDoc, merge_channel_stats
from app.mongo import DOCS, RUNS, SOURCES, utcnow
from app.storage import GridFsStorage

_DIRTY = {"$inc": {"rev": 1}}   # her yazım: rev++ ve synced=False (ana servis yeniden aktarır)


def _date_to_bson(d: date | None) -> datetime | None:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) if d else None   # BSON'da yalın tarih tipi yok


def _set(fields: dict) -> dict:
    return {"$set": {**fields, "synced": False, "updated_at": utcnow()}, **_DIRTY}


class MongoCrawlStore:
    def __init__(self, db):
        self.db = db
        self.storage = GridFsStorage(db)

    def put_content(self, content: bytes) -> tuple[str, str]:
        return self.storage.put(content)

    def read_content(self, key: str) -> bytes:
        return self.storage.get(key)

    def iter_main_documents(self, code: str, channels: list[str] | None = None,
                            batch: int = 200) -> Iterator[MainDocInfo]:
        """Kaynağın son sürüm ana belgeleri (_id'ye göre sayfalı). Onarım komutu kullanır."""
        last = None
        while True:
            q: dict = {"source_code": code, "is_latest": True, "role": {"$ne": "attachment"}}
            if channels:
                q["channel"] = {"$in": channels}
            if last is not None:
                q["_id"] = {"$gt": last}
            rows = list(self.db[DOCS].find(q, sort=[("_id", 1)], limit=batch))
            if not rows:
                return
            kids: dict = defaultdict(set)
            for k in self.db[DOCS].find({"parent_id": {"$in": [r["_id"] for r in rows]}, "is_latest": True},
                                        {"parent_id": 1, "url": 1}):
                kids[k["parent_id"]].add(k["url"])
            last = rows[-1]["_id"]
            for r in rows:
                pub = r.get("published_at")
                yield MainDocInfo(
                    StoredDoc(r["_id"], r["external_id"], r["version"], r.get("listing_hash"), r["content_sha256"],
                              r["fetched_at"], r.get("is_baseline", False), r.get("extra") or {}, r["channel"], r["url"]),
                    r["channel"], r["url"], r.get("final_url"), r["content_type"], r["storage_key"], r.get("title"),
                    pub.date() if isinstance(pub, datetime) else pub, r.get("category"), kids[r["_id"]])

    def upsert_source(self, source: SourceConfig) -> None:
        self.db[SOURCES].update_one({"_id": source.code}, _set({
            "name": source.name, "enabled": source.enabled, "config": source.model_dump(mode="json")}), upsert=True)

    def last_success(self, code: str) -> datetime | None:
        row = self.db[RUNS].find_one({"source_code": code, "status": "success"}, {"started_at": 1},
                                     sort=[("started_at", -1)])
        return row["started_at"] if row else None

    def previous_channel_stats(self, code: str, channels: list[str] | None = None) -> dict:
        rows = self.db[RUNS].find({"source_code": code, "status": {"$in": ["success", "partial"]}},
                                  {"channels": 1}, sort=[("started_at", -1)], limit=CHANNEL_STATS_MAX_RUNS,
                                  batch_size=50)
        try:
            return merge_channel_stats((r.get("channels") for r in rows), channels)
        finally:
            rows.close()   # merge erken çıkınca sunucudaki imleç açık kalmasın

    def start_run(self, code: str, started_at: datetime):
        doc = {"source_code": code, "started_at": started_at, "finished_at": None, "status": "running",
               "items_listed": 0, "items_new": 0, "items_changed": 0, "requests": 0, "channels": {}, "errors": [],
               "structure_alert": False, "synced": False, "rev": 1, "updated_at": utcnow()}
        return self.db[RUNS].insert_one(doc).inserted_id

    def finish_run(self, run_id, **fields) -> None:
        self.db[RUNS].update_one({"_id": run_id}, _set(fields))

    def latest_documents(self, code: str, external_ids: list[str]) -> dict[str, StoredDoc]:
        out: dict[str, StoredDoc] = {}
        for i in range(0, len(external_ids), 500):
            # sürüme göre artan: yarıda kalmış bir yazımda iki "latest" olsa bile büyük sürüm kazanır
            rows = self.db[DOCS].find({"source_code": code, "external_id": {"$in": external_ids[i:i + 500]},
                                       "is_latest": True, "role": {"$ne": "attachment"}},
                                      sort=[("version", 1)])
            for r in rows:
                out[r["external_id"]] = StoredDoc(r["_id"], r["external_id"], r["version"], r.get("listing_hash"),
                                                  r["content_sha256"], r["fetched_at"], r.get("is_baseline", False),
                                                  r.get("extra") or {}, r.get("channel", ""), r.get("url", ""))
        return out

    def touch_unchanged(self, prev: StoredDoc, *, listing_hash, version_key, title, checked_at) -> None:
        self.db[DOCS].update_one({"_id": prev.id}, _set({
            "listing_hash": listing_hash, "version_key": version_key, "title": title,
            "extra.checked_at": checked_at.isoformat()}))

    def add_version(self, prev: StoredDoc | None, run_id, main: NewDoc, attachments: list[NewDoc]) -> None:
        """Sıra: ekler → ana belge → eski sürümü kapat. Ana belge işleme noktasıdır (``latest_documents`` yalnızca
        onu görür): yazım ortasında kesilirse ana belge yoktur, sonraki tarama öğeyi yeniden yazar. Tersi sırada
        (ana belge önce) kesinti ekleri kalıcı kaybettirirdi, çünkü ana belge "değişmedi" sayılırdı. Ekler anahtar
        üzerinden upsert edilir: yarım kalmış önceki denemenin kalıntısı aynı kayıtla değiştirilir. Ana belgesi
        henüz olmayan ekleri ingest erteler (``IngestReport.deferred``)."""
        parent_id = ObjectId()
        for att in attachments:
            key = {"source_code": att.source_code, "external_id": att.external_id, "version": att.version}
            self.db[DOCS].replace_one(key, self._doc(att, run_id, parent_id), upsert=True)
        self.db[DOCS].insert_one({**self._doc(main, run_id), "_id": parent_id})
        if prev is not None:
            self.db[DOCS].update_one({"_id": prev.id}, _set({"is_latest": False}))
            self.db[DOCS].update_many({"parent_id": prev.id, "is_latest": True}, _set({"is_latest": False}))

    def add_late_attachments(self, prev: StoredDoc, run_id, attachments: list[NewDoc], missing: list[dict]) -> None:
        """Sonradan alınan ekler (sıra: ekler → ana belgenin ``extra.missing_attachments`` güncellemesi). Ekler anahtar
        üzerinden upsert edilir; yarım kalan denemenin tekrarı çift kayıt üretmez."""
        for att in attachments:
            key = {"source_code": att.source_code, "external_id": att.external_id, "version": att.version}
            self.db[DOCS].replace_one(key, self._doc(att, run_id, prev.id), upsert=True)
        self.db[DOCS].update_one({"_id": prev.id}, _set({"extra.missing_attachments": missing}))

    @staticmethod
    def _doc(d: NewDoc, run_id, parent_id=None) -> dict:
        return {**d.__dict__, "published_at": _date_to_bson(d.published_at), "is_latest": True,
                "parent_id": parent_id, "fetch_run_id": run_id, "synced": False, "rev": 1, "updated_at": utcnow()}
