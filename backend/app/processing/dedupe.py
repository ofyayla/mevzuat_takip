"""Tekilleştirme (İK-2): ham belgeyi tekil düzenleme kaydına (``regulation``) bağlar.

En tipik vaka aynı düzenlemenin Resmî Gazete'de ve kurum sitesinde ayrı ayrı yayımlanmasıdır. Sıra:

1. **Aynı belge:** aynı kaynak kimliğinin önceki sürümü bağlıysa aynı düzenleme; ek ise ana belgesinin düzenlemesi.
2. **Güçlü anahtar** (kesin): kanonik URL, mevzuat.gov.tr kimliği, ``karar:<kurum>:<sayı>``,
   ``no:<kurum>:<tür>:<sıra no>``. Örn. BDDK "(18.09.2026 - 11572) …" = RG "…18/9/2026 Tarihli ve 11572 Sayılı Kararı".
3. **Zayıf anahtar** (normalize başlık + kurum) ve **bulanık skor** yalnızca *farklı kaynaklar* arasında geçerlidir:
   aynı kaynak her ay aynı başlıklı duyuru yayımlayabilir ("Dolandırıcılık Hakkında Basın Duyurusu").
4. Bulanık skor = 0.6·başlık (token_set) + 0.3·metnin ilk 2000 karakteri + 0.1·kurum uyumu; aday: yayım tarihi
   ±7 gün. ≥0.90 otomatik birleştir; 0.70–0.90 ``confirmer`` (LLM, İK-3 ile gelecek) varsa ona sorulur, yoksa yeni
   kayıt açılır ve ``needs_dedupe_review`` işaretlenir; <0.70 yeni düzenleme.
5. Mevcut düzenlemeye yeni kaynak eklenirse YZ adımları yeniden çalıştırılmaz (yalnızca bağlantı eklenir).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlsplit

from rapidfuzz import fuzz
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.collectors.dates import tr_lower
from app.db import RawDocument, Regulation, RegulationKey, RegulationSourceLink, utcnow
from app.processing.normalize import (
    ISSUER_PARENT,
    ISSUERS,
    TitleFacts,
    detect_issuer,
    detect_rg_issue,
    detect_rg_reference,
    issuers_compatible,
    match_issuer,
    parse_title,
    title_key,
)
from app.settings import Settings

log = logging.getLogger(__name__)

# Başlık anahtarı üretilmeyecek kadar genel başlıklar
GENERIC_TITLE_KEYS = {"pdf", "indir", "tıklayınız", "basın duyurusu", "basın açıklaması", "duyuru", "genel bilgi",
                      "kamuoyu duyurusu", "dokuman linki"}
MIN_TITLE_KEY_LEN = 20


@dataclass
class DocFacts:
    raw_id: int
    source_code: str
    title: TitleFacts
    issuer: str | None
    issuer_name: str | None
    doc_date: date
    text: str
    strong_keys: list[str] = field(default_factory=list)
    weak_keys: list[str] = field(default_factory=list)
    rg_issue: str | None = None
    rg_date: str | None = None

    @property
    def official_date(self) -> date:
        """Resmî yayım tarihi: RG'de yayımlandıysa RG tarihi, değilse kaynağın yayım tarihi."""
        return date.fromisoformat(self.rg_date) if self.rg_date else self.doc_date


@dataclass
class LinkResult:
    regulation_id: int
    method: str
    score: float | None = None
    created: bool = False
    matched_key: str | None = None


# confirmer(doc, regulation, score) -> True/False (karar) | None (karar verilemedi)
Confirmer = Callable[[DocFacts, Regulation, float], "bool | None"]


def canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}" + (f"?{p.query}" if p.query else "")


