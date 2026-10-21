"""Yeni portal (Faz 5): ayar gerekçesi/geçmişi/önizleme, alarm kapatma, hat ölçümleri, tek tek yeniden deneme ve
yeniden işleme önizlemesi."""
from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.main import create_app
from app.db import Alert, AppSettingHistory, JobRun, LlmCall, Regulation, utcnow
from tests.test_api import _ready, client  # noqa: F401
from tests.test_portal_queues import _add
from tests.test_processing import env  # noqa: F401

ADMIN = "/api/v1/admin"


def test_settings_history_records_who_what_and_why(settings, env, client):  # noqa: F811
    c, sf = client
    r = c.put(f"{ADMIN}/settings", json={"values": {"relevance_threshold": "0.4", "relevance_samples": "5"},
                                         "reason": "TCMB duyuruları kaçıyordu."})
    assert r.status_code == 200
    c.put(f"{ADMIN}/settings", json={"values": {"relevance_threshold": "0.4"}, "reason": "Aynı değer."})   # değişmedi
    c.put(f"{ADMIN}/settings", json={"values": {"relevance_threshold": "0.35"}, "reason": "Biraz daha gevşet."})
    hist = c.get(f"{ADMIN}/settings/history").json()
    assert [(h["key"], h["oldValue"], h["newValue"]) for h in hist] == [
        ("relevance_threshold", "0.4", "0.35"), ("relevance_samples", "3", "5"), ("relevance_threshold", "0.3", "0.4")]
    assert hist[2]["reason"] == "TCMB duyuruları kaçıyordu." and hist[0]["changedBy"]
    assert [h["key"] for h in c.get(f"{ADMIN}/settings/history", params={"key": "relevance_samples"}).json()] == [
        "relevance_samples"]
    # geçersiz değer geçmişe yazılmaz
    assert c.put(f"{ADMIN}/settings", json={"values": {"relevance_threshold": "9"}}).status_code == 400
    assert len(c.get(f"{ADMIN}/settings/history").json()) == 3


def test_reason_is_required_only_when_the_flag_is_on(settings, env):  # noqa: F811
    sf, _ = env
    c = TestClient(create_app(settings.model_copy(update={"cors_origins": "", "require_setting_reason": True}), sf))
    no = c.put(f"{ADMIN}/settings", json={"values": {"relevance_threshold": "0.4"}})
    assert no.status_code == 400 and no.json()["error"]["code"] == "reason_required"
    ok = c.put(f"{ADMIN}/settings", json={"values": {"relevance_threshold": "0.4"}, "reason": "Deneme amaçlı."})
    assert ok.status_code == 200


def test_settings_preview_counts_flips_without_writing(settings, env, client):  # noqa: F811
    c, sf = client
    comp = {"model_score": 0.5, "vote_ratio": 1.0, "rules": 0.2, "model_uncertain": False, "split_votes": False,
            "has_text": True}
    with sf() as s:
        for i, (m, v, ru) in enumerate([(0.5, 1.0, 0.2), (0.2, 0.0, 0.0), (0.3, 0.0, 0.3)]):   # skorlar 0.76, 0.12, 0.27
            s.add(Regulation(title=f"k{i}", issuer="BDDK", primary_source_code="BDDK", canonical_key=f"k{i}",
                             processing_status="RELEVANT" if i == 0 else "IRRELEVANT", is_relevant=i == 0,
                             classified_at=utcnow(), relevance_score=0.5,
                             classification={"relevance": {**comp, "model_score": m, "vote_ratio": v, "rules": ru}}))
        s.add(Regulation(title="eski", issuer="BDDK", primary_source_code="BDDK", canonical_key="eski",
                         processing_status="IRRELEVANT", classified_at=utcnow() - timedelta(days=90),
                         classification={"relevance": comp}))
        s.commit()
    r = c.post(f"{ADMIN}/settings/preview", json={"values": {"relevance_threshold": "0.2"}}).json()
    assert r["evaluated"] == 3 and r["relevance"] == {"toRelevant": 1, "toIrrelevant": 0, "unchanged": 2}   # 0.27 ≥ 0.2
    assert r["changed"] == ["relevance_threshold"]
    up = c.post(f"{ADMIN}/settings/preview", json={"values": {"relevance_threshold": "0.9"}}).json()
    assert up["relevance"]["toIrrelevant"] == 1                                             # 0.76 < 0.9
    w = c.post(f"{ADMIN}/settings/preview", json={"values": {"relevance_samples": "6"}}).json()
    assert w["llm"]["relevanceSamples"] == {"current": 3, "new": 6}
    bad = c.post(f"{ADMIN}/settings/preview", json={"values": {"relevance_threshold": "x"}})
    assert bad.status_code == 400 and "relevance_threshold" in bad.json()["error"]["details"]["fields"]
    inconsistent = c.post(f"{ADMIN}/settings/preview", json={"values": {"confidence_medium": "0.9"}})
    assert inconsistent.status_code == 400
    assert c.get(f"{ADMIN}/settings").json()["relevance_threshold"] == 0.3                  # hiçbiri kaydedilmedi
    with sf() as s:
        assert s.scalars(select(AppSettingHistory)).all() == []


