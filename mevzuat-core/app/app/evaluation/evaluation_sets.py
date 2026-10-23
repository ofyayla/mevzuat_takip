"""Excel karşılaştırmasından kalıcı, katmanlı değerlendirme seti kurar.

Excel zayıf bir altın standarttır: "listede var" ilgili sayılır, "listede yok" ilgisiz sayılır. Bu yüzden set dört katmanlı
kurulur ve her etiketin kaynağı saklanır; Excel'in kaçırdığı bir kayıt fark edilirse etiket sonradan düzeltilebilir.

  excel_relevant     Excel ∩ sistem ilgili        beklenen: ilgili   (recall'u korur)
  excel_irrelevant   Excel ∩ sistem ilgisiz       beklenen: ilgili   (iyileştirme hedefi)
  extra_alert        sistem ilgili, Excel'de yok  beklenen: ilgisiz  (örneklem; gereksiz uyarı)
  system_irrelevant  sistem ilgisiz, Excel'de yok beklenen: ilgisiz  (örneklem; "her şeye ilgili" kontrolü)
"""
from __future__ import annotations

import random
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ai.relevance import load_fewshot
from app.collectors.dates import tr_lower
from app.db import EvaluationLabel, EvaluationSet, FewshotExample, Regulation

STRATA = ("excel_relevant", "excel_irrelevant", "extra_alert", "system_irrelevant")
STRATUM_LABELS = {
    "excel_relevant": "Excel'de var · sistem ilgili",
    "excel_irrelevant": "Excel'de var · sistem ilgisiz",
    "extra_alert": "Fazla uyarı (Excel'de yok)",
    "system_irrelevant": "Sistem ilgisiz (Excel'de yok)",
}
SAMPLED = ("extra_alert", "system_irrelevant")   # nüfusundan örneklenen katmanlar; diğerleri sayımdır
DEFAULT_SEED = 42
DEFAULT_EXTRA_SAMPLE = 40
DEFAULT_CONTROL_SAMPLE = 35
MAX_SAMPLE = 200


def leakage_filter(session: Session) -> tuple[set[int], set[str]]:
    """Few-shot'a girmiş kayıtlar (düzeltme havuzu) ve YAML örneklerinin başlıkları sete alınmaz."""
    ids = {rid for rid in session.scalars(select(FewshotExample.regulation_id).where(
        FewshotExample.task == "relevance", FewshotExample.regulation_id.is_not(None)))}
    titles = {tr_lower(example["title"]) for example in load_fewshot() if example.get("title")}
    return ids, titles


def _sample(rng: random.Random, population: list[int], n: int) -> list[int]:
    return sorted(rng.sample(population, min(n, len(population))))


def _control_population(session: Session, period: dict, exclude: set[int]) -> list[Regulation]:
    lo, hi = period.get("from"), period.get("to")
    if not (lo and hi):
        return []
    stmt = select(Regulation).where(
        Regulation.publish_date.between(date.fromisoformat(lo), date.fromisoformat(hi)),
        or_(Regulation.is_relevant.is_(False), Regulation.processing_status == "IRRELEVANT"),
    ).order_by(Regulation.id)
    return [reg for reg in session.scalars(stmt) if reg.id not in exclude]