def doc_facts(raw: RawDocument) -> DocFacts:
    extra = raw.extra or {}
    tf = parse_title(raw.title or "", raw.category)
    # Kaynağın ayrı alan olarak verdiği karar sayısı başlıktan çıkarılandan önceliklidir
    if not tf.decision_no and extra.get("karar_sayisi"):
        tf.decision_no = str(extra["karar_sayisi"])
    text = raw.text or ""
    issuer, issuer_name = None, None
    if raw.source_code == "RESMI_GAZETE":
        issuer, issuer_name = detect_issuer(text)
        title_issuer = match_issuer(raw.title or "") or match_issuer(extra.get("ilan_veren") or "")
        if issuer is None or (title_issuer and ISSUER_PARENT.get(title_issuer) == issuer):
            issuer = title_issuer or issuer     # "Mali Suçları Araştırma Kurulu Genel Tebliği" → MASAK (HMB değil)
            issuer_name = None if title_issuer else issuer_name
    elif raw.external_id.startswith("mevzuat.gov.tr:"):
        issuer = match_issuer(raw.title or "") or detect_issuer(text)[0] or raw.source_code
    else:
        issuer = raw.source_code
    if issuer and not issuer_name:
        issuer_name = ISSUERS.get(issuer, (issuer,))[0]
    doc_date = raw.published_at or raw.fetched_at.date()
    rg_date, rg_issue = detect_rg_reference(text)
    if raw.source_code == "RESMI_GAZETE":
        rg_date, rg_issue = raw.published_at.isoformat() if raw.published_at else None, detect_rg_issue(text)
    f = DocFacts(raw.id, raw.source_code, tf, issuer, issuer_name, doc_date, text, rg_issue=rg_issue,
                 rg_date=rg_date)

    # Liste satırları da (ör. KVKK kurul kararı → RG PDF'i) hedef belgenin URL'sini taşır
    f.strong_keys.append("url:" + canonical_url(raw.url))
    if raw.external_id.startswith("mevzuat.gov.tr:"):
        f.strong_keys.append(raw.external_id)
    if issuer and tf.decision_no:
        f.strong_keys.append(f"karar:{issuer}:{tf.decision_no.lower()}")
    if issuer and tf.seq_no and tf.reg_type:
        f.strong_keys.append(f"no:{issuer}:{tr_lower(tf.reg_type)}:{tf.seq_no.lower()}")
    if len(tf.key) >= MIN_TITLE_KEY_LEN and tf.key not in GENERIC_TITLE_KEYS:
        # bağlı kuruluşun anahtarı üst kurum koduyla üretilir: RG'deki "HMB" ile MASAK sitesindeki kopya eşleşsin
        f.weak_keys.append(f"title:{ISSUER_PARENT.get(issuer, issuer) or '?'}:{tf.key}")
    return f


# ------------------------------------------------------------------------------------------------ bağlama


