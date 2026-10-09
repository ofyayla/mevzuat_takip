"""Toplayıcının kayıt katmanı. ``SourceCollector`` yalnızca bu arayüzü bilir; nereye yazıldığı kuruluma göre değişir:

- ``SqlCrawlStore``: PostgreSQL/SQLite + dosya arşivi. Yerel geliştirme, testler ve tek kutu kurulum
  (``CRAWL_MODE=local``).
- ``MongoCrawlStore`` (app.crawler.mongo_store): DMZ'deki crawler. DMZ'den LAN'a yalnızca MongoDB (27017) açık
  olduğu için meta veri Mongo koleksiyonlarına, ham içerik GridFS'e yazılır; LAN'daki ana servis bunları ingest eder
  (``CRAWL_MODE=remote``).

İki uygulama da aynı kuralları korur: ham içerik silinmez, içerik değişince yeni sürüm açılır, eski sürüm
``is_latest=False`` kalır.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.collectors.config import SourceConfig
from app.db import FetchRun, RawDocument, Source, latest_documents


@dataclass
class StoredDoc:
    """Değişiklik tespiti için gereken son sürüm bilgisi."""

    id: Any
    external_id: str
    version: int
    listing_hash: str | None
    content_sha256: str
    fetched_at: datetime
    is_baseline: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class NewDoc:
    source_code: str
    channel: str
    external_id: str
    version: int
    role: str
    url: str
    final_url: str | None
    title: str | None
    published_at: date | None
    category: str | None
    listing_hash: str | None
    version_key: str | None
    content_type: str
    content_sha256: str
    size_bytes: int
    storage_key: str
    fetched_at: datetime
    is_baseline: bool = False
    extra: dict = field(default_factory=dict)


class CrawlStore(Protocol):
    def put_content(self, content: bytes) -> tuple[str, str]: ...
    def upsert_source(self, source: SourceConfig) -> None: ...
    def last_success(self, code: str) -> datetime | None: ...
    def previous_channel_stats(self, code: str) -> dict: ...
    def start_run(self, code: str, started_at: datetime) -> Any: ...
    def finish_run(self, run_id: Any, **fields) -> None: ...
    def latest_documents(self, code: str, external_ids: list[str]) -> dict[str, StoredDoc]: ...
    def touch_unchanged(self, prev: StoredDoc, *, listing_hash: str | None, version_key: str | None,
                        title: str | None, checked_at: datetime) -> None: ...
    def add_version(self, prev: StoredDoc | None, run_id: Any, main: NewDoc,
                    attachments: list[NewDoc]) -> None: ...


def aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt   # SQLite tz saklamaz


def merge_channel_stats(rows) -> dict:
    """Her kanal için en son kaydedilen istatistik (yalnızca bazı kanalların tarandığı çalıştırmalar olabilir)."""
    merged: dict = {}
    for channels in rows:
        for name, stats in (channels or {}).items():
            merged.setdefault(name, stats)
    return merged


class SqlCrawlStore:
    def __init__(self, session_factory: sessionmaker[Session], storage):
        self.session_factory = session_factory
        self.storage = storage

    def put_content(self, content: bytes) -> tuple[str, str]:
        return self.storage.put(content)

    def upsert_source(self, source: SourceConfig) -> None:
        cfg = source.model_dump(mode="json")
        with self.session_factory() as s:
            row = s.get(Source, source.code)
            if row is None:
                s.add(Source(code=source.code, name=source.name, enabled=source.enabled, config=cfg))
            else:
                row.name, row.enabled, row.config = source.name, source.enabled, cfg
            s.commit()

    def last_success(self, code: str) -> datetime | None:
        with self.session_factory() as s:
            return aware(s.scalar(select(FetchRun.started_at).where(
                FetchRun.source_code == code, FetchRun.status == "success"
            ).order_by(FetchRun.started_at.desc()).limit(1)))

    def previous_channel_stats(self, code: str) -> dict:
        with self.session_factory() as s:
            return merge_channel_stats(s.scalars(select(FetchRun.channels).where(
                FetchRun.source_code == code, FetchRun.status.in_(("success", "partial"))
            ).order_by(FetchRun.started_at.desc()).limit(50)))

    def start_run(self, code: str, started_at: datetime) -> int:
        with self.session_factory() as s:
            run = FetchRun(source_code=code, started_at=started_at)
            s.add(run)
            s.commit()
            return run.id

    def finish_run(self, run_id: int, **fields) -> None:
        with self.session_factory() as s:
            run = s.get(FetchRun, run_id)
            for k, v in fields.items():
                setattr(run, k, v)
            s.commit()

    def latest_documents(self, code: str, external_ids: list[str]) -> dict[str, StoredDoc]:
        with self.session_factory() as s:
            return {k: StoredDoc(r.id, r.external_id, r.version, r.listing_hash, r.content_sha256,
                                 aware(r.fetched_at), r.is_baseline, dict(r.extra or {}))
                    for k, r in latest_documents(s, code, external_ids).items()}

    def touch_unchanged(self, prev: StoredDoc, *, listing_hash, version_key, title, checked_at) -> None:
        with self.session_factory() as s:
            row = s.get(RawDocument, prev.id)
            row.listing_hash, row.version_key, row.title = listing_hash, version_key, title
            row.extra = {**(row.extra or {}), "checked_at": checked_at.isoformat()}  # JSON alanı yeniden atanmalı
            s.commit()

    def add_version(self, prev: StoredDoc | None, run_id: int, main: NewDoc, attachments: list[NewDoc]) -> None:
        with self.session_factory() as s:
            if prev is not None:
                s.get(RawDocument, prev.id).is_latest = False
            parent = RawDocument(**main.__dict__, is_latest=True, fetch_run_id=run_id)
            s.add(parent)
            s.flush()
            for att in attachments:
                s.add(RawDocument(**att.__dict__, is_latest=True, fetch_run_id=run_id, parent_id=parent.id))
            s.commit()
