"""DMZ ↔ LAN teslim alanı: MongoDB (``mevzuat_crawl`` veritabanı).

DMZ'deki crawler yazar, LAN'daki ana servis okur. Koleksiyonlar:

| Koleksiyon       | Yazan    | İçerik                                                                          |
|------------------|----------|---------------------------------------------------------------------------------|
| sources          | crawler  | kaynak yapılandırmasının anlık görüntüsü                                        |
| fetch_runs       | crawler  | tarama çalıştırmaları (PostgreSQL ``fetch_run`` ile aynı alanlar)               |
| raw_documents    | crawler  | ham belge meta verisi (PostgreSQL ``raw_document`` toplama alanları)            |
| raw.files/chunks | crawler  | GridFS: ham içerik, anahtar ``raw/ab/<sha256>``                                 |
| crawl_requests   | ana srv. | LAN'dan istenen tarama/backfill (portal "şimdi tara", ``mevzuat-monitor``)      |
| locks            | crawler  | kaynak başına tarama kilidi                                                     |
| crawler_status   | crawler  | crawler canlılık sinyali (LAN izlemesi "crawler_down" alarmı üretir)            |

Senkron işareti: crawler her yazımda ``synced=False`` yapar ve ``rev``'i artırır. Ana servis kaydı PostgreSQL'e
aktarınca ``synced=True`` işaretini yalnızca ``rev`` değişmemişse koyar; arada güncellenen kayıt bir sonraki turda
yeniden aktarılır (idempotent upsert, ``ext_id`` = Mongo ``_id``).
"""
from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

SOURCES, RUNS, DOCS, REQUESTS, LOCKS, STATUS = (
    "sources", "fetch_runs", "raw_documents", "crawl_requests", "locks", "crawler_status")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache
def _client(url: str, timeout_ms: int):
    from pymongo import MongoClient

    return MongoClient(url, tz_aware=True, serverSelectionTimeoutMS=timeout_ms, appname="mevzuat-takip")


def crawl_db(settings):
    if not settings.mongo_url:
        raise RuntimeError("MONGO_URL tanımlı değil (CRAWL_MODE=remote / RAW_STORAGE=gridfs için gerekli)")
    return _client(settings.mongo_url, settings.mongo_timeout_ms)[settings.mongo_db]


def ensure_indexes(db) -> None:
    """Crawler açılışta çağırır (idempotent)."""
    from pymongo import ASCENDING, DESCENDING

    docs = db[DOCS]
    docs.create_index([("source_code", ASCENDING), ("external_id", ASCENDING), ("version", ASCENDING)],
                      unique=True, name="uq_doc_version")
    docs.create_index([("source_code", ASCENDING), ("external_id", ASCENDING), ("is_latest", ASCENDING)],
                      name="ix_doc_latest")
    for coll in (DOCS, RUNS, SOURCES):
        db[coll].create_index([("synced", ASCENDING)], name="ix_unsynced",
                              partialFilterExpression={"synced": False})
    db[RUNS].create_index([("source_code", ASCENDING), ("started_at", DESCENDING)], name="ix_run_source")
    db[REQUESTS].create_index([("status", ASCENDING), ("created_at", ASCENDING)], name="ix_request_status")
    db["raw.files"].create_index([("filename", ASCENDING), ("uploadDate", ASCENDING)], name="ix_raw_filename")


def worker_id() -> str:
    return socket.gethostname()


# ------------------------------------------------------------------------------------------------ tarama talepleri


def submit_crawl_request(db, kind: str, source_code: str, params: dict | None = None,
                         requested_by: str | None = None) -> str:
    """LAN → crawler: ``kind`` = run | backfill. Crawler bir sonraki yoklamada (≤ CRAWLER_POLL_S) alır."""
    res = db[REQUESTS].insert_one({"kind": kind, "source_code": source_code, "params": params or {},
                                   "requested_by": requested_by, "status": "pending", "created_at": utcnow()})
    return str(res.inserted_id)


def claim_crawl_request(db, owner: str, busy_codes: list[str]) -> dict | None:
    from pymongo import ReturnDocument

    return db[REQUESTS].find_one_and_update(
        {"status": "pending", "source_code": {"$nin": busy_codes}},
        {"$set": {"status": "running", "claimed_by": owner, "started_at": utcnow()}},
        sort=[("created_at", 1)], return_document=ReturnDocument.AFTER)


def finish_crawl_request(db, request_id, *, ok: bool, result: Any = None, error: str | None = None) -> None:
    db[REQUESTS].update_one({"_id": request_id}, {"$set": {
        "status": "done" if ok else "failed", "finished_at": utcnow(), "result": result, "error": error}})


def requeue_orphaned_requests(db, owner: str) -> int:
    """Crawler yeniden başladığında yarıda kalan kendi taleplerini tekrar kuyruğa alır."""
    return db[REQUESTS].update_many({"status": "running", "claimed_by": owner},
                                    {"$set": {"status": "pending"}, "$unset": {"claimed_by": "", "started_at": ""}}
                                    ).modified_count


def get_crawl_request(db, request_id: str) -> dict | None:
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        return db[REQUESTS].find_one({"_id": ObjectId(request_id)})
    except InvalidId:
        return None


# ------------------------------------------------------------------------------------------------ kilit ve canlılık


def acquire_lock(db, name: str, owner: str, ttl_s: int) -> bool:
    """Süresi dolmuş kilit devralınır (crawler çöktüyse kaynak sonsuza dek kilitli kalmaz)."""
    from pymongo.errors import DuplicateKeyError

    now = utcnow()
    try:
        db[LOCKS].update_one({"_id": name, "expires_at": {"$lt": now}},
                             {"$set": {"owner": owner, "acquired_at": now, "expires_at": now + timedelta(seconds=ttl_s)}},
                             upsert=True)
        return True
    except DuplicateKeyError:   # kilit başkasında ve süresi dolmamış
        return False


def release_lock(db, name: str, owner: str) -> None:
    db[LOCKS].delete_one({"_id": name, "owner": owner})


def write_heartbeat(db, owner: str, info: dict) -> None:
    db[STATUS].update_one({"_id": owner}, {"$set": {**info, "at": utcnow()}}, upsert=True)


def last_heartbeat(db) -> dict | None:
    return db[STATUS].find_one(sort=[("at", -1)])
