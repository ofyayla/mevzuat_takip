"""Arşiv: portalda görünmeyen kayıtların sorgulanması (``/arsiv`` ekranı).

İki tür kayıt portal listesine düşmez ama sorgulanabilir olmalıdır:

* ``irrelevant`` — YZ'nin bankayla ilgisiz bulduğu kayıtlar (``processing_status = IRRELEVANT``); yanlış elemeyi
  yakalamak ve gerekçeyi görmek için.
* ``baseline`` — bir kanalın ilk taramasında sitede zaten duran, YZ hattına hiç girmemiş kayıtlar (``BASELINE``).

Bu kayıtlar binlerce, ilk taramadan sonra on binlerce olabilir; bu yüzden liste:

* sunucu tarafında sayfalanır (``page``/``page_size`` ≤ 100) ve hiçbir yerde "hepsini yükle" yapılmaz;
* ``ix_regulation_status_publish`` dizini sayesinde varsayılan sıralama (durum + en yeni yayın) sıralama yapmadan okunur;
* satır başına sorgu atmaz — kaynaklar sayfadaki tüm kayıtlar için tek sorguyla toplanır (portal listesindeki
  ``serialize`` satır başına 4+ sorgu atar, burada gerekmez);
* sekme sayıları tek ``GROUP BY`` sorgusuyla, geçerli süzgeçlere göre hesaplanır.

Aşırı büyük sonuç kümelerinde kullanıcıyı arama/tarih/kaynak süzgeçlerine yönlendirmek için sayfa sayısı
``MAX_PAGES`` ile sınırlıdır (derin OFFSET taraması yerine süzgeç).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.db import RawDocument, Regulation, RegulationSourceLink
from app.processing.normalize import search_fold
from app.services.regulations import ApiError, _fold_sql, _iso, _like_escape

KINDS = {"irrelevant": "IRRELEVANT", "baseline": "BASELINE"}
STATUS_TO_KIND = {v: k for k, v in KINDS.items()}
MAX_PAGES = 2000
RATIONALE_PREVIEW = 320


@dataclass
class ArchiveQuery:
    kind: str = "irrelevant"          # irrelevant | baseline | all
    source: str | None = None
    q: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = 1
    page_size: int = 25
    sort: str | None = None
    direction: str = "desc"


def _filters(query: ArchiveQuery) -> list:
    """Tür dışındaki süzgeçler (sekme sayıları bunlarla hesaplanır)."""
    conds = []
    if query.source:
        conds.append(exists().where(RegulationSourceLink.regulation_id == Regulation.id,
                                    RawDocument.id == RegulationSourceLink.raw_document_id,
                                    RawDocument.source_code == query.source))
    if query.date_from:
        conds.append(Regulation.publish_date >= query.date_from)
    if query.date_to:
        conds.append(Regulation.publish_date <= query.date_to)
    if query.q and query.q.strip():
        like = "%" + _like_escape(search_fold(query.q.strip())) + "%"
        conds.append(or_(_fold_sql(Regulation.title).like(like, escape="\\"),
                         _fold_sql(Regulation.issuer_name).like(like, escape="\\")))
    return conds


def _statuses(kind: str) -> tuple[str, ...]:
    if kind == "all":
        return tuple(KINDS.values())
    if kind not in KINDS:
        raise ApiError(400, "invalid_kind", "kind: irrelevant | baseline | all")
    return (KINDS[kind],)


def _order(query: ArchiveQuery):
    cols = {"publish_date": Regulation.publish_date, "detected_at": Regulation.detected_at,
            "title": Regulation.title, "issuer": func.coalesce(Regulation.issuer_name, Regulation.issuer),
            "score": Regulation.relevance_score}
    tie = (Regulation.publish_date.desc().nulls_last(), Regulation.id.desc())
    if query.sort in cols:
        col = cols[query.sort]
        primary = col.asc().nulls_last() if query.direction == "asc" else col.desc().nulls_last()
        return (primary, *tie)
    return tie


def _source_info(session: Session, reg_ids: list[int]) -> dict[int, dict]:
    """Sayfadaki kayıtların kaynak kodları ve birincil bağlantısı — tek sorgu."""
    info: dict[int, dict] = {i: {"sources": set(), "url": None, "rank": None} for i in reg_ids}
    if not reg_ids:
        return info
    rows = session.execute(select(RegulationSourceLink.regulation_id, RawDocument.id, RawDocument.source_code,
                                  RawDocument.url, RawDocument.role)
                           .join(RawDocument, RawDocument.id == RegulationSourceLink.raw_document_id)
                           .where(RegulationSourceLink.regulation_id.in_(reg_ids)))
    for reg_id, doc_id, source, url, role in rows:
        entry = info[reg_id]
        entry["sources"].add(source)
        if role == "attachment":
            continue
        rank = (source != "RESMI_GAZETE", doc_id)     # portaldaki _primary_url ile aynı tercih
        if entry["rank"] is None or rank < entry["rank"]:
            entry["rank"], entry["url"] = rank, url
    return info


def _relevance(reg: Regulation) -> dict:
    return (reg.classification or {}).get("relevance", {}) or {}


def _item(reg: Regulation, src: dict) -> dict:
    rel = _relevance(reg)
    rationale = rel.get("rationale") or None
    if rationale and len(rationale) > RATIONALE_PREVIEW:
        rationale = rationale[:RATIONALE_PREVIEW].rstrip() + "…"
    return {
        "id": reg.id, "title": reg.title, "issuer": reg.issuer_name or reg.issuer, "issuerCode": reg.issuer,
        "kind": STATUS_TO_KIND.get(reg.processing_status, reg.processing_status.lower()),
        "sources": sorted(src["sources"]), "sourceUrl": src["url"],
        "publishDate": reg.publish_date.isoformat() if reg.publish_date else None,
        "detectedAt": _iso(reg.detected_at), "classifiedAt": _iso(reg.classified_at),
        "relevanceScore": reg.relevance_score, "rationale": rationale, "uncertain": rel.get("uncertain"),
    }


def list_archive(session: Session, query: ArchiveQuery) -> dict:
    statuses = _statuses(query.kind)
    if query.page > MAX_PAGES:
        raise ApiError(400, "page_too_deep", "çok ileri bir sayfa; arama, tarih veya kaynak süzgeciyle daraltın",
                       {"max_page": MAX_PAGES})
    conds = _filters(query)
    by_status = dict(session.execute(select(Regulation.processing_status, func.count())
                                     .where(Regulation.processing_status.in_(tuple(KINDS.values())), *conds)
                                     .group_by(Regulation.processing_status)).all())
    counts = {kind: by_status.get(status, 0) for kind, status in KINDS.items()}
    counts["all"] = sum(counts.values())
    total = counts["all"] if query.kind == "all" else counts[query.kind]
    rows = session.scalars(select(Regulation).where(Regulation.processing_status.in_(statuses), *conds)
                           .order_by(*_order(query))
                           .offset((query.page - 1) * query.page_size).limit(query.page_size)).all()
    src = _source_info(session, [r.id for r in rows])
    return {"items": [_item(r, src[r.id]) for r in rows], "total": total, "counts": counts, "page": query.page,
            "pageSize": query.page_size, "pages": -(-total // query.page_size) if total else 0}


def archive_detail(session: Session, reg_id: int) -> dict:
    reg = session.get(Regulation, reg_id)
    if reg is None or reg.processing_status not in STATUS_TO_KIND:
        raise ApiError(404, "not_found", "arşivde böyle bir kayıt yok")
    docs = session.scalars(select(RawDocument).join(
        RegulationSourceLink, RegulationSourceLink.raw_document_id == RawDocument.id
    ).where(RegulationSourceLink.regulation_id == reg_id).order_by(RawDocument.id)).all()
    src = _source_info(session, [reg_id])[reg_id]
    rel = _relevance(reg)
    item = _item(reg, src)
    item["rationale"] = rel.get("rationale") or None      # önizleme değil, tam gerekçe
    item.update({
        "criteria": rel.get("criteria", []),
        "documents": [{"source": d.source_code, "url": d.url, "title": d.title, "role": d.role,
                       "publishedAt": d.published_at.isoformat() if d.published_at else None,
                       "fetchedAt": _iso(d.fetched_at), "isBaseline": bool(d.is_baseline)} for d in docs],
        "note": ("Kanalın ilk taramasında sitede zaten duran içerik; YZ ilgililik analizine girmedi."
                 if reg.processing_status == "BASELINE" else None),
    })
    return item