def test_alert_resolve_is_snoozed_while_the_finding_persists(settings, env):  # noqa: F811
    sf, _ = env
    c = TestClient(create_app(settings.model_copy(update={"cors_origins": ""}), sf))
    assert c.post(f"{ADMIN}/monitor/run").json()["opened"] == 12
    first = c.get(f"{ADMIN}/alerts").json()[0]
    assert first["link"] == "kaynaklar" and first["acknowledgedBy"] is None
    r = c.post(f"{ADMIN}/alerts/{first['id']}/resolve", json={"note": "Bilinen bakım."})
    assert r.status_code == 200 and r.json()["snoozeHours"] == 24
    assert c.post(f"{ADMIN}/alerts/{first['id']}/resolve", json={}).json()["error"]["code"] == "already_resolved"
    assert c.post(f"{ADMIN}/alerts/9999/resolve", json={}).status_code == 404
    again = c.post(f"{ADMIN}/monitor/run").json()                     # bulgu sürüyor ama bastırıldı
    assert again["opened"] == 0
    assert len(c.get(f"{ADMIN}/alerts").json()) == 11
    closed = c.get(f"{ADMIN}/alerts", params={"status": "closed"}).json()[0]
    assert closed["acknowledgedBy"] and closed["details"]["note"] == "Bilinen bakım."
    # süre dolunca yeniden açılır
    with sf() as s:
        a = s.get(Alert, first["id"])
        a.resolved_at = a.resolved_at - timedelta(hours=25)
        s.commit()
    assert c.post(f"{ADMIN}/monitor/run").json()["opened"] == 1


def test_retry_failed_accepts_ids_and_reprocess_dry_run(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, sf = client
    other = _add(sf, settings, "Hatalı", status="AI_FAILED", units=(), extra={"ai_attempts": 3, "ai_stage": "summary"})
    # LLM yapılandırılmamış ortam: yeniden deneme isteği 400; ama kayıt seçimi ids'e göre
    assert c.post(f"{ADMIN}/retry-failed", json={"ids": [other]}).json()["error"]["code"] == "llm_required"
    from app.monitoring.reprocess import failed_regulations
    with sf() as s:
        assert failed_regulations(s, ids=[other]) == {other: "summarize"} and failed_regulations(s, ids=[reg_id]) == {}
    est = c.post(f"{ADMIN}/reprocess", params={"dry_run": "true"},
                 json={"stage": "summarize", "from": "2020-01-01", "to": "2030-01-01"}).json()
    assert est["dryRun"] is True and est["count"] == 2 and est["llmCalls"] == 4 and est["estimate"] is True
    assert est["decidedKept"] == 0 and est["estimatedMinutes"] >= 1
    c.post(f"/api/v1/regulations/{reg_id}/decision", json={"decision": "approve"})
    est2 = c.post(f"{ADMIN}/reprocess", params={"dry_run": "true"},
                  json={"stage": "extract", "from": "2020-01-01", "to": "2030-01-01"}).json()
    assert est2["decidedKept"] == 1 and est2["llmCalls"] == 0
    with sf() as s:
        assert s.scalars(select(JobRun)).all() == []                       # önizleme iş kuyruğa almaz


def test_pipeline_node_metrics_and_review_flow(settings, env, client):  # noqa: F811
    c, sf = client
    _add(sf, settings, "Özet bekleyen", status="RELEVANT", units=(), detected_days_ago=5)
    _add(sf, settings, "Hatalı 1", status="AI_FAILED", units=(), extra={"ai_attempts": 3})
    _add(sf, settings, "Hatalı 2", status="AI_FAILED", units=(), extra={"ai_attempts": 1})
    ready = _add(sf, settings, "Hazır")
    with sf() as s:
        s.add(LlmCall(task="summary", provider="fake", model="m", prompt_version="v", input_hash="h", regulation_id=ready,
                      status="ok", created_at=utcnow()))
        s.add(LlmCall(task="summary", provider="fake", model="m", prompt_version="v", input_hash="h2",
                      regulation_id=ready, status="error", created_at=utcnow()))
        s.commit()
    c.post(f"/api/v1/regulations/{ready}/decision", json={"decision": "approve"})
    d = c.get("/api/v1/pipeline").json()
    nodes = {n["id"]: n for n in d["nodes"]}
    assert nodes["reg:RELEVANT"]["metrics"]["oldestDays"] == 5 and nodes["reg:RELEVANT"]["metrics"]["completed24h"] == 1
    assert nodes["reg:AI_FAILED"]["metrics"] == {"oldestDays": 0, "retryable": 1, "exhausted": 1}
    flow = d["reviewFlow"]
    assert flow["decided24h"] == 1 and flow["decided7d"] == 1 and flow["approved"] == 1 and flow["waiting"] == 0
