"""Yeni portal (Faz 1): kuyruklar, karar kilidi, çoklu filtre, öncelik sıralaması, liste alanları, toplu karar."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.ai.unit_matching import sync_units
from app.db import Regulation, Unit, UnitSuggestion, utcnow
from app.services.regulations import today
from tests.test_api import _ready, client  # noqa: F401
from tests.test_processing import env  # noqa: F401


def _add(sf, settings, title, *, severity="Orta", status="READY", effective=None, units=(("RISK_YONETIMI", 0.9),),
         detected_days_ago=0, extra=None, effective_text=None, dedupe=False):
    with sf() as s:
        if not s.scalar(select(Unit.code).limit(1)):
            sync_units(s, settings)
            s.commit()
        r = Regulation(title=title, issuer="BDDK", primary_source_code="BDDK", canonical_key=f"k:{title}",
                       processing_status=status, is_relevant=True, severity=severity, effective_date=effective,
                       effective_date_text=effective_text, extra=extra or {}, needs_dedupe_review=dedupe,
                       detected_at=utcnow() - timedelta(days=detected_days_ago), publish_date=today(settings))
        s.add(r)
        s.flush()
        for rank, (code, score) in enumerate(units, 1):
            s.add(UnitSuggestion(regulation_id=r.id, unit_code=code, rank=rank, score=score, reason="x", origin="ai"))
        s.commit()
        return r.id


def test_pending_queue_added_time_filter_and_preparing(settings, env, client):  # noqa: F811
    _ready(settings, env)
    c, sf = client
    old = _add(sf, settings, "Eski kayıt", detected_days_ago=20)
    recent = _add(sf, settings, "Yeni kayıt", detected_days_ago=2)
    prep = _add(sf, settings, "Özet bekleyen", status="RELEVANT", units=())
    failed = _add(sf, settings, "Hatalı", status="AI_FAILED", units=(),
                  extra={"ai_attempts": 3, "ai_stage": "summary", "ai_error": "zaman aşımı"})

    def ids(**params):
        return {i["id"] for i in c.get("/api/v1/regulations", params=params).json()["items"]}

    pending = ids(queue="tum")
    assert {old, recent} <= pending
    assert ids(queue="tum", detected_period="older") == {old}
    recently_added = ids(queue="tum", detected_period="recent")
    assert recent in recently_added and old not in recently_added
    exported = c.get("/api/v1/regulations/export", params={"queue": "tum", "detected_period": "older"})
    assert exported.status_code == 200 and exported.headers["X-Export-Total"] == "1"
    assert ids(queue="hazirlaniyor") == {prep, failed}
    assert c.get("/api/v1/regulations", params={"queue": "xx"}).json()["error"]["code"] == "validation_error"
    assert c.get("/api/v1/regulations", params={"detected_period": "xx"}).json()["error"]["code"] == "invalid_detected_period"
    st = c.get("/api/v1/regulations/stats").json()
    assert st["queues"] == {"tum": len(pending), "hazirlaniyor": 2, "ilgisizOrneklem": 0}
    assert st["preparing"]["failed"] == 1 and st["preparing"]["exhausted"] == 1 and st["preparing"]["retryable"] == 0
    assert st["severityDistribution"] == {"tum": {"Orta": len(pending)}}
    items = {i["id"]: i for i in c.get("/api/v1/regulations", params={"queue": "hazirlaniyor"}).json()["items"]}
    assert items[prep]["aiStage"]["stage"] == "summary" and items[prep]["canDecide"] is False
    assert items[failed]["aiStage"]["stage"] == "failed" and items[failed]["aiStage"]["error"] == "zaman aşımı"


def test_decision_is_locked_until_ai_is_ready(settings, env, client):  # noqa: F811
    c, sf = client
    reg_id = _add(sf, settings, "Hazır değil", status="SUMMARIZED")
    r = c.post(f"/api/v1/regulations/{reg_id}/decision", json={"decision": "approve"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_ready"
    assert c.get(f"/api/v1/regulations/{reg_id}").json()["canDecide"] is False
    with sf() as s:
        s.get(Regulation, reg_id).processing_status = "READY"
        s.commit()
    assert c.get(f"/api/v1/regulations/{reg_id}").json()["canDecide"] is True
    assert c.post(f"/api/v1/regulations/{reg_id}/decision", json={"decision": "approve"}).status_code == 200


def test_multi_value_filters_effective_and_needs_unit(settings, env, client):  # noqa: F811
    c, sf = client
    t = today(settings)
    soon = _add(sf, settings, "Yakın", severity="Kritik", effective=t + timedelta(days=3))
    mid = _add(sf, settings, "Orta vade", severity="Yüksek", effective=t + timedelta(days=20))
    forced = _add(sf, settings, "Yürürlükte", severity="Düşük", effective=t - timedelta(days=5),
                  units=(("RISK_YONETIMI", 0.2),))
    none = _add(sf, settings, "Tarihsiz", severity="Orta", units=())

    def ids(**p):
        return {i["id"] for i in c.get("/api/v1/regulations", params=p).json()["items"]}

    assert ids(effective="7") == {soon} and ids(effective="30") == {soon, mid}
    assert ids(effective="in_force") == {forced} and ids(effective="none") == {none}
    assert ids(effective="7,none") == {soon, none}
    assert ids(severity="Kritik,Düşük") == {soon, forced}
    assert ids(needs_unit="true") == {forced, none}          # zayıf (0,2 < 0,5) ve hiç öneri yok
    assert c.get("/api/v1/regulations", params={"effective": "x"}).json()["error"]["code"] == "invalid_effective"
    items = {i["id"]: i for i in c.get("/api/v1/regulations").json()["items"]}
    assert items[none]["needsUnit"] and items[forced]["needsUnit"] and not items[soon]["needsUnit"]


def test_priority_sort_orders_by_severity_times_urgency(settings, env, client):  # noqa: F811
    c, sf = client
    t = today(settings)
    a = _add(sf, settings, "Kritik ama uzak", severity="Kritik", effective=t + timedelta(days=200), detected_days_ago=30)
    b = _add(sf, settings, "Yüksek ve 3 gün", severity="Yüksek", effective=t + timedelta(days=3), detected_days_ago=30)
    d = _add(sf, settings, "Düşük", severity="Düşük", detected_days_ago=30)
    order = [i["id"] for i in c.get("/api/v1/regulations", params={"sort": "priority"}).json()["items"]]
    assert order == [b, a, d]                                  # 3×3=9 > 4×1=4 > 1×1=1
    asc = c.get("/api/v1/regulations", params={"sort": "priority", "direction": "asc"}).json()["items"]
    assert [i["id"] for i in asc] == [d, a, b]


def test_effective_conflict_and_possible_duplicate_fields(settings, env, client):  # noqa: F811
    c, sf = client
    t = today(settings)
    day = (t + timedelta(days=30)).strftime("%d.%m.%Y")
    reg_id = _add(sf, settings, "Çelişkili", effective=t + timedelta(days=30),
                  effective_text=f"{day} ve 08.02.2022 tarihli rehber", dedupe=True,
                  extra={"dedupe_candidates": [{"regulation_id": 99, "score": 0.82}]})
    clean = _add(sf, settings, "Temiz", effective=t + timedelta(days=30), effective_text=day)
    items = {i["id"]: i for i in c.get("/api/v1/regulations").json()["items"]}
    assert items[reg_id]["effectiveConflict"]["foundDates"] == ["2022-02-08"]
    assert items[reg_id]["possibleDuplicate"] == {"regulationId": 99, "score": 0.82}
    assert items[clean]["effectiveConflict"] is None and items[clean]["possibleDuplicate"] is None


def test_bulk_decision_partial_success(settings, env, client):  # noqa: F811
    c, sf = client
    ok1 = _add(sf, settings, "Birinci")
    ok2 = _add(sf, settings, "İkinci")
    notready = _add(sf, settings, "Hazır değil", status="SUMMARIZED")
    nounit = _add(sf, settings, "Birimsiz", units=())
    r = c.post("/api/v1/regulations/bulk-decision",
               json={"ids": [ok1, ok2, notready, nounit, 9999], "decision": "approve"})
    assert r.status_code == 200
    body = r.json()
    assert body["done"] == [ok1, ok2]
    assert {x["id"]: x["code"] for x in body["skipped"]} == {notready: "not_ready", nounit: "units_required",
                                                              9999: "not_found"}
    assert c.get(f"/api/v1/regulations/{ok1}").json()["status"] == "Onaylandı"
    assert c.get(f"/api/v1/regulations/{nounit}").json()["status"] == "Bekliyor"
    # tek birimle toplu onay: birimsiz kayıt da geçer ve birim düzeltme olarak kaydedilir
    r2 = c.post("/api/v1/regulations/bulk-decision", json={"ids": [nounit], "decision": "approve", "unit_codes": ["HAZINE"]})
    assert r2.json()["done"] == [nounit]
    assert c.get(f"/api/v1/regulations/{nounit}").json()["suggestedUnits"][0]["code"] == "HAZINE"
    # toplu ret: neden zorunlu
    bad = c.post("/api/v1/regulations/bulk-decision", json={"ids": [notready], "decision": "reject"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
