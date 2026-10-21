"""Yeni portal (Faz 3): kayıt bilgilerini düzenleme (başlık, tür, tarihler, özet, konular), YZ'nin ilk değerleri,
özet dayanak bağlantıları ve belge uç noktası."""
from __future__ import annotations

from sqlalchemy import select

from app.db import AuditEvent, Regulation, RegulationSummary
from tests.test_api import _ready, client  # noqa: F401
from tests.test_portal_queues import _add
from tests.test_processing import env  # noqa: F401

API = "/api/v1/regulations"


def test_edit_all_fields_keeps_ai_original_and_marks_edited(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, sf = client
    before = c.get(f"{API}/{reg_id}").json()
    assert before["editedFields"] == [] and before["aiOriginal"]["title"] == before["title"]
    r = c.patch(f"{API}/{reg_id}/content", json={
        "title": "Düzeltilmiş başlık", "reg_type": "Rehber", "publish_date": "2026-09-18",
        "effective": {"mode": "date", "date": "2026-11-01"}, "draft_deadline": "2026-10-30",
        "topics": ["Kripto varlıklar", "Müşteri hakları", "kripto varlıklar"], "note": "RG'deki haliyle."},
        headers={"If-Match": '"1"'})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["title"], d["type"], d["publishDate"], d["effectiveDate"], d["effectiveMode"]) == (
        "Düzeltilmiş başlık", "Rehber", "2026-09-18", "2026-11-01", "date")
    assert d["draftDeadline"] == "2026-10-30" and d["topics"] == ["Kripto varlıklar", "Müşteri hakları"]   # tekrar atıldı
    assert set(d["editedFields"]) == {"title", "type", "publishDate", "effective", "draftDeadline", "topics"}
    assert d["aiOriginal"]["title"] == before["title"] and d["aiOriginal"]["publishDate"] == "2026-09-19"
    assert d["aiOriginal"]["topics"] == before["topics"] and d["aiOriginal"]["effectiveDate"] == before["effectiveDate"]
    assert d["version"] == 2
    with sf() as s:
        ev = s.scalars(select(AuditEvent).where(AuditEvent.event_type == "content_edited")).one()
        assert {c_["field"] for c_ in ev.payload["changes"]} == {"title", "reg_type", "publish_date", "effective",
                                                                  "draft_deadline", "topics"}
        assert ev.payload["user_note"] == "RG'deki haliyle." and "Not: RG'deki haliyle." in ev.note
        assert s.scalars(select(Regulation)).one().extra["ai_original"]["title"] == before["title"]
        assert len(s.scalars(select(RegulationSummary)).all()) == 2          # konular yeni sürüm oldu
    # ikinci düzenleme ilk değerleri ezmez
    c.patch(f"{API}/{reg_id}/content", json={"title": "İkinci düzeltme"})
    assert c.get(f"{API}/{reg_id}").json()["aiOriginal"]["title"] == before["title"]
    assert c.get("/api/v1/admin/audit/verify").json()["ok"] is True


def test_reverting_to_the_ai_value_clears_the_edited_mark(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, _ = client
    orig = c.get(f"{API}/{reg_id}").json()["title"]
    c.patch(f"{API}/{reg_id}/content", json={"title": "Başka"})
    assert c.get(f"{API}/{reg_id}").json()["editedFields"] == ["title"]
    c.patch(f"{API}/{reg_id}/content", json={"title": orig})
    assert c.get(f"{API}/{reg_id}").json()["editedFields"] == []


def test_date_rules_and_validation(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, _ = client
    bad = c.patch(f"{API}/{reg_id}/content", json={"effective": {"mode": "date", "date": "2026-01-01"}})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_dates"      # yayımdan önce
    assert c.patch(f"{API}/{reg_id}/content", json={"effective": {"mode": "date"}}).status_code == 422
    assert c.patch(f"{API}/{reg_id}/content", json={"topics": ["x"] * 21}).status_code == 422
    assert c.patch(f"{API}/{reg_id}/content", json={"topics": ["u" * 121]}).json()["error"]["code"] == "invalid_topics"
    assert c.patch(f"{API}/{reg_id}/content", json={"reg_type": "  "}).json()["error"]["code"] in ("empty_content", "validation_error")
    assert c.patch(f"{API}/{reg_id}/content", json={}).json()["error"]["code"] == "empty_update"
    on_pub = c.patch(f"{API}/{reg_id}/content", json={"effective": {"mode": "on_publish"}}).json()
    assert on_pub["effectiveMode"] == "on_publish" and on_pub["effectiveDate"] == on_pub["publishDate"]
    moved = c.patch(f"{API}/{reg_id}/content", json={"publish_date": "2026-09-20"}).json()
    assert moved["effectiveDate"] == "2026-09-20" and moved["effectiveMode"] == "on_publish"   # yayımıyla birlikte kayar
    none = c.patch(f"{API}/{reg_id}/content", json={"effective": {"mode": "none"}}).json()
    assert none["effectiveMode"] == "none" and none["effectiveDate"] is None
    assert c.patch(f"{API}/{reg_id}/content", json={"draft_deadline": "2026-10-30"}).json()["draftDeadline"] == "2026-10-30"
    assert c.patch(f"{API}/{reg_id}/content", json={"draft_deadline": None}).json()["draftDeadline"] is None


def test_conflict_warning_is_silenced_by_confirmation(settings, env, client):  # noqa: F811
    c, sf = client
    reg_id = _add(sf, settings, "Çelişkili", effective=__import__("datetime").date(2026, 11, 7),
                  effective_text="7.11.2026 ve 08.02.2022")
    assert c.get(f"{API}/{reg_id}").json()["effectiveConflict"]["foundDates"] == ["2022-02-08"]
    r = c.patch(f"{API}/{reg_id}/content", json={"confirm_effective": True})
    assert r.status_code == 200 and r.json()["effectiveConflict"] is None
    assert r.json()["editedFields"] == []                       # yalnızca doğrulama: alan değişmedi


def test_summary_citations_and_manual_edit_drops_them(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, _ = client
    d = c.get(f"{API}/{reg_id}").json()
    cites = d["summaryCitations"]
    assert cites and cites[0]["n"] == 1 and cites[0]["passages"][0]["method"] == "exact"
    assert cites[0]["passages"][0]["charStart"] is not None
    edited = c.patch(f"{API}/{reg_id}/content", json={"summary": "Elle yazılmış özet."}).json()
    assert edited["summaryCitations"] == [] and "summary" in edited["editedFields"]
    assert edited["aiOriginal"]["summary"] == d["summary"]


def test_document_endpoint_returns_text_for_citations(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, sf = client
    cite = c.get(f"{API}/{reg_id}").json()["summaryCitations"][0]["passages"][0]
    doc = c.get(f"{API}/{reg_id}/document")
    assert doc.status_code == 200
    body = doc.json()
    assert body["rawDocumentId"] and body["text"] and body["truncated"] is False
    if cite["rawDocumentId"] == body["rawDocumentId"]:
        assert body["text"][cite["charStart"]:cite["charEnd"]].strip()
    assert c.get(f"{API}/9999/document").status_code == 404
