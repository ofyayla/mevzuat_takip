"""İK-8 paralel çalışma: Başkanlığın manuel tespitleri ↔ sistem kayıtları ve karşılaştırma raporu (plan §6 İK-8).

Eşleştirme (İK-2 tekilleştirme mantığı yeniden kullanılır): kanonik URL → güçlü anahtar (karar/sıra no, mevzuat.gov.tr)
→ başlık benzerliği (token_set ≥ 85, yayım tarihi ±7 gün, kurum uyumlu). Birleştirilmiş (MERGED) kayıt, katıldığı
kayda kadar izlenir.

Kaçırma nedenleri:
  not_collected    hiçbir kaynakta toplanmadı (İK-1 sorunu)
  not_processed    toplandı ama düzenleme kaydına bağlanmadı / işlenmedi (İK-2)
  baseline         ilk taramada sitede zaten duran içerik olduğu için YZ'ye gönderilmedi
  below_threshold  YZ ilgisiz buldu (İK-3 eşik sorunu)
  dedupe_lost      başka bir kayda birleştirildi ve o kayıt portalda değil (İK-2)
"""
from __future__ import annotations

import csv
import io
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.dates import parse_tr_date, tr_lower
from app.db import (
    ManualDetection,
    RawDocument,
    Regulation,
    RegulationKey,
    UnitSuggestion,
)
from app.processing.dedupe import canonical_url
from app.processing.normalize import ISSUERS, issuers_compatible, match_issuer, parse_title, title_key
from app.services.regulations import VISIBLE_STATUSES

TITLE_THRESHOLD = 85
DATE_WINDOW = timedelta(days=7)
COLUMNS = {"başlık": "title", "baslik": "title", "title": "title", "kurum": "issuer", "issuer": "issuer",
           "yayım tarihi": "publish_date", "yayim tarihi": "publish_date", "tarih": "publish_date",
           "publish_date": "publish_date", "bağlantı": "url", "baglanti": "url", "url": "url", "önem": "severity",
           "onem": "severity", "severity": "severity", "birimler": "unit_codes", "birim": "unit_codes",
           "unit_codes": "unit_codes", "not": "note", "note": "note"}


# ------------------------------------------------------------------------------------------------ giriş


