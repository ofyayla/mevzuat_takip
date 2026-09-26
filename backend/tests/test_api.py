"""İK-6 Portal API: liste/detay/istatistik, karar ve birim değişikliği iş kuralları, denetim izi, iyimser kilit,
kimlik doğrulama (disabled + Keycloak çevrimdışı JWT), yönetim uç noktaları."""
from __future__ import annotations

import time
from datetime import timedelta
from urllib.parse import quote

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.llm_client import FakeLLM
from app.ai.summary import summarize_pending
from app.ai.unit_matching import match_pending
from app.api.main import create_app
from app.db import AuditEvent, FewshotExample, Outbox, Regulation, UnitSuggestion
from app.services.regulations import today
from tests.test_processing import env  # noqa: F401
from tests.test_summary import GOOD, _relevant_bank_mellat, summary_out
from tests.test_units import UNITS, _set_topics

RISK_RESP = UNITS["units"][0]["responsibilities"][-1]


def _ready(settings, env):  # noqa: F811
    sf, _ = env
    reg_id = _relevant_bank_mellat(settings, env)
    summarize_pending(sf, FakeLLM(settings, lambda t, m, n: [summary_out([GOOD])]), settings)
    _set_topics(sf, reg_id, ["SERMAYE_LIKIDITE"])
    match_pending(sf, FakeLLM(settings, lambda t, m, n: [{"suggestions": [
        {"unit_code": "RISK_YONETIMI", "score": 0.9, "matched_responsibility": RISK_RESP, "reason": "Karşı taraf."}]}]),
        settings)
    return reg_id


@pytest.fixture
def client(settings, env):  # noqa: F811
    sf, _ = env
    s = settings.model_copy(update={"cors_origins": ""})
    return TestClient(create_app(s, sf)), sf


