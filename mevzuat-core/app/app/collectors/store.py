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

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterator, Protocol

from sqlalchemy import select, update
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
    channel: str = ""    # kimlik çakışması denetimi: aynı external_id başka kanalın farklı belgesi olabilir
    url: str = ""


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


@dataclass
class MainDocInfo:
    """Onarım komutu için: son sürüm ana belge + saklı içeriğin yeri + zaten bağlı eklerin adresleri."""

    doc: StoredDoc
    channel: str
    url: str
    final_url: str | None
    content_type: str
    storage_key: str
    title: str | None
    published_at: date | None
    category: str | None
    attachment_urls: set[str]


class CrawlStore(Protocol):
    def put_content(self, content: bytes) -> tuple[str, str]: ...
    def read_content(self, key: str) -> bytes: ...
    def iter_main_documents(self, code: str, channels: list[str] | None = None,
                            batch: int = 200) -> Iterator[MainDocInfo]: ...
    def upsert_source(self, source: SourceConfig) -> None: ...
    def last_success(self, code: str) -> datetime | None: ...
    def previous_channel_stats(self, code: str, channels: list[str] | None = None) -> dict: ...
    def start_run(self, code: str, started_at: datetime) -> Any: ...
    def finish_run(self, run_id: Any, **fields) -> None: ...
    def latest_documents(self, code: str, external_ids: list[str]) -> dict[str, StoredDoc]: ...
    def touch_unchanged(self, prev: StoredDoc, *, listing_hash: str | None, version_key: str | None,
                        title: str | None, checked_at: datetime) -> None: ...
    def add_version(self, prev: StoredDoc | None, run_id: Any, main: NewDoc,
                    attachments: list[NewDoc]) -> None: ...
    def add_late_attachments(self, prev: StoredDoc, run_id: Any, attachments: list[NewDoc],
                             missing: list[dict]) -> None: ...


def aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt   # SQLite tz saklamaz


CHANNEL_STATS_MAX_RUNS = 1000   # uzun süre görülmeyen kanal için geriye bakılacak en fazla tarama
SIGNATURE_LOOKBACK_RUNS = 50    # boş imzalı (hatalı/boş liste) kanal için geçerli imza aranacak tarama sayısı


def merge_channel_stats(rows, wanted=None) -> dict:
    """Her kanal için en son kaydedilen istatistik (yalnızca bazı kanalların tarandığı çalıştırmalar olabilir).

    ``rows`` yeniden eskiye sıralı ``channels`` sözlükleridir. ``signature`` boşsa (liste hatası, boş liste) boş
    olmayan en son imza kullanılır; yoksa hatadan sonraki taramada yapı karşılaştırması atlanırdı. ``wanted``
    verilirse istenen kanalların hepsi bulununca (ve imzaları dolunca ya da imza penceresi geçilince) okuma durur.
    """
    merged: dict = {}
    wanted = set(wanted) if wanted is not None else None
    for n, channels in enumerate(rows, 1):
        for name, stats in (channels or {}).items():
            cur = merged.setdefault(name, dict(stats))
            if not cur.get("signature") and stats.get("signature") and n <= SIGNATURE_LOOKBACK_RUNS:
                cur["signature"] = stats["signature"]
        if wanted is not None and wanted <= merged.keys() and (
                n >= SIGNATURE_LOOKBACK_RUNS or all(merged[w].get("signature") for w in wanted)):
            break
    return merged


class SqlCrawlStore:
    def __init__(self, session_factory: sessionmaker[Session], storage):
        self.session_factory = session_factory
        self.storage = storage

    def put_content(self, content: bytes) -> tuple[str, str]:
        return self.storage.put(content)

    def read_content(self, key: str) -> bytes:
        return self.storage.get(key)

    def iter_main_documents(self, code: str, channels: list[str] | None = None,
                            batch: int = 200) -> Iterator[MainDocInfo]:
        """Kaynağın son sürüm ana belgeleri (kimliğe göre sayfalı). Onarım komutu kullanır."""
        last = 0
        while True:
            with self.session_factory() as s:
                q = select(RawDocument).where(
                    RawDocument.source_code == code, RawDocument.is_latest.is_(True),
                    RawDocument.role != "attachment", RawDocument.id > last).order_by(RawDocument.id).limit(batch)
                if channels:
                    q = q.where(RawDocument.channel.in_(channels))
                rows = s.scalars(q).all()
                if not rows:
                    return
                kids: dict[int, set[str]] = defaultdict(set)
                for pid, url in s.execute(select(RawDocument.parent_id, RawDocument.url).where(
                        RawDocument.parent_id.in_([r.id for r in rows]), RawDocument.is_latest.is_(True))):
                    kids[pid].add(url)
                infos = [MainDocInfo(
                    StoredDoc(r.id, r.external_id, r.version, r.listing_hash, r.content_sha256, aware(r.fetched_at),
                              r.is_baseline, dict(r.extra or {}), r.channel, r.url),
                    r.channel, r.url, r.final_url, r.content_type, r.storage_key, r.title, r.published_at,
                    r.category, kids[r.id]) for r in rows]
            last = rows[-1].id
            yield from infos

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

    def previous_channel_stats(self, code: str, channels: list[str] | None = None) -> dict:
        with self.session_factory() as s:
            # yield_per: satırlar parça parça okunur, merge erken çıkınca kalanı çekilmez
            return merge_channel_stats(s.scalars(select(FetchRun.channels).where(
                FetchRun.source_code == code, FetchRun.status.in_(("success", "partial"))
            ).order_by(FetchRun.started_at.desc()).limit(CHANNEL_STATS_MAX_RUNS).execution_options(yield_per=50)),
                channels)

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
                                 aware(r.fetched_at), r.is_baseline, dict(r.extra or {}), r.channel, r.url)
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
                # eski sürümün ekleri de eskidir (aynı ek kimliği yeni sürümde yeniden eklenir)
                s.execute(update(RawDocument).where(RawDocument.parent_id == prev.id).values(is_latest=False))
            parent = RawDocument(**main.__dict__, is_latest=True, fetch_run_id=run_id)
            s.add(parent)
            s.flush()
            for att in attachments:
                s.add(RawDocument(**att.__dict__, is_latest=True, fetch_run_id=run_id, parent_id=parent.id))
            s.commit()

    def add_late_attachments(self, prev: StoredDoc, run_id: int, attachments: list[NewDoc],
                             missing: list[dict]) -> None:
        """Daha önce indirilemeyen ekler sonradan alındı: aynı sürümün ekleri olarak eklenir ve ana belgenin
        ``extra.missing_attachments`` listesi güncellenir (ana belge yeni sürüm açmaz)."""
        with self.session_factory() as s:
            have = set(s.scalars(select(RawDocument.external_id).where(RawDocument.parent_id == prev.id)))
            for att in attachments:
                if att.external_id not in have:
                    s.add(RawDocument(**att.__dict__, is_latest=True, fetch_run_id=run_id, parent_id=prev.id))
            row = s.get(RawDocument, prev.id)
            row.extra = {**(row.extra or {}), "missing_attachments": missing}   # JSON alanı yeniden atanmalı
            s.commit()
