from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.ai.llm_client import FakeLLM
from app.api.main import create_app
from app.db import (
    LlmCall,
    PromptExperiment,
    PromptExperimentItem,
    RawDocument,
    Regulation,
    RegulationSourceLink,
    Source,
)
from tests.test_processing import add_raw, env  # noqa: F401


def test_unlabelled_records_only_count_prompt_decision_changes():
    from app.evaluation.prompt_experiments import build_summary

    item = SimpleNamespace(
        expected_relevant=None,
        result_a={"is_relevant": True},
        result_b={"is_relevant": False},
    )
    summary = build_summary([item])
    assert summary["changed"] == 1 and summary["unlabelled"] == 1
    assert summary["A"]["labelled"] == 0 and summary["A"]["recall"] is None
    assert summary["B"]["labelled"] == 0 and summary["B"]["precision"] is None


def _test_regulation(sf, storage) -> int:
    with sf() as session:
        if session.get(Source, "BDDK") is None:
            session.add(Source(code="BDDK", name="BDDK"))
        session.flush()
        record = Regulation(
            title="Bankaların uzaktan çalışma usullerine ilişkin düzenleme",
            issuer="BDDK",
            issuer_name="Bankacılık Düzenleme ve Denetleme Kurumu",
            reg_type="Yönetmelik",
            primary_source_code="BDDK",
            canonical_key="prompt-experiment-test",
            processing_status="READY",
            publish_date=date(2026, 10, 1),
            is_relevant=True,
            relevance_score=0.8,
        )
        session.add(record)
        session.commit()
        regulation_id = record.id
    text = "İşverenlerin uzaktan çalışma düzenine ilişkin esasları belirlenmiştir. " * 8
    raw_id = add_raw(
        sf, storage, source="BDDK", channel="test", external_id="prompt-experiment-test",
        url="https://example.test/prompt-experiment", title="Uzaktan çalışma düzenlemesi",
        published=date(2026, 10, 1), content=text.encode("utf-8"),
    )
    with sf() as session:
        raw = session.get(RawDocument, raw_id)
        raw.text = text
        session.add(RegulationSourceLink(regulation_id=regulation_id, raw_document_id=raw_id, match_method="new"))
        session.commit()
    return regulation_id


def test_prompt_experiments_store_both_sides_and_reuse_llm_cache(settings, env, monkeypatch):  # noqa: F811
    from app.evaluation import prompt_experiments

    sf, storage = env
    regulation_id = _test_regulation(sf, storage)
    test_settings = settings.model_copy(update={
        "llm_provider": "vllm", "llm_model": "fake", "relevance_samples": 1,
    })
    calls = []

    def handler(task, messages, n):
        calls.append((task, messages, n))
        relevant = "B-only-test-guidance" not in messages[0]["content"]
        return [{
            "is_relevant": relevant,
            "relevance_score": 0.9 if relevant else 0.1,
            "reg_type": "Yönetmelik",
            "matched_topics": ["KURUMSAL"] if relevant else [],
            "matched_criteria": ["işveren"],
            "rationale": "Test gerekçesi.",
            "uncertain": False,
        }] * n

    fake = FakeLLM(test_settings, handler)
    monkeypatch.setattr(prompt_experiments, "get_llm", lambda _settings: fake)
    app = create_app(test_settings, sf)
    client = TestClient(app)

    defaults = client.get("/api/v1/evaluation/prompt-experiments/defaults")
    assert defaults.status_code == 200
    prompt_a = defaults.json()["systemPromptA"]
    body = {
        "title": "İşveren rolü deneyi",
        "records": [{"regulation_id": regulation_id, "expected_relevant": True}],
        "system_prompt_b": prompt_a + "\nB-only-test-guidance",
    }
    first = client.post("/api/v1/evaluation/prompt-experiments", json=body)
    assert first.status_code == 202, first.text
    first_id = first.json()["id"]
    detail = client.get(f"/api/v1/evaluation/prompt-experiments/{first_id}").json()
    assert detail["status"] == "completed", (detail["summary"], detail["items"])
    assert detail["items"][0]["A"]["is_relevant"] is True
    assert detail["items"][0]["B"]["is_relevant"] is False
    assert detail["summary"]["changed"] == 1
    assert detail["summary"]["A"]["tp"] == 1
    assert detail["summary"]["B"]["fn"] == 1
    assert len(calls) == 2

    second = client.post("/api/v1/evaluation/prompt-experiments", json=body)
    assert second.status_code == 202, second.text
    second_detail = client.get(
        f"/api/v1/evaluation/prompt-experiments/{second.json()['id']}"
    ).json()
    assert second_detail["summary"]["cachedCalls"] == 2
    assert len(calls) == 2
    assert client.get("/api/v1/evaluation/prompt-experiments").json()["items"][0]["id"] == second.json()["id"]

    with sf() as session:
        assert session.scalar(select(func.count()).select_from(LlmCall)) == 2
        assert session.scalar(select(func.count()).select_from(PromptExperiment)) == 2
        assert session.scalar(select(func.count()).select_from(PromptExperimentItem)) == 2
        assert session.get(Regulation, regulation_id).is_relevant is True


def test_prompt_experiment_rejects_duplicate_records_and_same_prompt(settings, env, monkeypatch):  # noqa: F811
    from app.ai import llm_client

    sf, storage = env
    regulation_id = _test_regulation(sf, storage)
    test_settings = settings.model_copy(update={"llm_provider": "vllm"})
    monkeypatch.setattr(llm_client, "get_llm", lambda _settings: object())
    client = TestClient(create_app(test_settings, sf))
    prompt = client.get("/api/v1/evaluation/prompt-experiments/defaults").json()["systemPromptA"]
    base = {"title": "Doğrulama", "system_prompt_b": prompt + " farklı", "records": []}

    duplicate = {**base, "records": [
        {"regulation_id": regulation_id, "expected_relevant": None},
        {"regulation_id": regulation_id, "expected_relevant": False},
    ]}
    assert client.post("/api/v1/evaluation/prompt-experiments", json=duplicate).status_code == 400
    same = {**base, "system_prompt_b": prompt,
            "records": [{"regulation_id": regulation_id, "expected_relevant": None}]}
    assert client.post("/api/v1/evaluation/prompt-experiments", json=same).status_code == 400
