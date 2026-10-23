"""Excel'den katmanlı değerlendirme seti kurma, etiket düzeltme ve setle A/B deneyi koşturma."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.llm_client import FakeLLM
from app.api.main import create_app
from app.db import EvaluationLabel, FewshotExample
from app.evaluation.evaluation_sets import build_from_compare
from app.evaluation.excel_compare import compare, parse_sheet
from tests.test_excel_compare import EXCEL, _reg, data  # noqa: F401
from tests.test_processing import env  # noqa: F401

SETS = "/api/v1/evaluation/sets"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def fixture_data(data):  # noqa: F811
    """test_excel_compare verisine dönemde sistemin ilgisiz bulduğu, Excel'de olmayan bir kayıt ekler."""
    sf, ids = data
    ids["F"] = _reg(sf, "f", "Üniversite lisansüstü yönetmeliği", "IRRELEVANT", date(2026, 10, 1), relevant=False)
    return sf, ids


def _build(sf, **kwargs):
    with sf() as session:
        result = compare(session, parse_sheet("liste.xlsx", EXCEL))
        evaluation_set = build_from_compare(session, result, name="Eylül", source_name="liste.xlsx",
                                            actor="tester", **kwargs)
        session.commit()
        labels = {label.regulation_id: label for label in session.scalars(select(EvaluationLabel))}
        return evaluation_set.params, {rid: (label.stratum, label.expected_relevant, label.label_source,
                                             label.match_method, label.excel_row)
                                       for rid, label in labels.items()}


def test_set_assigns_strata_and_label_sources(fixture_data):
    sf, ids = fixture_data
    params, labels = _build(sf)
    assert labels == {
        ids["A"]: ("excel_relevant", True, "excel", "fuzzy_title", 1),
        ids["B"]: ("excel_irrelevant", True, "excel", "fuzzy_text", 2),
        ids["B2"]: ("extra_alert", False, "excel_absent", None, None),
        ids["D"]: ("extra_alert", False, "excel_absent", None, None),
        ids["F"]: ("system_irrelevant", False, "excel_absent", None, None),
    }
    assert params["counts"] == {"excel_relevant": 1, "excel_irrelevant": 1, "extra_alert": 2, "system_irrelevant": 1}
    assert params["populations"]["extra_alert"] == {"population": 2, "sampled": 2}
    assert params["excelRows"] == 3 and params["leakageExcluded"] == 0


def test_sampling_is_seeded_and_caps_sampled_strata(fixture_data):
    sf, ids = fixture_data
    first, _ = _build(sf, extra_sample=1, seed=7)
    assert first["counts"]["extra_alert"] == 1
    assert first["populations"]["extra_alert"] == {"population": 2, "sampled": 1}
    with sf() as session:
        picks = []
        for _ in range(2):
            result = compare(session, parse_sheet("liste.xlsx", EXCEL))
            evaluation_set = build_from_compare(session, result, name="x", source_name=None, actor="t",
                                                extra_sample=1, seed=7)
            session.flush()
            picks.append(sorted(label.regulation_id for label in session.scalars(select(EvaluationLabel).where(
                EvaluationLabel.set_id == evaluation_set.id, EvaluationLabel.stratum == "extra_alert"))))
        assert picks[0] == picks[1]
        session.rollback()


def test_fewshot_records_never_enter_the_set(fixture_data):
    sf, ids = fixture_data
    with sf() as session:
        session.add(FewshotExample(task="relevance", regulation_id=ids["D"], input_text="x",
                                   expected_output={}, origin="user_correction"))
        session.commit()
    params, labels = _build(sf)
    assert ids["D"] not in labels and params["leakageExcluded"] == 1
    assert params["populations"]["extra_alert"]["population"] == 1


def test_wilson_and_mcnemar_values():
    from app.evaluation.prompt_experiments import mcnemar_p, wilson

    assert wilson(0, 0) is None
    assert wilson(0, 10) == [0.0, 0.278]
    assert wilson(5, 10) == [0.237, 0.763]
    assert mcnemar_p(0, 0) is None
    assert mcnemar_p(0, 5) == 0.0625 and mcnemar_p(1, 4) == 0.375 and mcnemar_p(3, 3) == 1.0


def _item(stratum, expected, a, b):
    return SimpleNamespace(stratum=stratum, expected_relevant=expected,
                           result_a={"is_relevant": a}, result_b={"is_relevant": b})