class Linker:
    def __init__(self, session: Session, settings: Settings, confirmer: Confirmer | None = None):
        self.s = session
        self.settings = settings
        self.confirmer = confirmer

    def link(self, raw: RawDocument) -> LinkResult:
        if raw.role == "attachment":
            parent = self.s.get(RawDocument, raw.parent_id) if raw.parent_id else None
            if parent is None or parent.regulation_id is None:
                raise LookupError("ana belge henüz bağlanmadı")
            return self._attach(raw, parent.regulation_id, "attachment")

        # 1) aynı kaynak kimliğinin önceki sürümü
        prev = self.s.scalar(select(RawDocument).where(
            RawDocument.source_code == raw.source_code, RawDocument.external_id == raw.external_id,
            RawDocument.id != raw.id, RawDocument.regulation_id.is_not(None)
        ).order_by(RawDocument.version.desc()).limit(1))
        facts = doc_facts(raw)
        if prev is not None:
            reg = self.s.get(Regulation, prev.regulation_id)
            reg.extra = {**(reg.extra or {}), "content_updates": [
                *(reg.extra or {}).get("content_updates", []),
                {"raw_document_id": raw.id, "version": raw.version, "at": utcnow().isoformat()}]}
            res = self._attach(raw, reg.id, "same_document")
            self._register_keys(facts, reg.id)
            return res

        # 2) güçlü anahtar, 3) zayıf anahtar (yalnızca kaynaklar arası)
        if hit := self._key_match(facts):
            reg_id, key = hit
            res = self._attach(raw, reg_id, "exact_key", 1.0, key)
            self._enrich(self.s.get(Regulation, reg_id), facts)
            self._register_keys(facts, reg_id)
            return res

        # 4) bulanık eşleştirme
        best, best_score = self._fuzzy(facts)
        if best is not None and best_score >= self.settings.dedupe_auto_threshold:
            res = self._attach(raw, best.id, "fuzzy_title", best_score)
            self._enrich(best, facts)
            self._register_keys(facts, best.id)
            return res
        pending = None
        if best is not None and best_score >= self.settings.dedupe_review_threshold:
            verdict = self.confirmer(facts, best, best_score) if self.confirmer else None
            if verdict:
                res = self._attach(raw, best.id, "llm_confirmed", best_score)
                self._enrich(best, facts)
                self._register_keys(facts, best.id)
                return res
            if verdict is None:
                pending = {"regulation_id": best.id, "score": round(best_score, 4)}

        reg = self._create(raw, facts, pending)
        res = self._attach(raw, reg.id, "new", created=True)
        self._register_keys(facts, reg.id)
        return res

    # ------------------------------------------------------------------ yardımcılar

    def _attach(self, raw: RawDocument, reg_id: int, method: str, score: float | None = None,
                key: str | None = None, *, created: bool = False) -> LinkResult:
        link = self.s.scalar(select(RegulationSourceLink).where(RegulationSourceLink.raw_document_id == raw.id))
        if link is None:
            link = RegulationSourceLink(raw_document_id=raw.id, regulation_id=reg_id)
            self.s.add(link)
        link.regulation_id, link.match_method, link.match_score, link.matched_key = reg_id, method, score, key
        raw.regulation_id = reg_id
        return LinkResult(reg_id, method, score, created, key)

    def _sources_of(self, reg_id: int) -> set[str]:
        return set(self.s.scalars(select(RawDocument.source_code).join(
            RegulationSourceLink, RegulationSourceLink.raw_document_id == RawDocument.id
        ).where(RegulationSourceLink.regulation_id == reg_id)))

    def _key_match(self, f: DocFacts) -> tuple[int, str] | None:
        keys = f.strong_keys + f.weak_keys
        if not keys:
            return None
        rows = {k.key: k for k in self.s.scalars(select(RegulationKey).where(RegulationKey.key.in_(keys)))}
        for key in f.strong_keys:
            if (row := rows.get(key)) and self._alive(row.regulation_id):
                return row.regulation_id, key
        for key in f.weak_keys:
            if (row := rows.get(key)) and self._alive(row.regulation_id) \
                    and f.source_code not in self._sources_of(row.regulation_id):
                return row.regulation_id, key
        return None

    def _alive(self, reg_id: int) -> bool:
        reg = self.s.get(Regulation, reg_id)
        return reg is not None and reg.processing_status != "MERGED"

    def _fuzzy(self, f: DocFacts) -> tuple[Regulation | None, float]:
        w = timedelta(days=self.settings.dedupe_date_window_days)
        cands = self.s.scalars(select(Regulation).where(
            Regulation.processing_status != "MERGED",
            or_(Regulation.publish_date.between(f.doc_date - w, f.doc_date + w), Regulation.publish_date.is_(None)),
        ).order_by(Regulation.id.desc()).limit(500)).all()
        best, best_score = None, 0.0
        for reg in cands:
            if not issuers_compatible(reg.issuer, f.issuer):
                continue
            if f.source_code in self._sources_of(reg.id):
                continue
            score = self.score(f, reg)
            if score > best_score:
                best, best_score = reg, score
        return best, best_score

    def score(self, f: DocFacts, reg: Regulation) -> float:
        title_sim = fuzz.token_set_ratio(f.title.key, title_key(reg.title)) / 100
        issuer_sim = 1.0 if (reg.issuer and f.issuer and issuers_compatible(reg.issuer, f.issuer)) else 0.5
        reg_text = self._primary_text(reg.id)
        if f.text.strip() and reg_text.strip():
            text_sim = fuzz.ratio(tr_lower(f.text[:2000]), tr_lower(reg_text[:2000])) / 100
            return 0.6 * title_sim + 0.3 * text_sim + 0.1 * issuer_sim
        return 0.85 * title_sim + 0.15 * issuer_sim

    def _primary_text(self, reg_id: int) -> str:
        return self.s.scalar(select(RawDocument.text).join(
            RegulationSourceLink, RegulationSourceLink.raw_document_id == RawDocument.id
        ).where(RegulationSourceLink.regulation_id == reg_id, RawDocument.role != "attachment")
            .order_by(RawDocument.id).limit(1)) or ""

    def _create(self, raw: RawDocument, f: DocFacts, pending: dict | None) -> Regulation:
        extra = {k: v for k, v in {
            "decision_no": f.title.decision_no, "decision_date": f.title.decision_date, "seq_no": f.title.seq_no,
            "amended_title": f.title.amended_title, "rg_issue": f.rg_issue, "rg_date": f.rg_date,
        }.items() if v}
        if pending:
            extra["dedupe_candidates"] = [pending]
        reg = Regulation(
            title=f.title.title or (raw.title or ""), issuer=f.issuer, issuer_name=f.issuer_name,
            reg_type=f.title.reg_type, is_amendment=f.title.is_amendment, publish_date=f.official_date,
            primary_source_code=raw.source_code, canonical_key=(f.strong_keys + f.weak_keys + [f"raw:{raw.id}"])[0],
            processing_status="BASELINE" if raw.is_baseline else "NEW", needs_dedupe_review=bool(pending),
            extra=extra,
        )
        self.s.add(reg)
        self.s.flush()
        return reg

    def _enrich(self, reg: Regulation, f: DocFacts) -> None:
        """Yeni kaynaktan gelen bilgiyle eksik alanları tamamlar (ör. RG kopyasındaki RG sayısı, kurum)."""
        if not reg.issuer and f.issuer:
            reg.issuer, reg.issuer_name = f.issuer, f.issuer_name
        if not reg.reg_type and f.title.reg_type:
            reg.reg_type = f.title.reg_type
        if f.rg_issue and not (reg.extra or {}).get("rg_issue"):
            reg.extra = {**(reg.extra or {}), "rg_issue": f.rg_issue, "rg_date": f.rg_date}
        if f.rg_date:
            reg.publish_date = f.official_date   # resmi yayım tarihi RG tarihidir

    def _register_keys(self, f: DocFacts, reg_id: int) -> None:
        keys = [(k, True) for k in f.strong_keys] + [(k, False) for k in f.weak_keys]
        existing = {k.key: k for k in self.s.scalars(select(RegulationKey).where(
            RegulationKey.key.in_([k for k, _ in keys])))} if keys else {}
        conflicts = []
        for key, strong in keys:
            row = existing.get(key)
            if row is None:
                self.s.add(RegulationKey(key=key, regulation_id=reg_id, strong=strong, raw_document_id=f.raw_id))
            elif row.regulation_id != reg_id and strong:
                conflicts.append({"key": key, "regulation_id": row.regulation_id})
        if conflicts:
            reg = self.s.get(Regulation, reg_id)
            reg.extra = {**(reg.extra or {}), "key_conflicts": conflicts}
            log.warning("düzenleme %s: anahtar çakışması %s", reg_id, conflicts)
        self.s.flush()


