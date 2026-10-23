"""Persistent, read-only A/B relevance-prompt experiments."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timezone
from io import BytesIO

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import relevance
from app.ai.common import Knowledge, RegulationContext, regulation_context
from app.ai.llm_client import LLMError, get_llm
from app.db import EvaluationSet, PromptExperiment, PromptExperimentItem, Regulation
from app.evaluation.evaluation_sets import SAMPLED, STRATA, STRATUM_LABELS
from app.settings import Settings

log = logging.getLogger(__name__)
_SETTING_KEYS = (
    "llm_provider", "llm_model", "relevance_threshold", "relevance_weight_model", "relevance_weight_votes",
    "relevance_weight_rules", "relevance_samples", "relevance_sample_temperature", "relevance_max_input_chars",
    "confidence_high", "confidence_medium",
)

# Kayıt eşzamanlılığı tüm deneyler için ortaktır: iki deney birlikte koşsa da vLLM'e (üretim sınıflandırmasıyla paylaşılan
# sunucu) aynı anda en fazla ``prompt_experiment_parallel`` kayıt gider. PostgreSQL'de oturum düzeyi danışma kilitleri
# sınırı API pod'ları arasında da ortak kılar; SQLite'ta (yerel/test, tek süreç) süreç içi semafor yeterlidir.
_SLOT_LOCK_KEY = 0x50455850   # pg_try_advisory_lock(int, int): ilk anahtar bu ad alanı, ikincisi yuva numarası
_SLOT_POLL_S = 1.0
_local_slots: dict[int, threading.BoundedSemaphore] = {}
_local_slots_guard = threading.Lock()


@contextmanager
def experiment_slot(engine: Engine, limit: int):
    """Ortak ``limit`` yuvadan birini tutar; boş yuva yoksa biri açılana dek bekler."""
    if engine.dialect.name != "postgresql":
        with _local_slots_guard:
            slots = _local_slots.setdefault(limit, threading.BoundedSemaphore(limit))
        with slots:
            yield
        return
    while True:
        conn = engine.connect()
        try:
            slot = next((n for n in range(limit) if conn.scalar(
                text("SELECT pg_try_advisory_lock(:key, :slot)"), {"key": _SLOT_LOCK_KEY, "slot": n})), None)
        except BaseException:
            conn.invalidate()   # kilit alınıp yanıt kaybolduysa bağlantıyla birlikte bırakılır
            conn.close()
            raise
        if slot is not None:
            break
        conn.close()
        time.sleep(_SLOT_POLL_S)
    try:
        yield
    finally:
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:key, :slot)"), {"key": _SLOT_LOCK_KEY, "slot": slot})
        except Exception:  # noqa: BLE001 — kilitli bağlantı havuza dönmesin; kapanan bağlantı kilidi bırakır
            log.exception("Prompt deneyi yuvası %s bırakılamadı; bağlantı kapatılıyor", slot)
            conn.invalidate()
        conn.close()


def freeze_settings(settings: Settings, knowledge: Knowledge, examples: list[dict]) -> dict:
    return {
        **{key: getattr(settings, key) for key in _SETTING_KEYS},
        "knowledge_version": knowledge.version,
        "taxonomy": knowledge.taxonomy,
        "guide": knowledge.guide,
        "fewshot_examples": examples,
    }


def context_hash(ctx: RegulationContext, config: dict) -> str:
    value = {
        "title": ctx.title,
        "issuer": ctx.issuer,
        "sources": ctx.sources,
        "publish_date": ctx.publish_date,
        "reg_type_hint": ctx.reg_type_hint,
        "text": ctx.text,
        "has_text": ctx.has_text,
        "alt_titles": ctx.alt_titles or [],
        "model": config["llm_model"],
        "provider": config["llm_provider"],
        "settings": {key: config[key] for key in _SETTING_KEYS if key.startswith("relevance_") or
                     key.startswith("confidence_")},
        "knowledge_version": config["knowledge_version"],
        "fewshot_examples": config["fewshot_examples"],
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decision_json(decision: relevance.RelevanceDecision) -> dict:
    return {
        "is_relevant": decision.is_relevant,
        "score": decision.score,
        "band": decision.band,
        "uncertain": decision.uncertain,
        "reg_type": decision.reg_type,
        "topics": decision.topics,
        "criteria": decision.criteria,
        "rationale": decision.rationale,
        "components": decision.components,
        "cached": bool(decision.components.get("cached")),
        "call_id": decision.call_id,
    }


def _side_metrics(items: list[PromptExperimentItem], side: str) -> dict:
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    result_field = "result_a" if side == "A" else "result_b"
    for item in items:
        result = getattr(item, result_field)
        if item.expected_relevant is None or result is None:
            continue
        predicted = result["is_relevant"]
        expected = item.expected_relevant
        key = "tp" if predicted and expected else "fp" if predicted else "fn" if expected else "tn"
        counts[key] += 1
    labelled = sum(counts.values())
    recall_denominator = counts["tp"] + counts["fn"]
    precision_denominator = counts["tp"] + counts["fp"]
    return {
        **counts,
        "labelled": labelled,
        "recall": round(counts["tp"] / recall_denominator, 3) if recall_denominator else None,
        "precision": round(counts["tp"] / precision_denominator, 3) if precision_denominator else None,
    }


def wilson(successes: int, n: int, z: float = 1.96) -> list[float] | None:
    """Oran için Wilson %95 güven aralığı; küçük n'de normal yaklaşıktan daha güvenilir."""
    if not n:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def mcnemar_p(a_only: int, b_only: int) -> float | None:
    """Uyumsuz çiftler (yalnız A / yalnız B doğru) için tam iki yönlü McNemar p-değeri."""
    n = a_only + b_only
    if not n:
        return None
    tail = sum(math.comb(n, i) for i in range(min(a_only, b_only) + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def _result(item: PromptExperimentItem, side: str) -> dict | None:
    return item.result_a if side == "A" else item.result_b


def _strata_summary(items: list[PromptExperimentItem]) -> dict:
    """Katman başına her tarafın "ilgili" dediği oran. Excel katmanlarında bu isabet (recall), Excel dışı
    katmanlarda gereksiz uyarı oranıdır; gained/lost A→B yön değiştiren kayıt sayısıdır."""
    summary = {}
    for stratum in STRATA:
        rows = [item for item in items if getattr(item, "stratum", None) == stratum]
        if not rows:
            continue
        entry: dict = {"total": len(rows)}
        for side in ("A", "B"):
            done = [r for r in (_result(item, side) for item in rows) if r is not None]
            relevant = sum(bool(r["is_relevant"]) for r in done)
            entry[side] = {"n": len(done), "relevant": relevant,
                           "rate": round(relevant / len(done), 3) if done else None, "ci": wilson(relevant, len(done)),
                           # tereddüt tek başına kaydı portala düşürür; kapsam kuralının etkisi burada görünür
                           "uncertain": sum(bool(r.get("uncertain")) for r in done)}
        both = [item for item in rows if item.result_a is not None and item.result_b is not None]
        entry["gained"] = sum(not item.result_a["is_relevant"] and item.result_b["is_relevant"] for item in both)
        entry["lost"] = sum(item.result_a["is_relevant"] and not item.result_b["is_relevant"] for item in both)
        summary[stratum] = entry
    return summary


def _significance(items: list[PromptExperimentItem]) -> dict:
    a_only = b_only = 0
    for item in items:
        if item.expected_relevant is None or item.result_a is None or item.result_b is None:
            continue
        a_ok = item.result_a["is_relevant"] == item.expected_relevant
        b_ok = item.result_b["is_relevant"] == item.expected_relevant
        a_only += a_ok and not b_ok
        b_only += b_ok and not a_ok
    return {"aOnlyCorrect": a_only, "bOnlyCorrect": b_only, "p": mcnemar_p(a_only, b_only)}


def _estimated_precision(items: list[PromptExperimentItem], populations: dict | None, side: str) -> float | None:
    """Örneklenen katmanları nüfus/örneklem ağırlığıyla büyüterek tahmin edilen precision. Excel'de olmayan
    "ilgisiz" etiketi doğru kabul edildiğinden bu bir alt sınırdır."""
    if not populations:
        return None
    predicted = true_positive = 0.0
    for stratum in STRATA:
        rows = [item for item in items if getattr(item, "stratum", None) == stratum
                and item.expected_relevant is not None and _result(item, side) is not None]
        if not rows:
            continue
        population = (populations.get(stratum) or {}).get("population")
        weight = population / len(rows) if stratum in SAMPLED and population else 1.0
        for item in rows:
            if _result(item, side)["is_relevant"]:
                predicted += weight
                true_positive += weight * bool(item.expected_relevant)
    return round(true_positive / predicted, 3) if predicted else None


def build_summary(items: list[PromptExperimentItem], populations: dict | None = None) -> dict:
    return {
        "total": len(items),
        "labelled": sum(item.expected_relevant is not None for item in items),
        "unlabelled": sum(item.expected_relevant is None for item in items),
        "changed": sum(item.result_a is not None and item.result_b is not None and
                       item.result_a["is_relevant"] != item.result_b["is_relevant"] for item in items),
        "A": _side_metrics(items, "A"),
        "B": _side_metrics(items, "B"),
        "cachedCalls": sum(bool(result and result.get("cached")) for item in items
                           for result in (item.result_a, item.result_b)),
        "strata": _strata_summary(items),
        "significance": _significance(items),
        "estimatedPrecision": {"A": _estimated_precision(items, populations, "A"),
                               "B": _estimated_precision(items, populations, "B")} if populations else None,
    }


def _populations(session: Session, experiment: PromptExperiment) -> dict | None:
    if experiment.evaluation_set_id is None:
        return None
    evaluation_set = session.get(EvaluationSet, experiment.evaluation_set_id)
    return (evaluation_set.params or {}).get("populations") if evaluation_set is not None else None


def _failure(session: Session, item_id: int, message: str) -> None:
    item = session.get(PromptExperimentItem, item_id)
    if item is None:
        return
    item.status = "failed"
    if item.result_a is None:
        item.error_a = item.error_a or message
    if item.result_b is None:
        item.error_b = item.error_b or message
    session.commit()


def _fail_experiment(sf: sessionmaker[Session], experiment_id: int, message: str) -> None:
    with sf() as session:
        experiment = session.get(PromptExperiment, experiment_id)
        if experiment is None:
            log.error("Başlatılamayan prompt deneyi %s veritabanında bulunamadı", experiment_id)
            return
        experiment.status = "failed"
        experiment.completed_at = datetime.now(timezone.utc)
        items = list(session.scalars(select(PromptExperimentItem).where(
            PromptExperimentItem.experiment_id == experiment_id)))
        for item in items:
            item.status = "failed"
            if item.result_a is None:
                item.error_a = item.error_a or message
            if item.result_b is None:
                item.error_b = item.error_b or message
        experiment.summary = {**build_summary(items, _populations(session, experiment)), "error": message}
        session.commit()


def run_prompt_experiment(sf: sessionmaker[Session], settings: Settings, experiment_id: int) -> None:
    with sf() as session:
        experiment = session.get(PromptExperiment, experiment_id)
        if experiment is None:
            log.error("Prompt deneyi %s bulunamadı; arka plan çalışması durduruldu", experiment_id)
            return
        experiment.status = "running"
        experiment.started_at = datetime.now(timezone.utc)
        item_ids = list(session.scalars(select(PromptExperimentItem.id).where(
            PromptExperimentItem.experiment_id == experiment_id).order_by(PromptExperimentItem.id)))
        config = dict(experiment.settings_snapshot)
        prompt_a, prompt_b = experiment.system_prompt_a, experiment.system_prompt_b
        session.commit()

    run_settings = settings.model_copy(update={key: config[key] for key in _SETTING_KEYS})
    try:
        llm = get_llm(run_settings)
    except Exception as exc:
        log.exception("Prompt deneyi %s için LLM istemcisi başlatılamadı", experiment_id)
        _fail_experiment(sf, experiment_id, f"{type(exc).__name__}: {exc}")
        return
    if llm is None:
        _fail_experiment(sf, experiment_id, "LLM yapılandırılmamış")
        return

    kn = Knowledge(taxonomy=config["taxonomy"], guide=config["guide"], version=config["knowledge_version"])
    prompt_a_version = f"relevance.{relevance.PROMPT}+{kn.version}"
    prompt_b_hash = hashlib.sha256(prompt_b.encode("utf-8")).hexdigest()[:16]
    prompt_b_version = f"relevance.experiment.{prompt_b_hash}+{kn.version}"
    parallel = max(1, int(settings.prompt_experiment_parallel))
    engine = sf.kw["bind"]

    def process(item_id: int) -> None:
        try:
            with experiment_slot(engine, parallel), sf() as session:
                item = session.get(PromptExperimentItem, item_id)
                reg = session.get(Regulation, item.regulation_id)
                if reg is None:
                    item.status = "failed"
                    item.error_a = item.error_b = "Düzenleme kaydı bulunamadı."
                    session.commit()
                    return
                ctx = regulation_context(session, reg, run_settings.relevance_max_input_chars)
                item.input_hash = context_hash(ctx, config)
                item.status = "running"
                session.commit()

                if item.result_a is None and item.error_a is None:
                    try:
                        decision_a = relevance.assess(
                            llm, ctx, kn, run_settings, session=session, force=False,
                            system_prompt_override=prompt_a, prompt_version_override=prompt_a_version,
                            examples_override=config["fewshot_examples"],
                        )
                        item.result_a = _decision_json(decision_a)
                        session.commit()
                    except LLMError as exc:
                        item.error_a = str(exc)
                        session.commit()

                if item.result_b is None and item.error_b is None:
                    try:
                        decision_b = relevance.assess(
                            llm, ctx, kn, run_settings, session=session, force=False,
                            system_prompt_override=prompt_b, prompt_version_override=prompt_b_version,
                            examples_override=config["fewshot_examples"],
                        )
                        item.result_b = _decision_json(decision_b)
                        session.commit()
                    except LLMError as exc:
                        item.error_b = str(exc)
                        session.commit()

                item.status = "completed" if item.result_a is not None and item.result_b is not None else "failed"
                session.commit()
        except Exception as exc:  # persist and log unexpected worker failures instead of leaving the run stuck
            log.exception("Prompt deneyi %s, kayıt öğesi %s işlenemedi", experiment_id, item_id)
            with sf() as session:
                _failure(session, item_id, f"{type(exc).__name__}: {exc}")

    # her kayıt kendi oturumunu açar; A ve B aynı kayıtta sıralı kalır, eşzamanlılık kayıtlar arasındadır
    with ThreadPoolExecutor(parallel, thread_name_prefix=f"prompt-experiment-{experiment_id}") as pool:
        list(pool.map(process, item_ids))

    with sf() as session:
        experiment = session.get(PromptExperiment, experiment_id)
        items = list(session.scalars(select(PromptExperimentItem).where(
            PromptExperimentItem.experiment_id == experiment_id).order_by(PromptExperimentItem.id)))
        experiment.summary = build_summary(items, _populations(session, experiment))
        experiment.status = "completed" if all(item.status == "completed" for item in items) else \
            "partial" if any(item.result_a is not None or item.result_b is not None for item in items) else "failed"
        experiment.completed_at = datetime.now(timezone.utc)
        session.commit()


def item_output(item: PromptExperimentItem) -> dict:
    return {
        "regulationId": item.regulation_id,
        "title": item.title_snapshot,
        "expectedRelevant": item.expected_relevant,
        "stratum": item.stratum,
        "status": item.status,
        "inputHash": item.input_hash,
        "A": item.result_a,
        "B": item.result_b,
        "errorA": item.error_a,
        "errorB": item.error_b,
    }


def experiment_output(experiment: PromptExperiment, items: list[PromptExperimentItem] | None = None) -> dict:
    result = {
        "id": experiment.id,
        "title": experiment.title,
        "status": experiment.status,
        "evaluationSetId": experiment.evaluation_set_id,
        "summary": experiment.summary,
        "createdBy": experiment.created_by,
        "createdAt": experiment.created_at.isoformat(),
        "startedAt": experiment.started_at.isoformat() if experiment.started_at else None,
        "completedAt": experiment.completed_at.isoformat() if experiment.completed_at else None,
    }
    if items is not None:
        result.update({
            "systemPromptA": experiment.system_prompt_a,
            "systemPromptB": experiment.system_prompt_b,
            "settings": {key: experiment.settings_snapshot[key] for key in _SETTING_KEYS},
            "items": [item_output(item) for item in items],
        })
    return result


# ------------------------------------------------------------------------------------------------ Excel çıktısı

def _yes_no(value: bool | None) -> str | None:
    return None if value is None else "İlgili" if value else "İlgisiz"


def _side_cell(side: str, field: str):
    def get(item: dict):
        result = item.get(side)
        if result is None:
            return None
        value = result.get(field)
        return "; ".join(value) if isinstance(value, list) else value
    return get


def _decision(side: str):
    return lambda item: _yes_no((item.get(side) or {}).get("is_relevant")) if item.get(side) else None


def _correct(side: str):
    def get(item: dict):
        result, expected = item.get(side), item.get("expectedRelevant")
        if result is None or expected is None:
            return None
        return "Doğru" if result["is_relevant"] == expected else "Yanlış"
    return get


def _changed(item: dict):
    a, b = item.get("A"), item.get("B")
    if a is None or b is None:
        return None
    return "Evet" if a["is_relevant"] != b["is_relevant"] else "Hayır"


def _uncertain(side: str):
    return lambda item: None if item.get(side) is None else "Evet" if item[side].get("uncertain") else "Hayır"


def _cached(side: str):
    return lambda item: None if item.get(side) is None else "Evet" if item[side].get("cached") else "Hayır"


ITEM_COLUMNS: list[tuple[str, int, callable]] = [
    ("Kayıt no", 10, lambda i: i["regulationId"]),
    ("Başlık", 60, lambda i: i["title"]),
    ("Katman", 28, lambda i: STRATUM_LABELS.get(i["stratum"], i["stratum"]) if i.get("stratum") else None),
    ("Beklenen", 12, lambda i: _yes_no(i["expectedRelevant"])),
    ("A kararı", 12, _decision("A")),
    ("A skoru", 10, _side_cell("A", "score")),
    ("A bandı", 12, _side_cell("A", "band")),
    ("A tereddüt", 10, _uncertain("A")),
    ("A doğruluğu", 12, _correct("A")),
    ("B kararı", 12, _decision("B")),
    ("B skoru", 10, _side_cell("B", "score")),
    ("B bandı", 12, _side_cell("B", "band")),
    ("B tereddüt", 10, _uncertain("B")),
    ("B doğruluğu", 12, _correct("B")),
    ("Karar değişti", 12, _changed),
    ("A konuları", 30, _side_cell("A", "topics")),
    ("B konuları", 30, _side_cell("B", "topics")),
    ("A gerekçesi", 70, _side_cell("A", "rationale")),
    ("B gerekçesi", 70, _side_cell("B", "rationale")),
    ("A önbellekten", 12, _cached("A")),
    ("B önbellekten", 12, _cached("B")),
    ("Kayıt durumu", 12, lambda i: i["status"]),
    ("A hatası", 40, lambda i: i["errorA"]),
    ("B hatası", 40, lambda i: i["errorB"]),
    ("Girdi özeti", 24, lambda i: i["inputHash"]),
]
_STRATUM_COLUMNS: list[tuple[str, int, callable]] = [
    ("Katman", 32, lambda r: r[0]),
    ("Kayıt", 10, lambda r: r[1]["total"]),
    ("A ilgili / değerlendirilen", 20, lambda r: f'{r[1]["A"]["relevant"]}/{r[1]["A"]["n"]}'),
    ("A oranı", 10, lambda r: r[1]["A"]["rate"]),
    ("B ilgili / değerlendirilen", 20, lambda r: f'{r[1]["B"]["relevant"]}/{r[1]["B"]["n"]}'),
    ("B oranı", 10, lambda r: r[1]["B"]["rate"]),
    ("A→B kazanılan", 14, lambda r: r[1]["gained"]),
    ("A→B kaybedilen", 14, lambda r: r[1]["lost"]),
]


def _pct(value: float | None) -> str:
    return f"%{value * 100:.1f}" if value is not None else "—"


def _metric_rows(label: str, metric: dict) -> list[tuple[str, object]]:
    return [(f"{label} · doğru ilgili / gereksiz / kaçırılan / doğru ilgisiz",
             f'{metric["tp"]} / {metric["fp"]} / {metric["fn"]} / {metric["tn"]}'),
            (f"{label} · etiketli kayıt", metric["labelled"]),
            (f"{label} · precision / recall", f'{_pct(metric["precision"])} / {_pct(metric["recall"])}')]


def build_workbook(experiment: dict) -> bytes:
    """``experiment_output`` çıktısını (kayıtlarıyla) üç sayfalık çalışma kitabına yazar: özet, kayıt kayıt A/B kararları,
    prompt anlık görüntüleri. Gerekçe gibi model metinleri ``services.export._cell`` ile formül enjeksiyonuna karşı korunur."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    from app.evaluation.excel_compare import _table
    from app.services.export import _cell

    summary = experiment.get("summary") or {}
    settings = experiment.get("settings") or {}
    rows: list[tuple[str, object]] = [
        ("Deney", experiment["title"]), ("Durum", experiment["status"]),
        ("Oluşturan", experiment.get("createdBy")), ("Oluşturulma", experiment.get("createdAt")),
        ("Tamamlanma", experiment.get("completedAt")),
        ("Değerlendirme seti no", experiment.get("evaluationSetId")),
        ("Sağlayıcı / model", f'{settings.get("llm_provider", "—")} / {settings.get("llm_model", "—")}'),
        ("Eşik", settings.get("relevance_threshold")),
        ("Ağırlıklar (model / oy / kural)", " / ".join(str(settings.get(k)) for k in (
            "relevance_weight_model", "relevance_weight_votes", "relevance_weight_rules"))),
        ("Örnek sayısı / sıcaklık", f'{settings.get("relevance_samples")} / {settings.get("relevance_sample_temperature")}'),
    ]
    if summary:
        rows += [("", None), ("Toplam kayıt", summary["total"]), ("Etiketli / etiketsiz",
                                                                   f'{summary["labelled"]} / {summary["unlabelled"]}'),
                 ("Karar değişen kayıt", summary["changed"]), ("Önbellekten gelen sonuç", summary["cachedCalls"])]
        rows += _metric_rows("A", summary["A"]) + _metric_rows("B", summary["B"])
        significance = summary.get("significance") or {}
        rows += [("Yalnız A / yalnız B doğru", f'{significance.get("aOnlyCorrect", 0)} / {significance.get("bOnlyCorrect", 0)}'),
                 ("McNemar p", significance.get("p"))]
        estimated = summary.get("estimatedPrecision")
        if estimated:
            rows.append(("Tahmini precision, alt sınır (A / B)", f'{_pct(estimated["A"])} / {_pct(estimated["B"])}'))
        if summary.get("error"):
            rows.append(("Hata", summary["error"]))

    wb = Workbook()
    ws = wb.active
    ws.title = "Özet"
    for label, value in rows:
        ws.append([_cell(label), _cell(value)])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 52, 48
    for c in ws["A"]:
        c.font = Font(bold=True)
    for c in ws["B"]:
        c.alignment = Alignment(vertical="top", wrap_text=True, horizontal="left")
    strata = [(STRATUM_LABELS.get(key, key), summary["strata"][key]) for key in STRATA if key in summary.get("strata", {})]
    if strata:
        _table(wb.create_sheet("Katmanlar"), _STRATUM_COLUMNS, strata)
    _table(wb.create_sheet("Kayıtlar"), ITEM_COLUMNS, experiment.get("items", []))
    prompts = wb.create_sheet("Promptlar")
    prompts.append(["A · mevcut prompt", "B · taslak prompt"])
    prompts.append([_cell(experiment.get("systemPromptA")), _cell(experiment.get("systemPromptB"))])
    for col in ("A", "B"):
        prompts.column_dimensions[col].width = 100
        prompts[f"{col}1"].font = Font(bold=True)
        prompts[f"{col}2"].alignment = Alignment(vertical="top", wrap_text=True)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def experiment_filename(experiment_id: int, today: date) -> str:
    return f"prompt-deneyi-{experiment_id}-{today.isoformat()}.xlsx"
