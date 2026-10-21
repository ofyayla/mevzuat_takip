"""Yeni portal (Faz 2): arşivde karar değiştirme, yeniden açma, düzenleme izni ve karar istatistikleri."""
from __future__ import annotations

from sqlalchemy import select

from app.db import AuditEvent, FewshotExample, Outbox
from tests.test_api import _ready, client  # noqa: F401
from tests.test_processing import env  # noqa: F401

API = "/api/v1/regulations"


def _approve(c, reg_id):
    return c.post(f"{API}/{reg_id}/decision", json={"decision": "approve"})


def test_revise_approve_to_reject_keeps_history_and_stops_learning(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, sf = client
    assert _approve(c, reg_id).status_code == 200
    r = c.post(f"{API}/{reg_id}/decision/revise", json={"decision": "reject", "rejection_reason": "duplicate",
                                                        "reason": "Aynı düzenleme başka kayıtta var."})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "Reddedildi" and d["decisionRevisions"] == 1
    last = d["auditTrail"][-1]
    assert last["action"] == "Karar değiştirildi" and "Onay · Risk Yönetimi Başkanlığı → Ret · duplicate" in last["note"]
    assert last["rejectionReason"] == "duplicate"      # portal ret gerekçesini düzeltilmiş kararda da gösterir
    with sf() as s:
        ev = s.scalars(select(AuditEvent).where(AuditEvent.event_type == "decision_revised")).one()
        assert ev.payload["from"]["status"] == "Onaylandı" and ev.payload["to"]["rejectionReason"] == "duplicate"
        assert ev.payload["reason"] == "Aynı düzenleme başka kayıtta var."
        assert not [e for e in s.scalars(select(FewshotExample)) if e.active and e.task == "unit_match"]
        assert s.scalars(select(Outbox).where(Outbox.event_type == "record.decision_revised")).one()
    assert c.get("/api/v1/admin/audit/verify").json()["ok"] is True


def test_revise_validation_and_optimistic_lock(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, _ = client
    body = {"decision": "reject", "rejection_reason": "duplicate", "reason": "Gerekçe yeterince uzun."}
    assert c.post(f"{API}/{reg_id}/decision/revise", json=body).json()["error"]["code"] == "not_decided"
    _approve(c, reg_id)
    assert c.post(f"{API}/{reg_id}/decision/revise", json={**body, "reason": "kısa"}).status_code == 422
    assert c.post(f"{API}/{reg_id}/decision/revise", json={"decision": "reject", "reason": "Gerekçe var."}
                  ).json()["error"]["code"] == "missing_rejection_reason"
    assert c.post(f"{API}/{reg_id}/decision/revise", json={"decision": "approve", "reason": "Aynı karar tekrar."}
                  ).json()["error"]["code"] == "no_change"
    stale = c.post(f"{API}/{reg_id}/decision/revise", json=body, headers={"If-Match": '"1"'})
    assert stale.status_code == 412 and stale.json()["error"]["details"]["current_version"] == 2
    ok = c.post(f"{API}/{reg_id}/decision/revise", json=body, headers={"If-Match": '"2"'})
    assert ok.status_code == 200 and ok.headers["etag"] == '"3"'
    same = c.post(f"{API}/{reg_id}/decision/revise", json=body)
    assert same.json()["error"]["code"] == "no_change"           # aynı ret nedeni
    other = c.post(f"{API}/{reg_id}/decision/revise", json={**body, "rejection_reason": "outdated"})
    assert other.status_code == 200 and other.json()["decisionRevisions"] == 2


def test_revise_units_keeps_ai_suggestion_and_filters(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, sf = client
    _approve(c, reg_id)
    assert c.get(API, params={"status": "Onaylandı", "unit_changed": "true"}).json()["total"] == 0
    r = c.post(f"{API}/{reg_id}/decision/revise", json={"decision": "approve", "unit_codes": ["HAZINE"],
                                                        "reason": "Birim görüşüne göre Hazine."})
    assert r.status_code == 200
    d = r.json()
    assert [u["code"] for u in d["suggestedUnits"]] == ["HAZINE"] and d["status"] == "Onaylandı"
    assert [u["code"] for u in d["aiSuggestedUnits"]] == ["RISK_YONETIMI"]
    assert all(u["isFinal"] for u in d["suggestedUnits"])
    assert c.get(API, params={"status": "Onaylandı", "unit_changed": "true"}).json()["total"] == 1
    with sf() as s:
        ex = [e for e in s.scalars(select(FewshotExample)) if e.active and e.task == "unit_match"]
        assert len(ex) == 1 and ex[0].origin == "user_correction" and ex[0].expected_output == {"units": ["HAZINE"]}
    st = c.get(f"{API}/decision-stats").json()
    assert st["approved"] == 1 and st["approvedWithChange"] == 1 and st["unitAccuracy"] == 0.0 and st["revisions"] == 1


def test_revise_reject_to_approve_requires_units_and_freezes(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, _ = client
    c.post(f"{API}/{reg_id}/decision", json={"decision": "reject", "rejection_reason": "outdated"})
    r = c.post(f"{API}/{reg_id}/decision/revise", json={"decision": "approve", "reason": "Yanlış reddedilmişti."})
    assert r.status_code == 200 and r.json()["status"] == "Onaylandı"
    assert r.json()["suggestedUnits"][0]["isFinal"] is True and r.json()["aiSuggestedUnits"][0]["code"] == "RISK_YONETIMI"
    assert c.get(f"{API}/decision-stats").json()["approvedAsSuggested"] == 1


def test_reopen_returns_record_to_the_queue(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, _ = client
    assert c.post(f"{API}/{reg_id}/decision/reopen", json={"reason": "Henüz karar yok."}).json()["error"]["code"] == "not_decided"
    _approve(c, reg_id)
    assert c.get(API, params={"queue": "tum"}).json()["total"] == 0
    r = c.post(f"{API}/{reg_id}/decision/reopen", json={"reason": "Birim itiraz etti."})
    d = r.json()
    assert r.status_code == 200 and d["status"] == "Bekliyor" and d["decidedBy"] is None and d["canDecide"] is True
    assert d["auditTrail"][-1]["action"] == "Karar kaldırıldı"
    assert c.get(API, params={"queue": "tum"}).json()["total"] == 1
    assert _approve(c, reg_id).status_code == 200                # yeniden karar verilebilir
    assert c.get(f"{API}/{reg_id}").json()["decisionRevisions"] == 1


def test_content_can_be_edited_after_a_decision(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, _ = client
    _approve(c, reg_id)
    r = c.patch(f"{API}/{reg_id}/content", json={"title": "Düzeltilmiş başlık"})
    assert r.status_code == 200 and r.json()["title"] == "Düzeltilmiş başlık"
    assert c.get(API, params={"status": "Onaylandı", "edited": "true"}).json()["total"] == 1
    assert c.get(API, params={"status": "Onaylandı", "sort": "decided_at"}).json()["total"] == 1
    # birim doğrudan değiştirilemez; yalnızca kararı değiştirme yoluyla
    assert c.put(f"{API}/{reg_id}/units", json={"unit_codes": ["HAZINE"]}).status_code == 409