# ------------------------------------------------------------------------------------------------ yönetim


def merge_regulations(session: Session, from_id: int, into_id: int, *, method: str = "manual") -> None:
    """``from_id`` düzenlemesini ``into_id``'ye katar (onay bekleyen eşleşmenin onaylanması)."""
    if from_id == into_id:
        return
    src, dst = session.get(Regulation, from_id), session.get(Regulation, into_id)
    if src is None or dst is None:
        raise LookupError("düzenleme bulunamadı")
    for link in session.scalars(select(RegulationSourceLink).where(RegulationSourceLink.regulation_id == from_id)):
        link.regulation_id = into_id
        if link.match_method == "new":
            link.match_method = method
    for raw in session.scalars(select(RawDocument).where(RawDocument.regulation_id == from_id)):
        raw.regulation_id = into_id
    for key in session.scalars(select(RegulationKey).where(RegulationKey.regulation_id == from_id)):
        key.regulation_id = into_id
    src.processing_status = "MERGED"
    src.extra = {**(src.extra or {}), "merged_into": into_id}
    dst.needs_dedupe_review = False
    session.flush()


def split_document(session: Session, raw_id: int) -> int:
    """Yanlış birleştirmeyi geri alır: belgeyi (ve eklerini, sonraki sürümlerini) yeni bir düzenlemeye taşır.
    Plan §6 İK-2: ``POST /admin/regulations/{id}/split`` bu fonksiyonu çağırır."""
    raw = session.get(RawDocument, raw_id)
    if raw is None or raw.regulation_id is None:
        raise LookupError("belge bağlı değil")
    old = session.get(Regulation, raw.regulation_id)
    f = doc_facts(raw)
    reg = Regulation(title=f.title.title or raw.title or "", issuer=f.issuer, issuer_name=f.issuer_name,
                     reg_type=f.title.reg_type, is_amendment=f.title.is_amendment, publish_date=f.doc_date,
                     primary_source_code=raw.source_code, canonical_key=f"split:{raw.id}",
                     processing_status="BASELINE" if raw.is_baseline else "NEW",
                     extra={"split_from": old.id})
    session.add(reg)
    session.flush()
    moved = session.scalars(select(RawDocument).where(
        RawDocument.regulation_id == old.id,
        or_(RawDocument.id == raw.id, RawDocument.parent_id == raw.id,
            (RawDocument.source_code == raw.source_code) & (RawDocument.external_id == raw.external_id)))).all()
    for doc in moved:
        doc.regulation_id = reg.id
        link = session.scalar(select(RegulationSourceLink).where(RegulationSourceLink.raw_document_id == doc.id))
        if link:
            link.regulation_id, link.match_method, link.match_score = reg.id, "manual", None
    ids = [d.id for d in moved]
    for key in session.scalars(select(RegulationKey).where(RegulationKey.raw_document_id.in_(ids))):
        key.regulation_id = reg.id
    session.flush()
    return reg.id