def parse_rows(filename: str, content: bytes) -> list[dict]:
    """CSV (virgül/noktalı virgül, UTF-8/UTF-8-BOM/Windows-1254) veya Excel (.xlsx) → satır sözlükleri."""
    if filename.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl

        ws = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True).active
        rows = list(ws.iter_rows(values_only=True))
        header = [str(h or "").strip() for h in rows[0]]
        records = [dict(zip(header, r)) for r in rows[1:] if any(v not in (None, "") for v in r)]
    else:
        for enc in ("utf-8-sig", "cp1254"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
        records = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    out = []
    for r in records:
        row = {}
        for k, v in r.items():
            key = COLUMNS.get(tr_lower(str(k or "").strip()))
            if key:
                row[key] = v
        if row.get("title"):
            out.append(row)
    return out


def normalize_row(row: dict) -> dict:
    d = row.get("publish_date")
    if isinstance(d, datetime):
        d = d.date()
    elif isinstance(d, str):
        d = date.fromisoformat(d) if len(d) == 10 and d[4] == "-" else parse_tr_date(d)
    units = row.get("unit_codes") or []
    if isinstance(units, str):
        units = [u.strip() for u in units.replace(";", ",").split(",") if u.strip()]
    return {"title": str(row["title"]).strip(), "issuer": (str(row.get("issuer") or "").strip() or None),
            "publish_date": d, "url": (str(row.get("url") or "").strip() or None),
            "severity": (str(row.get("severity") or "").strip() or None), "unit_codes": units,
            "note": (str(row.get("note") or "").strip() or None)}


def add_detection(session: Session, data: dict, entered_by: str | None) -> ManualDetection:
    md = ManualDetection(**data, entered_by=entered_by)
    session.add(md)
    session.flush()
    match_detection(session, md)
    return md


# ------------------------------------------------------------------------------------------------ eşleştirme


def _follow_merge(session: Session, reg: Regulation | None) -> Regulation | None:
    seen = set()
    while reg is not None and reg.processing_status == "MERGED" and reg.id not in seen:
        seen.add(reg.id)
        into = (reg.extra or {}).get("merged_into")
        nxt = session.get(Regulation, into) if into else None
        if nxt is None:
            break
        reg = nxt
    return reg


def issuer_code(text: str | None) -> str | None:
    """Başkanlık kurum sütununa kısaltma ("BDDK", "Hazine ve Maliye Bak.") veya tam ad yazabilir."""
    if not text:
        return None
    t = text.strip()
    if t.upper() in ISSUERS:
        return t.upper()
    return match_issuer(t)


def match_detection(session: Session, md: ManualDetection) -> None:
    md.matched_regulation_id = md.matched_raw_document_id = md.match_method = md.match_score = None
    if md.url:
        cu = canonical_url(md.url)
        row = session.get(RegulationKey, "url:" + cu)       # İK-2 her belge için URL anahtarı tutar
        if row is not None:
            md.matched_regulation_id, md.match_method, md.match_score = row.regulation_id, "url", 1.0
            return
        tail = cu.split("/", 1)[-1][-80:]                    # bağlanmamış ham belge: yol sonuna göre aday, sonra tam
        for hit in session.scalars(select(RawDocument).where(RawDocument.url.contains(tail)).limit(50)):
            if canonical_url(hit.url) == cu:
                md.matched_raw_document_id, md.match_method, md.match_score = hit.id, "url", 1.0
                md.matched_regulation_id = hit.regulation_id
                return
    tf = parse_title(md.title)
    issuer = issuer_code(md.issuer) or match_issuer(md.title)
    keys = []
    if issuer and tf.decision_no:
        keys.append(f"karar:{issuer}:{tf.decision_no.lower()}")
    if issuer and tf.seq_no and tf.reg_type:
        keys.append(f"no:{issuer}:{tr_lower(tf.reg_type)}:{tf.seq_no.lower()}")
    for k in keys:
        row = session.get(RegulationKey, k)
        if row is not None:
            md.matched_regulation_id, md.match_method, md.match_score = row.regulation_id, "exact_key", 1.0
            return
    q = select(Regulation)
    if md.publish_date:
        q = q.where(Regulation.publish_date.between(md.publish_date - DATE_WINDOW, md.publish_date + DATE_WINDOW))
    best, best_score = None, 0.0
    key = title_key(md.title)
    for reg in session.scalars(q):
        if issuer and reg.issuer and not issuers_compatible(issuer, reg.issuer):
            continue
        sc = fuzz.token_set_ratio(key, title_key(reg.title))
        if sc > best_score:
            best, best_score = reg, sc
    if best is not None and best_score >= TITLE_THRESHOLD:
        md.matched_regulation_id, md.match_method, md.match_score = best.id, "fuzzy_title", round(best_score / 100, 3)
        return
    # düzenlemeye bağlanmamış ham belge (işlenmemiş) başlıkla aranır
    rq = select(RawDocument).where(RawDocument.regulation_id.is_(None), RawDocument.role != "attachment")
    if md.publish_date:
        rq = rq.where(RawDocument.published_at.between(md.publish_date - DATE_WINDOW, md.publish_date + DATE_WINDOW))
    for raw in session.scalars(rq):
        if fuzz.token_set_ratio(key, title_key(raw.title or "")) >= TITLE_THRESHOLD:
            md.matched_raw_document_id, md.match_method = raw.id, "fuzzy_title_raw"
            return


def classify_detection(session: Session, md: ManualDetection) -> tuple[str, Regulation | None]:
    reg = session.get(Regulation, md.matched_regulation_id) if md.matched_regulation_id else None
    if reg is None:
        return ("not_processed" if md.matched_raw_document_id else "not_collected"), None
    final = _follow_merge(session, reg)
    visible = final is not None and final.is_relevant and final.processing_status in VISIBLE_STATUSES
    if visible:
        return "detected", final
    if reg.processing_status == "MERGED":
        return "dedupe_lost", final
    if final.processing_status == "BASELINE":
        return "baseline", final
    if final.is_relevant is False or final.processing_status == "IRRELEVANT":
        return "below_threshold", final
    return "not_processed", final


# ------------------------------------------------------------------------------------------------ rapor


def _decide(reg: Regulation, threshold: float) -> bool:
    """İK-3 karar kuralı, farklı eşikte (eşik taraması)."""
    c = (reg.classification or {}).get("relevance", {})
    score = reg.relevance_score or 0.0
    return score >= threshold or bool(c.get("model_uncertain")) or bool(c.get("split_votes")) or \
        (not c.get("has_text", True) and score >= threshold / 2)


@dataclass
class Report:
    data: dict


def report(session: Session, date_from: date, date_to: date, thresholds=(0.2, 0.3, 0.4, 0.5, 0.6)) -> dict:
    mds = session.scalars(select(ManualDetection).where(
        ManualDetection.publish_date.between(date_from, date_to))).all()
    cats: dict[str, list] = {}
    sev_pairs, matched_regs = [], []
    for md in mds:
        cat, reg = classify_detection(session, md)
        cats.setdefault(cat, []).append({"id": md.id, "title": md.title, "regulation_id": reg.id if reg else None})
        if reg is not None:
            matched_regs.append(reg)
        if cat == "detected" and md.severity and reg.severity:
            sev_pairs.append((md.severity, reg.severity))
    total = len(mds)
    missed = total - len(cats.get("detected", []))

    regs = session.scalars(select(Regulation).where(Regulation.publish_date.between(date_from, date_to),
                                                    Regulation.classified_at.is_not(None),
                                                    Regulation.processing_status != "MERGED")).all()
    visible = [r for r in regs if r.is_relevant and r.processing_status in VISIBLE_STATUSES]
    decided = [r for r in visible if r.review_status != "Bekliyor"]
    rejected = [r for r in decided if r.review_status == "Reddedildi"]

    # birim isabeti: onaylanan kayıtta ilk YZ önerisi (rank 1, origin ai) nihai birimler arasında mı
    top1 = anyhit = n_units = 0
    for r in (x for x in visible if x.review_status == "Onaylandı"):
        final = set(session.scalars(select(UnitSuggestion.unit_code).where(
            UnitSuggestion.regulation_id == r.id, UnitSuggestion.is_final.is_(True))))
        ai = list(session.scalars(select(UnitSuggestion.unit_code).where(
            UnitSuggestion.regulation_id == r.id, UnitSuggestion.origin == "ai").order_by(
            UnitSuggestion.created_at, UnitSuggestion.rank)))
        if not final or not ai:
            continue
        n_units += 1
        top1 += ai[0] in final
        anyhit += bool(set(ai) & final)

    latencies = []
    for r in visible:
        if r.publish_date and r.detected_at:
            det = r.detected_at if r.detected_at.tzinfo else r.detected_at.replace(tzinfo=timezone.utc)
            pub = datetime.combine(r.publish_date, datetime.min.time(), tzinfo=timezone(timedelta(hours=3)))
            latencies.append(max(0.0, (det - pub).total_seconds() / 3600))

    matched_ids = {r.id for r in matched_regs}
    sweep = []
    for t in thresholds:
        shown = [r for r in regs if r.is_relevant is not None and _decide(r, t)]
        miss_t = sum(1 for r in matched_regs if r.is_relevant is not None and not _decide(r, t)) + \
            sum(len(cats.get(c, [])) for c in ("not_collected", "not_processed", "dedupe_lost", "baseline"))
        noise_t = sum(1 for r in shown if r.review_status == "Reddedildi")
        sweep.append({"threshold": t, "shown": len(shown), "missed": miss_t,
                      "miss_rate": round(miss_t / total, 3) if total else None,
                      "rejected_shown": noise_t,
                      "manual_hidden": sorted(r.id for r in matched_regs if r.id in matched_ids
                                              and r.is_relevant is not None and not _decide(r, t))})

    return {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "manual": {"total": total, "detected": len(cats.get("detected", [])), "missed": missed,
                   "miss_rate": round(missed / total, 3) if total else None,
                   "by_reason": {k: len(v) for k, v in cats.items() if k != "detected"},
                   "missed_items": {k: v for k, v in cats.items() if k != "detected"}},
        "system": {"shown": len(visible), "decided": len(decided), "rejected": len(rejected),
                   "unnecessary_rate": round(len(rejected) / len(decided), 3) if decided else None,
                   "pending": len(visible) - len(decided)},
        "severity_agreement": {"n": len(sev_pairs),
                               "exact": round(sum(a == b for a, b in sev_pairs) / len(sev_pairs), 3)
                               if sev_pairs else None},
        "unit_accuracy": {"n": n_units, "top1": round(top1 / n_units, 3) if n_units else None,
                          "any": round(anyhit / n_units, 3) if n_units else None},
        "latency_hours": {"n": len(latencies),
                          "median": round(statistics.median(latencies), 1) if latencies else None,
                          "p90": round(statistics.quantiles(latencies, n=10)[8], 1) if len(latencies) >= 10 else None},
        "threshold_sweep": sweep,
    }


def filtered_out(session: Session, date_from: date, date_to: date, limit: int = 500) -> list[dict]:
    """Eşik altında kalan (IRRELEVANT) kayıtlar — kaçırma analizi için (plan İK-3)."""
    rows = session.scalars(select(Regulation).where(
        Regulation.publish_date.between(date_from, date_to), Regulation.processing_status == "IRRELEVANT")
        .order_by(Regulation.relevance_score.desc()).limit(limit))
    return [{"id": r.id, "title": r.title, "issuer": r.issuer_name or r.issuer, "publishDate": str(r.publish_date),
             "score": r.relevance_score,
             "rationale": ((r.classification or {}).get("relevance") or {}).get("rationale")} for r in rows]