def test_summary_reports_strata_significance_and_weighted_precision():
    from app.evaluation.prompt_experiments import build_summary

    items = [_item("excel_relevant", True, True, True), _item("excel_relevant", True, True, False),
             _item("extra_alert", False, True, False), _item("extra_alert", False, False, False)]
    summary = build_summary(items, {"extra_alert": {"population": 10, "sampled": 2}})
    assert summary["strata"]["excel_relevant"]["A"]["rate"] == 1.0
    assert summary["strata"]["excel_relevant"]["lost"] == 1 and summary["strata"]["extra_alert"]["lost"] == 1
    assert summary["strata"]["extra_alert"]["A"]["ci"] == [0.095, 0.905]
    # ikinci Excel satırında yalnız A, birinci fazla uyarıda yalnız B doğru
    assert summary["significance"] == {"aOnlyCorrect": 1, "bOnlyCorrect": 1, "p": 1.0}
    # A: tahmin edilen ilgili = 2 (Excel) + 1 × 5 (ağırlık) = 7, doğru = 2
    assert summary["estimatedPrecision"]["A"] == round(2 / 7, 3)
    assert summary["estimatedPrecision"]["B"] == 1.0
    assert build_summary(items)["estimatedPrecision"] is None


def test_candidate_template_without_rules_is_the_production_prompt(settings):
    from app.ai.common import knowledge, render
    from app.ai.relevance import _system_context, candidate_system_prompts, relevance_system_prompt

    kn = knowledge(settings)
    plain = render("relevance.v2.system.j2", variant={"roles": False, "scope": False, "control": False},
                   **_system_context(kn))
    assert plain == relevance_system_prompt(kn)   # v2 = v1 + açılıp kapanan kural blokları; başka fark yok
    prompts = {c["key"]: c["systemPrompt"] for c in candidate_system_prompts(kn)}
    assert set(prompts) == {"v2-tam", "v2-roller", "v2-kapsam", "v1-kontrol",
                            "en-tam", "en-roller", "en-kapsam", "en-kontrol", "en-v1"}
    assert all(text != plain for text in prompts.values())
    roles, group = "kişisel veri sorumlusu", "Bereket Varlık Kiralama A.Ş."
    assert roles in prompts["v2-tam"] and group in prompts["v2-tam"]
    assert roles in prompts["v2-roller"] and group not in prompts["v2-roller"]
    assert group in prompts["v2-kapsam"] and roles not in prompts["v2-kapsam"]
    assert roles not in prompts["v1-kontrol"] and group not in prompts["v1-kontrol"]
    # İngilizce varyantlar: talimatlar İngilizce, bilgi (kılavuz, taksonomi) ve çıktı alanları aynı
    en_roles = "personal data controller"
    assert en_roles in prompts["en-tam"] and group in prompts["en-tam"]
    assert en_roles in prompts["en-roller"] and group not in prompts["en-roller"]
    assert group in prompts["en-kapsam"] and en_roles not in prompts["en-kapsam"]
    for key in ("en-kontrol", "en-v1"):
        assert en_roles not in prompts[key] and group not in prompts[key]
    assert prompts["en-kontrol"] != prompts["en-v1"]
    for key in ("en-tam", "en-roller", "en-kapsam", "en-kontrol", "en-v1"):
        assert prompts[key].startswith("You are") and "Sen bir" not in prompts[key]
        assert kn.guide in prompts[key] and "<kaynak_metin>" in prompts[key] and "rationale: in Turkish" in prompts[key]


def test_strata_count_uncertain_decisions_per_side():
    from app.evaluation.prompt_experiments import build_summary

    item = SimpleNamespace(stratum="extra_alert", expected_relevant=False,
                           result_a={"is_relevant": True, "uncertain": True},
                           result_b={"is_relevant": False, "uncertain": False})
    strata = build_summary([item])["strata"]["extra_alert"]
    assert strata["A"]["uncertain"] == 1 and strata["B"]["uncertain"] == 0


def _client(settings, sf, monkeypatch):
    from app.evaluation import prompt_experiments

    test_settings = settings.model_copy(update={"llm_provider": "vllm", "llm_model": "fake", "relevance_samples": 1})

    def handler(task, messages, n):
        relevant = "B-only-test-guidance" not in messages[0]["content"]
        return [{"is_relevant": relevant, "relevance_score": 0.9 if relevant else 0.1, "reg_type": "Yönetmelik",
                 "matched_topics": [], "matched_criteria": [], "rationale": "Test.", "uncertain": False}] * n

    monkeypatch.setattr(prompt_experiments, "get_llm", lambda _s: FakeLLM(test_settings, handler))
    return TestClient(create_app(test_settings, sf))


