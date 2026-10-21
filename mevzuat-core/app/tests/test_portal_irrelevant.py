"""Yeni portal (Faz 4): ilgisiz kayıtların kontrolü (doğrula / ilgili say)."""
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


def test_list_includes_all_unreviewed_and_orders_by_score(settings, env, client):  # noqa: F811
    c, sf = client
    far = _irr(sf, "Çok ilgisiz", 0.02)                    # eşiğe uzak, yine de listede (örnekleme yapılmaz)
    near1 = _irr(sf, "Eşiğe yakın", 0.28)
    near2 = _irr(sf, "Biraz yakın", 0.18)
    unsure = _irr(sf, "Belirsiz", 0.05, uncertain=True)
    _irr(sf, "İncelenmiş", 0.29, reviewed=True)
    body = c.get(f"{API}/irrelevant-sample").json()
    assert [i["id"] for i in body["items"]] == [near1, near2, unsure, far] and body["total"] == 4
    assert body["threshold"] == settings.relevance_threshold and body["items"][0]["rationale"].endswith("ilgisiz.")
    assert c.get(f"{API}/stats").json()["queues"]["ilgisizOrneklem"] == 4
    page2 = c.get(f"{API}/irrelevant-sample", params={"page": 2, "page_size": 2}).json()
    assert [i["id"] for i in page2["items"]] == [unsure, far] and page2["total"] == 4


def test_irrelevant_sample_search_matches_title_and_rationale(settings, env, client):  # noqa: F811
    c, sf = client
    title_match = _irr(sf, "İzin Yönetmeliği", 0.22)
    rationale_match = _irr(sf, "Bilgi Notu", 0.20)
    with sf() as s:
        reg = s.get(Regulation, rationale_match)
        reg.classification = {"relevance": {"rationale": "Bankacılık işlemlerine ilişkin açıklama."}}
        s.commit()

    title_result = c.get(f"{API}/irrelevant-sample", params={"q": "IZIN"}).json()
    assert [item["id"] for item in title_result["items"]] == [title_match]
    assert title_result["total"] == 1

    rationale_result = c.get(f"{API}/irrelevant-sample", params={"q": "bankacılık"}).json()
    assert [item["id"] for item in rationale_result["items"]] == [rationale_match]
    assert rationale_result["total"] == 1
    assert c.get(f"{API}/irrelevant-sample", params={"q": "eşleşmeyen"}).json()["total"] == 0


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
