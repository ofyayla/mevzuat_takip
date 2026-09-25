"""Toplama katmanının veritabanı modelleri (plan §5.1: source, fetch_run, raw_document).

Üretimde PostgreSQL, yerel geliştirmede ve testlerde SQLite kullanılır; tipler ikisiyle de uyumludur.
Alembic migrasyonları sonraki adımda bu modellerden üretilecektir.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

BigId = BigInteger().with_variant(Integer, "sqlite")  # SQLite'ta autoincrement için INTEGER gerekir


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "source"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FetchRun(Base):
    __tablename__ = "fetch_run"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    source_code: Mapped[str] = mapped_column(ForeignKey("source.code"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")   # running | success | partial | failed
    items_listed: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_changed: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    # kanal bazlı: {kanal: {items, pages, signature, structure_changed, empty_listing, error, warnings}}
    channels: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    structure_alert: Mapped[bool] = mapped_column(Boolean, default=False)


class RawDocument(Base):
    """Arşivlenmiş ham içerik. Asla silinmez; içerik değişirse yeni sürüm eklenir."""

    __tablename__ = "raw_document"
    __table_args__ = (UniqueConstraint("source_code", "external_id", "version", name="uq_raw_doc_version"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    source_code: Mapped[str] = mapped_column(ForeignKey("source.code"), index=True)
    channel: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(512), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("raw_document.id"))
    role: Mapped[str] = mapped_column(String(16), default="main")   # main | attachment | listing
    url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[date | None] = mapped_column(Date, index=True)
    category: Mapped[str | None] = mapped_column(String(200))
    listing_hash: Mapped[str | None] = mapped_column(String(40))
    version_key: Mapped[str | None] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(100))
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(200))
    fetch_run_id: Mapped[int | None] = mapped_column(ForeignKey("fetch_run.id"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processing_status: Mapped[str] = mapped_column(String(24), default="FETCHED")  # İK-2 bunu ilerletir
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


def make_sessionmaker(database_url: str) -> sessionmaker[Session]:
    if database_url.startswith("sqlite:///"):
        from pathlib import Path

        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def latest_documents(session: Session, source_code: str, external_ids: list[str]) -> dict[str, RawDocument]:
    if not external_ids:
        return {}
    out: dict[str, RawDocument] = {}
    for chunk_start in range(0, len(external_ids), 500):
        chunk = external_ids[chunk_start:chunk_start + 500]
        rows = session.scalars(select(RawDocument).where(
            RawDocument.source_code == source_code, RawDocument.external_id.in_(chunk),
            RawDocument.is_latest.is_(True), RawDocument.role != "attachment"))
        out.update({r.external_id: r for r in rows})
    return out
