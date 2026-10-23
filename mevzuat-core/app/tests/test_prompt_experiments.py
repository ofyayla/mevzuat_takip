from __future__ import annotations

import threading
import time
from contextlib import ExitStack
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


def _test_regulation(sf, storage, key: str = "prompt-experiment-test") -> int:
    with sf() as session:
        if session.get(Source, "BDDK") is None:
            session.add(Source(code="BDDK", name="BDDK"))
        session.flush()
        record = Regulation(
            title=f"Bankaların uzaktan çalışma usullerine ilişkin düzenleme ({key})",
            issuer="BDDK",
            issuer_name="Bankacılık Düzenleme ve Denetleme Kurumu",
            reg_type="Yönetmelik",
            primary_source_code="BDDK",
            canonical_key=key,
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
        sf, storage, source="BDDK", channel="test", external_id=key,
        url=f"https://example.test/{key}", title="Uzaktan çalışma düzenlemesi",
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


def _tracking_llm(test_settings, delay: float = 0.1):
    """Aynı anda süren LLM çağrılarını sayan sahte LLM; ``state["peak"]`` görülen en yüksek eşzamanlılıktır."""
    state = {"active": 0, "peak": 0, "calls": 0}
    guard = threading.Lock()

    def handler(task, messages, n):
        with guard:
            state["active"] += 1
            state["calls"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(delay)
        with guard:
            state["active"] -= 1
        return [{"is_relevant": True, "relevance_score": 0.9, "reg_type": "Yönetmelik", "matched_topics": ["KURUMSAL"],
                 "matched_criteria": ["işveren"], "rationale": "Test gerekçesi.", "uncertain": False}] * n

    return FakeLLM(test_settings, handler), state


def _experiment_body(client, regulation_ids: list[int], marker: str) -> dict:
    prompt_a = client.get("/api/v1/evaluation/prompt-experiments/defaults").json()["systemPromptA"]
    return {"title": f"Paralel deney {marker}", "system_prompt_b": f"{prompt_a}\n{marker}",
            "records": [{"regulation_id": rid, "expected_relevant": True} for rid in regulation_ids]}


def test_prompt_experiment_processes_records_in_parallel_up_to_limit(settings, env, monkeypatch):  # noqa: F811
    from app.evaluation import prompt_experiments

    sf, storage = env
    regulation_ids = [_test_regulation(sf, storage, f"parallel-{n}") for n in range(4)]
    test_settings = settings.model_copy(update={
        "llm_provider": "vllm", "llm_model": "fake", "relevance_samples": 1, "prompt_experiment_parallel": 2,
    })
    fake, state = _tracking_llm(test_settings)
    monkeypatch.setattr(prompt_experiments, "get_llm", lambda _settings: fake)
    client = TestClient(create_app(test_settings, sf))

    created = client.post("/api/v1/evaluation/prompt-experiments", json=_experiment_body(client, regulation_ids, "B"))
    assert created.status_code == 202, created.text
    detail = client.get(f"/api/v1/evaluation/prompt-experiments/{created.json()['id']}").json()
    assert detail["status"] == "completed", detail["items"]
    assert all(item["A"] and item["B"] for item in detail["items"])
    assert state["calls"] == 8
    assert state["peak"] == 2


def test_prompt_experiment_limit_is_shared_across_experiments(settings, env, monkeypatch):  # noqa: F811
    from app.evaluation import prompt_experiments

    sf, storage = env
    regulation_ids = [_test_regulation(sf, storage, f"shared-{n}") for n in range(6)]
    test_settings = settings.model_copy(update={
        "llm_provider": "vllm", "llm_model": "fake", "relevance_samples": 1, "prompt_experiment_parallel": 2,
    })
    fake, state = _tracking_llm(test_settings)
    monkeypatch.setattr(prompt_experiments, "get_llm", lambda _settings: fake)
    run = prompt_experiments.run_prompt_experiment
    monkeypatch.setattr(prompt_experiments, "run_prompt_experiment", lambda *_args: None)   # deneyler elle koşulur
    client = TestClient(create_app(test_settings, sf))
    experiment_ids = [
        client.post("/api/v1/evaluation/prompt-experiments",
                    json=_experiment_body(client, regulation_ids[start:start + 3], marker)).json()["id"]
        for start, marker in ((0, "B1"), (3, "B2"))
    ]

    # her deney tek başına 2 kaydı birlikte işleyebilir; ortak sınır olmasa ikisi birlikte 4'e çıkardı
    workers = [threading.Thread(target=run, args=(sf, test_settings, eid)) for eid in experiment_ids]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert state["peak"] == 2
    assert state["calls"] == 12
    with sf() as session:
        assert all(session.get(PromptExperiment, eid).status == "completed" for eid in experiment_ids)


class _FakePgEngine:
    """``pg_try_advisory_lock`` / ``pg_advisory_unlock`` davranışını taklit eden en küçük motor."""

    dialect = SimpleNamespace(name="postgresql")

    def __init__(self):
        self.held: set[int] = set()
        self.guard = threading.Lock()

    def connect(self):
        engine = self

        class Connection:
            def scalar(self, statement, params):
                assert "pg_try_advisory_lock" in str(statement)
                with engine.guard:
                    if params["slot"] in engine.held:
                        return False
                    engine.held.add(params["slot"])
                    return True

            def execute(self, statement, params):
                assert "pg_advisory_unlock" in str(statement)
                with engine.guard:
                    engine.held.discard(params["slot"])

            def invalidate(self):
                pass

            def close(self):
                pass

        return Connection()


def test_postgres_experiment_slots_use_advisory_locks_and_wait_for_a_free_slot(monkeypatch):
    from app.evaluation import prompt_experiments

    monkeypatch.setattr(prompt_experiments, "_SLOT_POLL_S", 0.01)
    engine = _FakePgEngine()
    acquired = threading.Event()

    def third():
        with prompt_experiments.experiment_slot(engine, 2):
            acquired.set()

    with ExitStack() as first:
        first.enter_context(prompt_experiments.experiment_slot(engine, 2))
        with prompt_experiments.experiment_slot(engine, 2):
            assert engine.held == {0, 1}
            waiter = threading.Thread(target=third)
            waiter.start()
            assert not acquired.wait(0.1)   # iki yuva da dolu: üçüncü bekler
            first.close()                   # 0 numaralı yuva boşalır
            assert acquired.wait(2)
            waiter.join(timeout=2)
            assert engine.held == {1}
    assert engine.held == set()
