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
    event,
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
    review_status: Mapped[str] = mapped_column(String(16), default="Bekliyor", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_by_title: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    row_version: Mapped[int] = mapped_column(Integer, default=1)       # iyimser kilit (If-Match / ETag)
    needs_dedupe_review: Mapped[bool] = mapped_column(Boolean, default=False)  # belirsiz bant (0.70–0.90)
    # İK-3: ilgililik ve önem (YZ)
    is_relevant: Mapped[bool | None] = mapped_column(Boolean, index=True)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    confidence_band: Mapped[str | None] = mapped_column(String(8))     # Yüksek | Orta | Düşük
    severity: Mapped[str | None] = mapped_column(String(8), index=True)  # Kritik | Yüksek | Orta | Düşük
    severity_rationale: Mapped[str | None] = mapped_column(Text)
    ai_reg_type: Mapped[str | None] = mapped_column(String(64))
    classification: Mapped[dict] = mapped_column(JSON, default=dict)   # oylar, bileşenler, gerekçe, kriterler
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # İK-4: yürürlük tarihi (YZ çıkarımı, kaynak alıntısıyla ve deterministik kontrolle doğrulanmış)
    effective_date: Mapped[date | None] = mapped_column(Date, index=True)
    effective_date_text: Mapped[str | None] = mapped_column(Text)
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


class RegulationSummary(Base):
    """İK-4 yapılandırılmış özet (plan §5.2). Sürümlüdür: yeniden üretimde eski sürüm is_current=False kalır."""

    __tablename__ = "regulation_summary"
    __table_args__ = (UniqueConstraint("regulation_id", "version", name="uq_summary_version"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(ForeignKey("regulation.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    short_content: Mapped[str] = mapped_column(Text)               # doğrulanmış cümlelerden oluşan paragraf
    short_content_evidence: Mapped[list] = mapped_column(JSON, default=list)  # [{sentence, evidence:[…]}]
    relevant_topics: Mapped[list] = mapped_column(JSON, default=list)          # [{text, evidence:[…]}]
    effective_date_evidence: Mapped[dict | None] = mapped_column(JSON)
    grounding_score: Mapped[float] = mapped_column(Float)          # doğrulanan ifade oranı (kaldırılanlar dahil)
    unverified_claims: Mapped[list] = mapped_column(JSON, default=list)  # kaldırılan / doğrulanamayan ifadeler
    source_links: Mapped[list] = mapped_column(JSON, default=list)       # [{label, url}]
    method: Mapped[str] = mapped_column(String(16))                 # single | map_reduce
    prompt_version: Mapped[str] = mapped_column(String(64))
    llm_call_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Unit(Base):
    """Birim bilgi tabanı (İK-5): config/units.yaml → `mevzuat-ai units-load`."""

    __tablename__ = "unit"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    group_name: Mapped[str | None] = mapped_column(String(200))
    responsibilities: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    regulatory_areas: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source_doc_ref: Mapped[str | None] = mapped_column(String(300))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UnitSuggestion(Base):
    """Düzenleme → birim önerisi. ``origin``: ai | manual. Başkanlık değiştirirse eski öneriler is_active=False."""

    __tablename__ = "unit_suggestion"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(ForeignKey("regulation.id"), index=True)
    unit_code: Mapped[str] = mapped_column(ForeignKey("unit.code"))
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    matched_responsibility: Mapped[str | None] = mapped_column(Text)   # birimin görev tanımındaki madde
    origin: Mapped[str] = mapped_column(String(8), default="ai")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)       # onay anında dondurulur (İK-6)
    llm_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_call.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FewshotExample(Base):
    """Onay/düzeltmelerden oluşan örnek havuzu (plan §5.3). Model eğitimi yapılmadan isabeti artırır."""

    __tablename__ = "fewshot_example"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    task: Mapped[str] = mapped_column(String(32), index=True)          # unit_match | relevance
    regulation_id: Mapped[int | None] = mapped_column(ForeignKey("regulation.id"))
    input_text: Mapped[str] = mapped_column(Text)                      # başlık + konular + kısa içerik
    expected_output: Mapped[dict] = mapped_column(JSON)
    origin: Mapped[str] = mapped_column(String(24))                    # workshop | user_decision | user_correction
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    """Denetim izi — yalnızca ekleme (plan §5.2). ORM düzeyinde güncelleme/silme engellenir; üretimde ayrıca DB
    yetkisiyle (UPDATE/DELETE izni verilmez) korunur."""

    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(ForeignKey("regulation.id"), index=True)
    # detected | viewed | approved | rejected | units_changed | summary_regenerated | reprocessed
    event_type: Mapped[str] = mapped_column(String(32))
    actor_type: Mapped[str] = mapped_column(String(8))            # system | user
    actor_id: Mapped[str | None] = mapped_column(String(200))
    actor_name: Mapped[str | None] = mapped_column(String(200))
    actor_title: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str | None] = mapped_column(Text)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Alert(Base):
    """İK-7 alarmları (plan §5.3). ``key`` ile tekilleşir: koşul sürdükçe aynı alarm güncellenir (last_seen_at), koşul
    ortadan kalkınca kapanır (resolved_at). Tekrar oluşursa yeni kayıt açılır."""

    __tablename__ = "alert"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(200), index=True)       # ör. silence:SPK, cross_check_miss:reg:42
    source_code: Mapped[str | None] = mapped_column(String(32), index=True)
    # silence | volume_low | volume_high | structure_change | fetch_error | cross_check_miss | ai_failure
    alert_type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))                # down | delayed | warning
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Outbox(Base):
    """Domain olayları (record.approved / record.rejected). Faz 1'de dinleyen yok; Faz 2 Outlook bildirimi okuyacak."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    aggregate_id: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@event.listens_for(AuditEvent, "before_update")
@event.listens_for(AuditEvent, "before_delete")
def _audit_is_append_only(mapper, connection, target):
    raise PermissionError("audit_event yalnızca eklemeye açıktır")


class LlmCall(Base):
    """Her LLM çağrısının izi (plan §5.3): maliyet, hata ayıklama, denetim ve önbellek (input_hash)."""

    __tablename__ = "llm_call"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    task: Mapped[str] = mapped_column(String(32), index=True)   # relevance | severity | dedupe_confirm | …
    provider: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    regulation_id: Mapped[int | None] = mapped_column(ForeignKey("regulation.id"), index=True)
    request: Mapped[dict | None] = mapped_column(JSON)
    response: Mapped[dict | None] = mapped_column(JSON)          # {"outputs": [...], "raw": [...]}
    reasoning: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))              # ok | invalid | error
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    """Çalışma zamanında değiştirilebilen eşikler (paralel çalışmada kod dağıtımı gerekmeden ayarlanır)."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


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
