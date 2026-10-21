"""Yeni portal (Faz 4): ilgisiz kayıtların kontrol örneklemi (doğrula / ilgili say)."""
from __future__ import annotations

from sqlalchemy import select

from app.db import AuditEvent, FewshotExample, Regulation, utcnow
from tests.test_api import client  # noqa: F401
from tests.test_processing import env  # noqa: F401

API = "/api/v1/regulations"


def _irr(sf, title, score, *, uncertain=False, reviewed=False):
    with sf() as s:
        r = Regulation(title=title, issuer="BDDK", primary_source_code="BDDK", canonical_key=f"k:{title}",
                       processing_status="IRRELEVANT", is_relevant=False, relevance_score=score, detected_at=utcnow(),
                       classification={"relevance": {"rationale": f"{title} bankayla ilgisiz.", "uncertain": uncertain}},
                       extra={"irrelevant_review": {"verdict": "confirm"}} if reviewed else {})
        s.add(r)
        s.commit()
        return r.id


def test_sample_picks_near_threshold_unreviewed_and_orders_by_score(settings, env, client):  # noqa: F811
    c, sf = client
    far = _irr(sf, "Çok ilgisiz", 0.02)                    # eşiğe uzak: örnekleme girmez
    near1 = _irr(sf, "Eşiğe yakın", 0.28)
    near2 = _irr(sf, "Biraz yakın", 0.18)
    unsure = _irr(sf, "Belirsiz", 0.05, uncertain=True)     # düşük skor ama model belirsiz dedi
    _irr(sf, "İncelenmiş", 0.29, reviewed=True)
    body = c.get(f"{API}/irrelevant-sample").json()
    assert [i["id"] for i in body["items"]] == [near1, near2, unsure] and body["total"] == 3
    assert far not in {i["id"] for i in body["items"]}
    assert body["threshold"] == settings.relevance_threshold and body["items"][0]["rationale"].endswith("ilgisiz.")
    assert c.get(f"{API}/stats").json()["queues"]["ilgisizOrneklem"] == 3
    page2 = c.get(f"{API}/irrelevant-sample", params={"page": 2, "page_size": 2}).json()
    assert [i["id"] for i in page2["items"]] == [unsure] and page2["total"] == 3


def test_sample_size_is_capped_by_runtime_setting(settings, env, client):  # noqa: F811
    c, sf = client
    for i in range(5):
        _irr(sf, f"Aday {i}", 0.2 + i / 100)
    assert c.put("/api/v1/admin/settings", json={"values": {"irrelevant_sample_size": "2"}}).status_code == 200
    body = c.get(f"{API}/irrelevant-sample").json()
    assert body["total"] == 2 and len(body["items"]) == 2
    assert c.get(f"{API}/stats").json()["queues"]["ilgisizOrneklem"] == 2


def test_confirm_marks_reviewed_and_keeps_record_irrelevant(settings, env, client):  # noqa: F811
    c, sf = client
    rid = _irr(sf, "Doğrulanacak", 0.25)
    r = c.post(f"{API}/{rid}/irrelevant-review", json={"verdict": "confirm", "note": "Gerçekten ilgisiz."})
    assert r.status_code == 200 and r.json()["processingStatus"] == "IRRELEVANT"
    assert c.get(f"{API}/irrelevant-sample").json()["total"] == 0
    with sf() as s:
        reg = s.get(Regulation, rid)
        assert reg.extra["irrelevant_review"]["verdict"] == "confirm" and reg.processing_status == "IRRELEVANT"
        assert s.scalars(select(AuditEvent).where(AuditEvent.event_type == "irrelevant_confirmed")).one().note.endswith(
            "Not: Gerçekten ilgisiz.")
        ex = s.scalars(select(FewshotExample)).one()
        assert (ex.task, ex.origin, ex.expected_output) == ("relevance", "user_decision", {"is_relevant": False})
    item = c.get(f"/api/v1/archive/regulations/{rid}").json()
    assert item["irrelevantReview"]["verdict"] == "confirm"
    assert c.post(f"{API}/{rid}/irrelevant-review", json={"verdict": "confirm"}).json()["error"]["code"] == "already_reviewed"


def test_relevant_verdict_returns_record_to_the_ai_pipeline(settings, env, client):  # noqa: F811
    c, sf = client
    rid = _irr(sf, "Yanlış elenmiş", 0.27)
    with sf() as s:
        before = s.get(Regulation, rid).detected_at
    r = c.post(f"{API}/{rid}/irrelevant-review", json={"verdict": "relevant"})
    assert r.status_code == 200 and r.json()["processingStatus"] == "RELEVANT"
    with sf() as s:
        reg = s.get(Regulation, rid)
        assert reg.is_relevant is True and reg.extra["irrelevant_review"]["original_detected_at"]
        assert reg.detected_at >= before
        ex = s.scalars(select(FewshotExample)).one()
        assert (ex.origin, ex.weight, ex.expected_output) == ("user_correction", 2.0, {"is_relevant": True})
    # portalda artık "YZ hazırlıyor" kuyruğunda görünür ve karar kilitli
    prep = c.get(API, params={"queue": "hazirlaniyor"}).json()["items"]
    assert [i["id"] for i in prep] == [rid] and prep[0]["canDecide"] is False
    assert c.get(f"{API}/irrelevant-sample").json()["total"] == 0
    assert c.get("/api/v1/admin/audit/verify").json()["ok"] is True


def test_review_validation(settings, env, client):  # noqa: F811
    c, sf = client
    assert c.post(f"{API}/9999/irrelevant-review", json={"verdict": "confirm"}).status_code == 404
    assert c.post(f"{API}/1/irrelevant-review", json={"verdict": "x"}).status_code == 422
