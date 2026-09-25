"""Veritabanı modelleri: toplama (plan §5.1: source, fetch_run, raw_document) ve tekil düzenleme kaydı
(§5.2: regulation, regulation_key, regulation_source_link — İK-2).

Üretimde PostgreSQL, yerel geliştirmede ve testlerde SQLite kullanılır; tipler ikisiyle de uyumludur.
Alembic migrasyonları sonraki adımda bu modellerden üretilecektir.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
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
    # FETCHED → EXTRACTED → LINKED; EXTRACT_FAILED (yeniden denenir). Bkz. plan §2.2
    processing_status: Mapped[str] = mapped_column(String(24), default="FETCHED", index=True)
    # Kanalın ilk taramasında sitede zaten duran içerik: arşive ve tekilleştirmeye girer, YZ hattına girmez
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    # İK-2: metin çıkarma
    text: Mapped[str | None] = mapped_column(Text)
    extraction_method: Mapped[str | None] = mapped_column(String(24))  # html|pdf_text|ocr|pdf_text+ocr|docx|…
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    page_count: Mapped[int | None] = mapped_column(Integer)
    extract_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    regulation_id: Mapped[int | None] = mapped_column(ForeignKey("regulation.id"), index=True)


class Regulation(Base):
    """Tekil düzenleme kaydı (portaldaki "kayıt"). Aynı düzenlemenin farklı kaynaklardaki yayınları
    ``regulation_source_link`` ile bağlanır; YZ adımları (İK-3..5) düzenleme başına bir kez çalışır."""

    __tablename__ = "regulation"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text)
    issuer: Mapped[str | None] = mapped_column(String(64), index=True)       # kurum kodu (BDDK, SPK …)
    issuer_name: Mapped[str | None] = mapped_column(String(300))
    reg_type: Mapped[str | None] = mapped_column(String(64))                # kural tabanlı ön tahmin; İK-3 kesinleştirir
    is_amendment: Mapped[bool] = mapped_column(Boolean, default=False)
    publish_date: Mapped[date | None] = mapped_column(Date, index=True)
    primary_source_code: Mapped[str] = mapped_column(ForeignKey("source.code"))
    canonical_key: Mapped[str] = mapped_column(String(600))
    # NEW → (İK-3) CLASSIFIED … ; BASELINE: yalnızca ilk taramada görülen eski içerikten oluştu, YZ'ye gitmez
    processing_status: Mapped[str] = mapped_column(String(24), default="NEW", index=True)
    review_status: Mapped[str] = mapped_column(String(16), default="Bekliyor")
    needs_dedupe_review: Mapped[bool] = mapped_column(Boolean, default=False)  # belirsiz bant (0.70–0.90)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class RegulationKey(Base):
    """Kesin eşleştirme anahtarları (ör. ``url:…``, ``karar:BDDK:11572``, ``title:BDDK:…``). Bir anahtar tek
    düzenlemeye aittir; aynı anahtarı üreten yeni belge doğrudan o düzenlemeye bağlanır."""

    __tablename__ = "regulation_key"

    key: Mapped[str] = mapped_column(String(600), primary_key=True)
    regulation_id: Mapped[int] = mapped_column(ForeignKey("regulation.id"), index=True)
    strong: Mapped[bool] = mapped_column(Boolean, default=True)   # False: başlık anahtarı (yalnızca kaynaklar arası)
    raw_document_id: Mapped[int | None] = mapped_column(ForeignKey("raw_document.id"))  # anahtarı üreten belge


class RegulationSourceLink(Base):
    __tablename__ = "regulation_source_link"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(ForeignKey("regulation.id"), index=True)
    raw_document_id: Mapped[int] = mapped_column(ForeignKey("raw_document.id"), unique=True)
    # new | same_document | attachment | exact_key | fuzzy_title | llm_confirmed | manual
    match_method: Mapped[str] = mapped_column(String(24))
    match_score: Mapped[float | None] = mapped_column(Float)
    matched_key: Mapped[str | None] = mapped_column(String(600))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