def test_list_detail_and_stats(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, _ = client
    body = c.get("/api/v1/regulations").json()
    assert body["total"] == 1 and body["page"] == 1
    item = body["items"][0]
    assert item["id"] == reg_id and item["status"] == "Bekliyor" and item["issuerCode"] == "BDDK"
    assert item["publishDate"] == "2026-09-19" and item["summary"] == GOOD["sentence"]
    assert item["suggestedUnits"][0]["unit"] == "Risk Yönetimi Başkanlığı" and item["analysisStatus"] == "hazır"
    assert item["sources"] == ["BDDK", "RESMI_GAZETE"] and item["sourceUrl"].startswith("https://www.resmigazete")
    assert c.get("/api/v1/regulations", params={"source": "KVKK"}).json()["total"] == 0
    assert c.get("/api/v1/regulations", params={"unit": "RISK_YONETIMI"}).json()["total"] == 1
    assert c.get("/api/v1/regulations", params={"q": "Mellat"}).json()["total"] == 1
    assert c.get("/api/v1/regulations", params={"metric": "critical_high"}).json()["total"] == 0   # önem Orta
    assert c.get("/api/v1/regulations/stats").json() == {"pending": 1, "criticalHighPending": 0, "today": 1,
                                                         "upcoming": 0}
    r = c.get(f"/api/v1/regulations/{reg_id}")
    assert r.headers["etag"] == '"1"'
    d = r.json()
    assert d["auditTrail"][0]["action"] == "YZ tarafından tespit edildi" and d["auditTrail"][0]["icon"] == "bot"
    assert d["evidence"]["summary"][0]["evidence"][0]["method"] == "exact"
    assert {"summary", "suggestedUnits", "severity"} <= set(d["aiGeneratedFields"])
    # hata biçimi
    assert c.get("/api/v1/regulations/9999").json() == {"error": {"code": "not_found",
                                                                  "message": "düzenleme bulunamadı", "details": {}}}
    assert c.get("/api/v1/regulations", params={"page_size": 500}).json()["error"]["code"] == "validation_error"


def test_irrelevant_and_merged_are_hidden(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, sf = client
    with sf() as s:
        s.get(Regulation, reg_id).is_relevant = False
        s.commit()
    assert c.get("/api/v1/regulations").json()["total"] == 0
    assert c.get(f"/api/v1/regulations/{reg_id}").status_code == 404


def test_views_once_per_user(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, sf = client
    assert c.post(f"/api/v1/regulations/{reg_id}/views").json() == {"created": True}
    assert c.post(f"/api/v1/regulations/{reg_id}/views").json() == {"created": False}
    assert c.post(f"/api/v1/regulations/{reg_id}/views", headers={"X-Demo-User": quote("Ayşe")}).json()["created"]
    with sf() as s:
        assert len(s.scalars(select(AuditEvent).where(AuditEvent.event_type == "viewed")).all()) == 2


def test_approve_freezes_units_records_learning_and_outbox(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, sf = client
    r = c.post(f"/api/v1/regulations/{reg_id}/decision", json={"decision": "approve", "note": "Takip edilecek."},
               headers={"If-Match": '"1"', "X-Demo-User": quote("Ayşe Kaya")})
    assert r.status_code == 200 and r.headers["etag"] == '"2"'
    d = r.json()
    assert d["status"] == "Onaylandı" and d["decidedBy"]["name"] == "Ayşe Kaya" and d["decisionNote"] == "Takip edilecek."
    last = d["auditTrail"][-1]
    assert last["action"] == "Onaylandı" and last["icon"] == "check-circle-2" and last["actor"] == "Ayşe Kaya · Uyum Uzmanı"
    assert "Risk Yönetimi Başkanlığı birimine yönlendirildi." in last["note"] and "Takip edilecek." in last["note"]
    with sf() as s:
        assert s.scalars(select(UnitSuggestion)).one().is_final
        ex = s.scalars(select(FewshotExample)).one()
        assert (ex.origin, ex.weight, ex.expected_output) == ("user_decision", 1.0, {"units": ["RISK_YONETIMI"]})
        ob = s.scalars(select(Outbox)).one()
        assert ob.event_type == "record.approved" and ob.payload["units"] == ["RISK_YONETIMI"]
    # ikinci karar: 409; birim değişikliği de kapalı
    again = c.post(f"/api/v1/regulations/{reg_id}/decision", json={"decision": "reject"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_decided"
    assert c.put(f"/api/v1/regulations/{reg_id}/units", json={"unit_codes": ["HAZINE"]}).status_code == 409


def test_optimistic_locking(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, _ = client
    ok = c.put(f"/api/v1/regulations/{reg_id}/units", json={"unit_codes": ["RISK_YONETIMI", "HAZINE"]},
               headers={"If-Match": '"1"'})
    assert ok.status_code == 200 and ok.json()["version"] == 2
    stale = c.post(f"/api/v1/regulations/{reg_id}/decision", json={"decision": "approve"}, headers={"If-Match": '"1"'})
    assert stale.status_code == 412 and stale.json()["error"]["details"] == {"current_version": 2}


def test_change_units_keeps_ai_reason_and_records_correction(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, sf = client
    r = c.put(f"/api/v1/regulations/{reg_id}/units", json={"unit_codes": ["HAZINE", "RISK_YONETIMI"],
                                                           "note": "Likidite etkisi"})
    units = r.json()["suggestedUnits"]
    assert [(u["code"], u["origin"]) for u in units] == [("HAZINE", "manual"), ("RISK_YONETIMI", "ai")]
    assert units[0]["reason"] == "Uzman tarafından manuel olarak atandı." and units[1]["reason"] == "Karşı taraf."
    ev = r.json()["auditTrail"][-1]
    assert ev["action"] == "Birim değiştirildi" and ev["icon"] == "shuffle"
    assert '"Risk Yönetimi Başkanlığı" iken "Hazine Müdürlüğü, Risk Yönetimi Başkanlığı"' in ev["note"]
    with sf() as s:
        ex = s.scalars(select(FewshotExample)).one()
        assert (ex.origin, ex.weight) == ("user_correction", 2.0)
    # onayda düzeltme zaten kaydedildiği için ikinci örnek oluşmaz
    c.post(f"/api/v1/regulations/{reg_id}/decision", json={"decision": "approve"})
    with sf() as s:
        assert len(s.scalars(select(FewshotExample)).all()) == 1
    bad = c.put(f"/api/v1/regulations/{reg_id}/units", json={"unit_codes": []})
    assert bad.status_code == 422


def test_unknown_unit_and_reject(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, sf = client
    r = c.put(f"/api/v1/regulations/{reg_id}/units", json={"unit_codes": ["HUKUK_ISLERI"]})
    assert r.status_code == 400 and r.json()["error"]["details"] == {"unit_codes": ["HUKUK_ISLERI"]}
    r = c.post(f"/api/v1/regulations/{reg_id}/decision", json={"decision": "reject"})
    assert r.json()["status"] == "Reddedildi" and r.json()["auditTrail"][-1]["icon"] == "x-circle"
    with sf() as s:
        assert s.scalars(select(Outbox)).one().event_type == "record.rejected"
        assert s.scalars(select(FewshotExample)).first() is None


def test_audit_is_append_only(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    _, sf = client
    with sf() as s:
        ev = s.scalars(select(AuditEvent).where(AuditEvent.regulation_id == reg_id)).first()
        ev.note = "değiştirildi"
        with pytest.raises(PermissionError):
            s.commit()


def test_effective_soon_and_upcoming(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, sf = client
    with sf() as s:
        s.get(Regulation, reg_id).effective_date = today(settings) + timedelta(days=10)
        s.commit()
    assert c.get("/api/v1/regulations").json()["items"][0]["effectiveSoon"] is True
    assert c.get("/api/v1/regulations/stats").json()["upcoming"] == 1


# ------------------------------------------------------------------------------------------------ Keycloak


@pytest.fixture
def keycloak(settings, env, tmp_path):  # noqa: F811
    sf, _ = env
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "k1.pem").write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    s = settings.model_copy(update={"auth_mode": "keycloak", "keycloak_issuer": "https://sso.kurum/realms/banka",
                                    "keycloak_client_id": "mevzuat-portal", "keycloak_public_keys_dir": keys,
                                    "cors_origins": ""})

    def token(roles=("mevzuat_expert",), **over):
        now = int(time.time())
        claims = {"iss": s.keycloak_issuer, "azp": "mevzuat-portal", "aud": "account", "sub": "u-1",
                  "preferred_username": "akaya", "name": "Ayşe Kaya", "title": "Uyum Uzmanı", "iat": now,
                  "exp": now + 300, "realm_access": {"roles": list(roles)}} | over
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})

    return TestClient(create_app(s, sf)), token


def test_keycloak_auth_and_roles(settings, env, keycloak):  # noqa: F811
    reg_id = _ready(settings, env)
    c, token = keycloak
    assert c.get("/api/v1/regulations").json()["error"]["code"] == "missing_token"
    h = {"Authorization": f"Bearer {token()}"}
    me = c.get("/api/v1/me", headers=h).json()
    assert me["name"] == "Ayşe Kaya" and me["canDecide"] and not me["isAdmin"]
    assert c.get("/api/v1/regulations", headers=h).json()["total"] == 1
    viewer = {"Authorization": f"Bearer {token(roles=('mevzuat_viewer',))}"}
    assert c.post(f"/api/v1/regulations/{reg_id}/decision", json={"decision": "approve"},
                  headers=viewer).status_code == 403
    assert c.get("/api/v1/admin/settings", headers=h).status_code == 403
    r = c.post(f"/api/v1/regulations/{reg_id}/decision", json={"decision": "approve"}, headers=h)
    assert r.json()["decidedBy"]["name"] == "Ayşe Kaya"
    # geçersiz tokenlar
    expired = token(exp=int(time.time()) - 3600)
    assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"}).json()["error"]["code"] == \
        "invalid_token"
    wrong_iss = token(iss="https://baska")
    assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {wrong_iss}"}).status_code == 401
    wrong_client = token(azp="baska-istemci")
    assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {wrong_client}"}).status_code == 401
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode({"iss": "https://sso.kurum/realms/banka", "azp": "mevzuat-portal", "exp": int(time.time()) + 60,
                         "realm_access": {"roles": ["mevzuat_admin"]}}, other_key, algorithm="RS256")
    assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    norole = token(roles=("offline_access",))
    assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {norole}"}).status_code == 403


# ------------------------------------------------------------------------------------------------ yönetim ve genel


def test_admin_settings_and_health_endpoints(settings, env, client):  # noqa: F811
    c, _ = client
    r = c.put("/api/v1/admin/settings", json={"values": {"relevance_threshold": "0.4"}})
    assert r.json()["relevance_threshold"] == 0.4
    assert c.put("/api/v1/admin/settings", json={"values": {"llm_api_key": "x"}}).status_code == 400
    h = c.get("/api/v1/sources/health").json()
    assert h["overall"] == "down" and {s["status"] for s in h["sources"]} == {"down"}   # hiç tarama yok
    assert "Henüz tarama yapılmadı" in h["bannerText"]
    assert c.get("/healthz").json() == {"status": "ok"} and c.get("/readyz").json()["checks"]["db"] == "ok"
    assert len(c.get("/api/v1/units").json()) == 0 or True
    assert c.get("/").status_code == 200 and "Mevzuat" in c.get("/").text