def test_set_api_label_correction_and_experiment_on_set(settings, fixture_data, monkeypatch):
    sf, ids = fixture_data
    client = _client(settings, sf, monkeypatch)

    created = client.post(f"{SETS}/from-excel", files={"file": ("liste.xlsx", EXCEL, XLSX)}, data={"name": "Eylül seti"})
    assert created.status_code == 201, created.text
    set_id = created.json()["id"]
    assert created.json()["counts"] == {"excel_relevant": 1, "excel_irrelevant": 1, "extra_alert": 2,
                                        "system_irrelevant": 1}
    assert client.get(SETS).json()["items"][0]["id"] == set_id

    patched = client.patch(f"{SETS}/{set_id}/labels/{ids['D']}",
                           json={"expected_relevant": True, "note": "Liste kaçırmış"})
    assert patched.status_code == 200 and patched.json()["source"] == "human"
    assert client.delete(f"{SETS}/{set_id}/labels/{ids['F']}").status_code == 204
    assert client.delete(f"{SETS}/{set_id}/labels/{ids['F']}").status_code == 404
    detail = client.get(f"{SETS}/{set_id}").json()
    by_id = {label["regulationId"]: label for label in detail["labels"]}
    assert by_id[ids["D"]]["expectedRelevant"] is True and ids["F"] not in by_id

    defaults = client.get("/api/v1/evaluation/prompt-experiments/defaults").json()
    assert [c["key"] for c in defaults["candidates"]] == ["v2-tam", "v2-roller", "v2-kapsam", "v1-kontrol",
                                                         "en-tam", "en-roller", "en-kapsam", "en-kontrol", "en-v1"]
    prompt_a = defaults["systemPromptA"]
    body = {"title": "Set deneyi", "evaluation_set_id": set_id, "system_prompt_b": prompt_a + "\nB-only-test-guidance"}
    mixed = client.post("/api/v1/evaluation/prompt-experiments",
                        json={**body, "records": [{"regulation_id": ids["A"]}]})
    assert mixed.status_code == 400
    assert client.post("/api/v1/evaluation/prompt-experiments", json={**body, "evaluation_set_id": 999}).status_code == 404
    started = client.post("/api/v1/evaluation/prompt-experiments", json=body)
    assert started.status_code == 202, started.text
    detail = client.get(f"/api/v1/evaluation/prompt-experiments/{started.json()['id']}").json()
    assert detail["status"] == "completed" and detail["evaluationSetId"] == set_id
    assert {item["regulationId"]: item["stratum"] for item in detail["items"]} == {
        ids["A"]: "excel_relevant", ids["B"]: "excel_irrelevant", ids["B2"]: "extra_alert", ids["D"]: "extra_alert"}
    strata = detail["summary"]["strata"]
    assert strata["extra_alert"]["A"]["relevant"] == 2 and strata["extra_alert"]["B"]["relevant"] == 0
    assert strata["excel_relevant"]["lost"] == 1 and "system_irrelevant" not in strata
    # D'nin etiketi "ilgili"ye düzeltildi: A (hepsine ilgili der) onu doğru bilir, B bilmez
    assert detail["summary"]["A"]["tp"] == 3 and detail["summary"]["B"]["tn"] == 1
    assert detail["summary"]["estimatedPrecision"]["B"] is None


def test_set_endpoints_validate_input(settings, fixture_data, monkeypatch):
    sf, _ = fixture_data
    client = _client(settings, sf, monkeypatch)
    empty = client.post(f"{SETS}/from-excel", files={"file": ("liste.xlsx", EXCEL, XLSX)}, data={"name": "  "})
    assert empty.status_code in (400, 422)
    assert client.get(f"{SETS}/404").status_code == 404
    assert client.patch(f"{SETS}/404/labels/1", json={"expected_relevant": True}).status_code == 404
    prompt = client.get("/api/v1/evaluation/prompt-experiments/defaults").json()["systemPromptA"]
    no_records = client.post("/api/v1/evaluation/prompt-experiments",
                             json={"title": "Boş", "system_prompt_b": prompt + " farklı"})
    assert no_records.status_code == 422