def build_from_compare(session: Session, result: dict, *, name: str, source_name: str | None, actor: str,
                       seed: int = DEFAULT_SEED, extra_sample: int = DEFAULT_EXTRA_SAMPLE,
                       control_sample: int = DEFAULT_CONTROL_SAMPLE) -> EvaluationSet:
    """``excel_compare.compare`` sonucundan seti kurar (onaylı satır seçimleri sonuca zaten işlenmiştir)."""
    rng = random.Random(seed)
    leak_ids, leak_titles = leakage_filter(session)
    leaked = 0

    def allowed(reg_id: int, title: str) -> bool:
        nonlocal leaked
        if reg_id in leak_ids or tr_lower(title or "") in leak_titles:
            leaked += 1
            return False
        return True

    labels: dict[int, dict] = {}
    duplicates = 0
    for item in result["items"]:
        reg = item["regulation"]
        if reg is None or item["group"] not in ("relevant", "irrelevant"):
            continue
        if reg["id"] in labels:
            duplicates += 1
            continue
        if not allowed(reg["id"], reg["title"]):
            continue
        labels[reg["id"]] = {
            "title": reg["title"], "stratum": f"excel_{item['group']}", "expected": True, "source": "excel",
            "excel_row": item["no"], "method": item["method"],
        }
    matched_ids = {item["regulation"]["id"] for item in result["items"] if item["regulation"] is not None}

    extra_by_id = {row["id"]: row for row in sorted(result["extra"], key=lambda row: row["id"])}
    extra_ids = [rid for rid, row in extra_by_id.items() if allowed(rid, row["title"])]
    for rid in _sample(rng, extra_ids, extra_sample):
        labels[rid] = {"title": extra_by_id[rid]["title"], "stratum": "extra_alert", "expected": False,
                       "source": "excel_absent", "excel_row": None, "method": None}

    control = [reg for reg in _control_population(session, result["precision"]["period"], matched_ids | set(labels))
               if allowed(reg.id, reg.title)]
    control_by_id = {reg.id: reg for reg in control}
    for rid in _sample(rng, list(control_by_id), control_sample):
        labels[rid] = {"title": control_by_id[rid].title, "stratum": "system_irrelevant", "expected": False,
                       "source": "excel_absent", "excel_row": None, "method": None}

    counts = {stratum: sum(1 for label in labels.values() if label["stratum"] == stratum) for stratum in STRATA}
    params = {
        "seed": seed, "extraSample": extra_sample, "controlSample": control_sample,
        "period": result["precision"]["period"], "thresholds": result["thresholds"],
        "excelRows": result["summary"]["total"], "duplicateRows": duplicates, "leakageExcluded": leaked,
        # nüfus/örneklem: örneklenen katmanlarda nüfus Excel'den bağımsız tüm adaylardır (precision tahmini ağırlığı)
        "populations": {
            "excel_relevant": {"population": counts["excel_relevant"], "sampled": counts["excel_relevant"]},
            "excel_irrelevant": {"population": counts["excel_irrelevant"], "sampled": counts["excel_irrelevant"]},
            "extra_alert": {"population": len(extra_ids), "sampled": counts["extra_alert"]},
            "system_irrelevant": {"population": len(control_by_id), "sampled": counts["system_irrelevant"]},
        },
        "counts": counts,
    }
    evaluation_set = EvaluationSet(name=name, source_name=source_name, params=params, created_by=actor)
    session.add(evaluation_set)
    session.flush()
    for rid, label in labels.items():
        session.add(EvaluationLabel(
            set_id=evaluation_set.id, regulation_id=rid, title_snapshot=label["title"], stratum=label["stratum"],
            expected_relevant=label["expected"], label_source=label["source"], excel_row=label["excel_row"],
            match_method=label["method"], labelled_by=actor))
    return evaluation_set


def stratum_counts(session: Session, set_id: int) -> dict[str, int]:
    rows = session.execute(select(EvaluationLabel.stratum, func.count()).where(
        EvaluationLabel.set_id == set_id).group_by(EvaluationLabel.stratum)).all()
    counts = dict(rows)
    return {stratum: counts.get(stratum, 0) for stratum in STRATA}


def label_output(label: EvaluationLabel) -> dict:
    return {
        "regulationId": label.regulation_id, "title": label.title_snapshot, "stratum": label.stratum,
        "stratumLabel": STRATUM_LABELS.get(label.stratum, label.stratum), "expectedRelevant": label.expected_relevant,
        "source": label.label_source, "excelRow": label.excel_row, "matchMethod": label.match_method,
        "note": label.note, "labelledBy": label.labelled_by, "labelledAt": label.labelled_at.isoformat(),
    }


def set_output(session: Session, evaluation_set: EvaluationSet, labels: list[EvaluationLabel] | None = None) -> dict:
    result = {
        "id": evaluation_set.id, "name": evaluation_set.name, "sourceName": evaluation_set.source_name,
        "params": evaluation_set.params, "createdBy": evaluation_set.created_by,
        "createdAt": evaluation_set.created_at.isoformat(),
        "counts": stratum_counts(session, evaluation_set.id),
    }
    if labels is not None:
        result["labels"] = [label_output(label) for label in labels]
    return result
