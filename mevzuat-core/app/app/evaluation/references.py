"""Excel kimlikleri: başlık benzerliğinden önce kurum ve yayın bağlamını doğrular."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.collectors.dates import parse_tr_date, tr_lower
from app.db import RawDocument, Regulation
from app.evaluation.parallel import issuer_code
from app.processing.normalize import ISSUERS, match_issuer, parse_title

HEADER_CHARS = 1600
_NUMBER = r"\d+(?:\s*[/\-]\s*\d+)*"
_BULLETIN = re.compile(
    r"(?P<no>\d{4}\s*/\s*\d+)\s*sayılı\s*(?:spk\s*)?bülten|"
    r"(?:spk\s+)?bülteni\s*(?P<after>\d{4}\s*/\s*\d+)"
)
_RULING = re.compile(r"\be-\d{8}-\d{2,}-\d+\b")
_LETTER = re.compile(r"\b(?P<no>\d+)\s*sayılı\s+genel\s+mektup")
_DECISION = re.compile(rf"\b(?P<no>{_NUMBER})\s*sayılı\s+ve\s+[^;]{{0,120}}?kurul\s+kararı")
_MULTIPLE = re.compile(r"\d+(?:/\d+)?\s*(?:,|ve)\s*\d+(?:/\d+)?\s*sayılı")
_REVERSE_DECISION = re.compile(
    rf"(?P<no>{_NUMBER})\s*say(?:ılı|ı)\s+ve\s+(?P<date>\d{{1,2}}[./]\d{{1,2}}[./]\d{{4}})\s*tarihli"
)
_DECISION_HEADER = re.compile(rf"\bkarar\s*(?:sayısı|no)\s*:?\s*(?P<no>{_NUMBER})")
_DECISION_DATE = re.compile(r"karar\s*tarihi\s*:?\s*(\d{1,2}[./]\d{1,2}[./]\d{4})")


@dataclass(frozen=True)
class Reference:
    kind: str
    number: str
    dated: date | None = None
    primary: bool = True
    conflicting: bool = False


@dataclass
class ReferenceResult:
    reference: Reference
    status: str
    raw: RawDocument | None = None
    candidates: list[int] = field(default_factory=list)

    def output(self) -> dict:
        return {"kind": self.reference.kind, "number": self.reference.number,
                "date": self.reference.dated.isoformat() if self.reference.dated else None,
                "status": self.status, "candidateIds": self.candidates}


def reference_issuer(value: str | None, title: str) -> str | None:
    code = issuer_code(value) or match_issuer(title)
    if code is None and re.search(r"takas\s*(?:bank|istanbul)", tr_lower(value or "")):
        return "TAKASBANK"
    return code


def _extract(text: str, issuer: str, kind: str | None = None) -> Reference | None:
    low = re.sub(r"\s+", " ", tr_lower(text)).strip()
    if issuer == "GIB" and kind in (None, "ruling") and (m := _RULING.search(low)):
        return Reference("ruling", m.group())
    if issuer == "SPK" and kind in (None, "bulletin") and (m := _BULLETIN.search(low)):
        return Reference("bulletin", re.sub(r"\s+", "", m.group("no") or m.group("after")))
    if issuer == "TAKASBANK" and kind in (None, "letter") and (m := _LETTER.search(low)):
        return Reference("letter", m.group("no"))
    if kind in (None, "decision") and (m := _DECISION_HEADER.search(low)):
        dated = parse_tr_date(d.group(1).replace("/", ".")) if (d := _DECISION_DATE.search(low)) else None
        return Reference("decision", re.sub(r"\s+", "", m.group("no")), dated)
    # Çok sayılı karar bir tek karara indirgenmez; bulanık yönteme bırakılır.
    if _MULTIPLE.search(low):
        return None
    facts = parse_title(text)
    non_decision = facts.decision_no and re.search(
        rf"(?<!\d){re.escape(facts.decision_no.lower())}\s*sayılı\s*(?:resm[îi]\s+gazete|kanun|yazı|bülten)",
        low,
    )
    numbered_context = facts.decision_no and re.search(
        rf"(?<!\d){re.escape(facts.decision_no.lower())}\s*sayılı\s+(?P<after>.{{0,100}})", low,
    )
    if numbered_context and not re.match(
        r"(?:(?:kurul|cumhurbaşkanı|bakanlar kurulu|bankamız|ilke)\s+)?karar", numbered_context.group("after"),
    ):
        non_decision = True
    if kind in (None, "decision") and facts.decision_no and not non_decision:
        dated = date.fromisoformat(facts.decision_date) if facts.decision_date else None
        if dated is None and (m := _DECISION_DATE.search(low)):
            dated = parse_tr_date(m.group(1).replace("/", "."))
        return Reference("decision", re.sub(r"\s+", "", facts.decision_no.lower()),
                         dated)
    if kind in (None, "circular") and facts.reg_type == "Genelge" and facts.seq_no:
        return Reference("circular", facts.seq_no.lower())
    if kind in (None, "decision") and (m := _DECISION.search(low)) and "kanun" not in m.group():
        return Reference("decision", re.sub(r"\s+", "", m.group("no")))
    if kind in (None, "decision") and (m := _REVERSE_DECISION.search(low)):
        if "karar" in low[m.end():m.end() + 220]:
            return Reference("decision", re.sub(r"\s+", "", m.group("no")),
                             parse_tr_date(m.group("date").replace("/", ".")))
    return None


def extract_reference(title: str, text: str, issuer: str, kind: str | None = None) -> Reference | None:
    if ref := _extract(title, issuer, kind):
        if ref.kind == "decision" and _DECISION_HEADER.search(tr_lower(text[:HEADER_CHARS])):
            header = _extract(text[:HEADER_CHARS], issuer, "decision")
            if header and (header.number != ref.number or (ref.dated and header.dated and ref.dated != header.dated)):
                return Reference(ref.kind, ref.number, ref.dated, primary=False, conflicting=True)
            if header and not ref.dated:
                return Reference(ref.kind, ref.number, header.dated)
        return ref
    ref = _extract(text[:HEADER_CHARS], issuer, kind)
    if ref:
        prefix = tr_lower(text[:HEADER_CHARS]).split(ref.number, 1)[0]
        if re.search(r"\b(?:önceki|eski|atıf|dayanak)\b", prefix):
            return Reference(ref.kind, ref.number, ref.dated, primary=False)
    return ref


def match_reference(session: Session, row: dict, window: tuple[timedelta, timedelta]) -> ReferenceResult | None:
    issuer = reference_issuer(row["issuer"], row["title"])
    if issuer is None:
        return None
    ref = extract_reference(row["title"], row["text"], issuer)
    if ref is None:
        return None
    result = ReferenceResult(ref, "unverified")
    anchor = ref.dated or row["created"]
    if anchor is None:
        return result
    periods = [RawDocument.published_at.between(anchor, anchor + timedelta(days=7))] if ref.dated else []
    if row["created"]:
        periods.append(RawDocument.published_at.between(
            row["created"] - window[0], row["created"] + window[1],
        ))
    query = (select(RawDocument, Regulation.issuer)
             .outerjoin(Regulation, Regulation.id == RawDocument.regulation_id)
             .where(RawDocument.is_latest.is_(True), RawDocument.role != "listing",
                    or_(*periods)).order_by(RawDocument.id))
    hits: dict[tuple[str, int], RawDocument] = {}
    citations: set[int] = set()
    has_conflict = ref.conflicting
    for raw, reg_issuer in session.execute(query):
        heading = (raw.text or "")[:HEADER_CHARS]
        actual_issuer = (raw.source_code if raw.source_code in ISSUERS or raw.source_code == "TAKASBANK"
                         else reg_issuer or match_issuer(heading))
        if issuer != actual_issuer:
            continue
        actual = extract_reference(raw.title or "", heading, issuer, ref.kind)
        # GİB API'sinin özelge numarası HTML başlığından bağımsız bir alandır.
        number = (raw.extra or {}).get("ozelge_no")
        if ref.kind == "ruling" and number:
            actual = Reference("ruling", tr_lower(str(number)))
        if actual and actual.conflicting and actual.number == ref.number:
            has_conflict = True
        if actual and actual.primary and actual.kind == ref.kind and actual.number == ref.number:
            if ref.dated and actual.dated != ref.dated:
                continue
            if not ref.dated and (raw.published_at.year != anchor.year or
                                  (actual.dated and actual.dated.year != anchor.year)):
                continue
            key = ("reg", raw.regulation_id) if raw.regulation_id else ("raw", raw.id)
            hits.setdefault(key, raw)
        elif raw.regulation_id and re.search(
            rf"(?<![\w/\-]){re.escape(ref.number)}(?![\w/\-])", tr_lower((raw.title or "") + "\n" + (raw.text or ""))
        ):
            citations.add(raw.regulation_id)
    result.candidates = sorted({r.regulation_id for r in hits.values() if r.regulation_id} | citations)
    if has_conflict:
        result.status = "conflict"
    elif not ref.primary:
        result.status = "citation"
    elif len(hits) == 1:
        result.raw = next(iter(hits.values()))
        result.status = "matched"
        result.candidates = [result.raw.regulation_id] if result.raw.regulation_id else []
    elif hits:
        result.status = "ambiguous"
    elif citations:
        result.status = "citation"
    return result
