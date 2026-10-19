"""Arşiv ekranı: ilgisiz ve ilk tarama (BASELINE) kayıtlarının sayfalı sorgusu (/api/v1/archive/regulations)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.db import RegulationSourceLink, Regulation
from tests.test_processing import add_raw, env  # noqa: F401

N_IRRELEVANT, N_BASELINE = 130, 45


def _reg(status: str, i: int, **kw) -> Regulation:
    return Regulation(title=kw.pop("title", f"{status} düzenleme {i}"), issuer="BDDK", issuer_name="BDDK",
                      primary_source_code="BDDK", canonical_key=f"{status}:{i}", processing_status=status,
                      publish_date=kw.pop("publish_date", date(2026, 1, 1) + timedelta(days=i)), **kw)


@pytest.fixture
def client(settings, env):  # noqa: F811
    sf, storage = env
    with sf() as s:
        s.add_all(_reg("IRRELEVANT", i, is_relevant=False, relevance_score=i / 1000,
                       classification={"relevance": {"rationale": f"Gerekçe {i} " + "x" * 400,
                                                     "criteria": ["Bankacılık düzenlemesi değil"]}})
                  for i in range(N_IRRELEVANT))
        s.add_all(_reg("BASELINE", i) for i in range(N_BASELINE))
        s.add_all([_reg("READY", 1, is_relevant=True), _reg("MERGED", 1), _reg("NEW", 1)])   # arşivde görünmemeli
        s.commit()
    return TestClient(create_app(settings.model_copy(update={"cors_origins": ""}), sf)), sf, storage


def get(c, **params):
    r = c.get("/api/v1/archive/regulations", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_default_lists_irrelevant_only_with_counts_for_all_kinds(client):
    c, _, _ = client
    body = get(c)
    assert body["total"] == N_IRRELEVANT and body["counts"] == {"irrelevant": N_IRRELEVANT, "baseline": N_BASELINE,
                                                                "all": N_IRRELEVANT + N_BASELINE}
    assert {i["kind"] for i in body["items"]} == {"irrelevant"}
    assert body["pageSize"] == 25 and body["pages"] == 6 and len(body["items"]) == 25
    assert get(c, kind="baseline")["total"] == N_BASELINE
    assert get(c, kind="all")["total"] == N_IRRELEVANT + N_BASELINE


def test_pagination_covers_every_record_once_in_newest_first_order(client):
    c, _, _ = client
    seen, last_date = [], None
    for page in range(1, 4):
        body = get(c, page=page, page_size=50)
        assert body["pages"] == 3 and len(body["items"]) == (30 if page == 3 else 50)
        for it in body["items"]:
            assert last_date is None or it["publishDate"] <= last_date    # yayın tarihi azalan
            last_date = it["publishDate"]
            seen.append(it["id"])
    assert len(seen) == len(set(seen)) == N_IRRELEVANT
    assert get(c, page=4, page_size=50)["items"] == []                    # son sayfadan sonrası boş, hata değil


def test_page_limits(client):
    c, _, _ = client
    assert c.get("/api/v1/archive/regulations", params={"page_size": 101}).json()["error"]["code"] == "validation_error"
    assert c.get("/api/v1/archive/regulations", params={"page": 0}).status_code == 422
    r = c.get("/api/v1/archive/regulations", params={"page": 2001})
    assert r.status_code == 400 and r.json()["error"]["code"] == "page_too_deep"
    assert c.get("/api/v1/archive/regulations", params={"kind": "merged"}).status_code == 422


def test_search_dates_sort_and_rationale_preview(client):
    c, sf, _ = client
    with sf() as s:
        s.add(_reg("IRRELEVANT", 999, title="IBAN ışık İŞLEMLERİ", publish_date=None, is_relevant=False))
        s.commit()
    assert get(c, q="işlemleri")["total"] == 1 and get(c, q="IŞIK")["total"] == 1 and get(c, q="ISIK")["total"] == 0
    assert get(c, q="bddk")["total"] == N_IRRELEVANT + 1                  # kurum adında da arar
    assert get(c, q="%")["total"] == 0                                    # joker değil
    body = get(c, **{"from": "2026-01-01", "to": "2026-01-10"})
    assert body["total"] == 10 and body["counts"]["baseline"] == 10       # sekme sayıları süzgeçlere uyar
    undated = get(c, sort="publish_date", direction="desc", page_size=100, page=2)["items"]
    assert undated[-1]["publishDate"] is None                             # tarihsizler sona
    top = get(c, sort="score", direction="desc")["items"][0]
    assert top["relevanceScore"] == pytest.approx((N_IRRELEVANT - 1) / 1000)
    assert len(top["rationale"]) <= 321 and top["rationale"].endswith("…")


def test_source_filter_and_detail(client):
    c, sf, storage = client
    raw = add_raw(sf, storage, source="SPK", channel="x", external_id="1", url="https://spk.example/1", title="Kurul kararı")
    with sf() as s:
        reg = s.query(Regulation).filter_by(canonical_key="IRRELEVANT:7").one()
        s.add(RegulationSourceLink(regulation_id=reg.id, raw_document_id=raw, match_method="new"))
        s.commit()
        reg_id = reg.id
    body = get(c, source="SPK")
    assert body["total"] == 1 and body["items"][0]["id"] == reg_id
    assert body["items"][0]["sources"] == ["SPK"] and body["items"][0]["sourceUrl"] == "https://spk.example/1"
    assert get(c, source="KVKK")["total"] == 0
    d = c.get(f"/api/v1/archive/regulations/{reg_id}").json()
    assert d["rationale"].startswith("Gerekçe 7") and len(d["rationale"]) > 400      # tam gerekçe
    assert d["criteria"] == ["Bankacılık düzenlemesi değil"]
    assert d["documents"][0]["url"] == "https://spk.example/1"


def test_detail_only_for_archived_records_and_baseline_note(client):
    c, sf, _ = client
    with sf() as s:
        ids = {r.processing_status: r.id for r in s.query(Regulation).filter(
            Regulation.processing_status.in_(("READY", "MERGED", "BASELINE")))}
    assert c.get(f"/api/v1/archive/regulations/{ids['READY']}").status_code == 404     # portalda görünen kayıt arşivde değil
    assert c.get(f"/api/v1/archive/regulations/{ids['MERGED']}").status_code == 404
    assert c.get("/api/v1/archive/regulations/999999").status_code == 404
    d = c.get(f"/api/v1/archive/regulations/{ids['BASELINE']}").json()
    assert d["kind"] == "baseline" and "ilk taramasında" in d["note"] and d["relevanceScore"] is None


def test_archive_does_not_leak_into_portal_and_page_is_served(client):
    c, _, _ = client
    assert c.get("/api/v1/regulations").json()["total"] == 1          # yalnız READY; arşiv kayıtları portalda yok
    page = c.get("/arsiv")
    assert page.status_code == 200 and '<html lang="tr">' in page.text
    assert "fonts.googleapis.com" not in page.text and "cdn.jsdelivr" not in page.text


def test_archive_query_count_does_not_grow_with_page_size(client):
    """Satır başına sorgu yok: 100 satırlık sayfa da 25 satırlık sayfa kadar sorguyla gelir."""
    from sqlalchemy import event

    c, sf, _ = client
    engine = sf.kw["bind"]
    counter = {"n": 0}

    def count(*_):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", count)
    try:
        counter["n"] = 0
        get(c, page_size=25)
        small = counter["n"]
        counter["n"] = 0
        get(c, page_size=100)
        big = counter["n"]
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert small == big and big <= 4
