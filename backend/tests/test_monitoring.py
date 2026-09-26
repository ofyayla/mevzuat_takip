"""İK-7: iş günü takvimi, kaynak sağlığı kontrolleri, çapraz kontrol, alarm yaşam döngüsü, backfill, reprocess."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.llm_client import FakeLLM
from app.api.main import create_app
from app.db import Alert, AuditEvent, FetchRun, RawDocument, Regulation, RegulationSourceLink, Source
from app.monitoring import alerts as alerts_mod
from app.monitoring.calendar import business_hours_between, is_business_day
from app.monitoring.checks import check_source
from app.monitoring.reprocess import backfill, reprocess
from app.monitoring.service import health_summary, run_monitor
from tests.conftest import DictFetcher
from tests.test_ai import rel, sev
from tests.test_processing import env  # noqa: F401
from tests.test_resmi_gazete import BASE, fihrist

UTC = timezone.utc
MON = datetime(2026, 9, 28, 6, tzinfo=UTC)          # Pazartesi 09:00 TR


def test_business_calendar():
    assert not is_business_day(date(2026, 10, 29)) and not is_business_day(date(2026, 5, 28))   # 29 Ekim, bayram
    assert not is_business_day(date(2026, 9, 26)) and is_business_day(date(2026, 9, 25))        # Ctesi / Cuma
    fri_18 = datetime(2026, 9, 25, 15, tzinfo=UTC)
    assert business_hours_between(fri_18, MON) == 15.0                                           # 6 + 9 saat
    # Kurban Bayramı haftası: 26 Mayıs (arife, iş günü) → 1 Haziran; bayram günleri sayılmaz
    assert business_hours_between(datetime(2026, 5, 26, 21, tzinfo=UTC), datetime(2026, 6, 1, 21, tzinfo=UTC)) == 24


# ------------------------------------------------------------------------------------------------ yardımcılar


def add_run(sf, code, started, status="success", channels=None, errors=None, items=0, baseline=False):
    with sf() as s:
        if s.get(Source, code) is None:
            s.add(Source(code=code, name=code))
            s.flush()   # ilişki tanımı yok: üst kayıt önce yazılmalı (yabancı anahtar denetimi açık)
        run = FetchRun(source_code=code, started_at=started, finished_at=started, status=status,
                       channels=channels or {"k": {"items": 5}}, errors=errors or [])
        s.add(run)
        s.flush()
        for i in range(items):
            s.add(RawDocument(source_code=code, channel="k", external_id=f"{run.id}-{i}", url=f"https://x/{run.id}/{i}",
                              title="t", content_type="text/html", content_sha256="0" * 64, size_bytes=1,
                              storage_key="k", fetch_run_id=run.id, fetched_at=started, is_baseline=baseline))
        s.commit()


def status(settings, sf, code, now):
    from app.collectors.config import load_sources

    with sf() as s:
        return check_source(s, load_sources(settings.sources_file).get(code), settings, now)


# ------------------------------------------------------------------------------------------------ kontroller


def test_fetch_errors(settings, env):  # noqa: F811
    sf, _ = env
    assert status(settings, sf, "BDDK", MON).status == "down"                                  # hiç tarama yok
    add_run(sf, "BDDK", MON - timedelta(hours=5), items=1)
    add_run(sf, "BDDK", MON - timedelta(hours=1), status="failed", errors=["bağlantı"])
    st = status(settings, sf, "BDDK", MON)
    assert st.status == "delayed" and "Son tarama başarısız" in st.message
    add_run(sf, "BDDK", MON - timedelta(minutes=40), status="failed")
    add_run(sf, "BDDK", MON - timedelta(minutes=20), status="failed", errors=["zaman aşımı"])
    st = status(settings, sf, "BDDK", MON)
    assert st.status == "down" and st.message.startswith("Son 3 tarama başarısız")


def test_silence_respects_business_days(settings, env):  # noqa: F811
    sf, _ = env
    fri = datetime(2026, 9, 18, 15, tzinfo=UTC)                                                  # Cuma 18:00
    add_run(sf, "BDDK", fri, items=2)
    for h in range(6, 24 * 10, 12):                                                             # boş taramalar
        add_run(sf, "BDDK", fri + timedelta(hours=h))
    # tolerans 120 iş saati; Cuma 18:00 → Pazartesi 09:00 yalnızca 15 iş saati
    assert status(settings, sf, "BDDK", datetime(2026, 9, 21, 6, tzinfo=UTC)).status == "ok"
    # 28.09 Pazartesi 09:00: Cuma 6 + beş iş günü 120 + Pazartesi 9 = 135 iş saati (> 120); hafta sonları sayılmaz
    st = status(settings, sf, "BDDK", MON)
    assert st.status == "delayed" and st.findings[0].alert_type == "silence"
    assert st.findings[0].details["elapsed_hours"] == 135.0 and "iş saati" in st.message


def test_learned_tolerance_from_history(settings, env):  # noqa: F811
    sf, _ = env
    start = datetime(2026, 8, 1, 7, tzinfo=UTC)
    for d in range(45):                                                                          # RG: her gün yayın
        add_run(sf, "RESMI_GAZETE", start + timedelta(days=d), items=3)
    now = start + timedelta(days=44, hours=26)                                                  # son yayından 26 sa
    st = status(settings, sf, "RESMI_GAZETE", now)
    assert st.tolerance_basis.startswith("geçmiş p95") and st.tolerance_hours == 24.0
    assert st.status == "delayed"                                                                # yapılandırma: 30 sa


def test_volume_low(settings, env):  # noqa: F811
    sf, _ = env
    start = MON - timedelta(weeks=9)
    for d in range(0, 7 * 8, 1):                                                                 # 8 hafta, günde 2
        add_run(sf, "KVKK", start + timedelta(days=d), items=2)
    for d in range(56, 63):                                                                       # son hafta boş
        add_run(sf, "KVKK", start + timedelta(days=d))
    st = status(settings, sf, "KVKK", MON)
    vol = [f for f in st.findings if f.alert_type == "volume_low"]
    assert vol and vol[0].details["last_7_days"] == 0 and vol[0].details["history_mean"] == 14


def test_structure_alerts(settings, env):  # noqa: F811
    sf, _ = env
    add_run(sf, "TKBB", MON - timedelta(hours=2), items=1)
    add_run(sf, "TKBB", MON - timedelta(hours=1), channels={
        "duyurular": {"items": 0, "empty_listing": True},
        "birlik_duzenlemeleri": {"items": 30, "structure_changed": True, "signature": "abc"}})
    st = status(settings, sf, "TKBB", MON)
    kinds = {(f.key, f.severity) for f in st.findings}
    assert ("structure_change:TKBB:duyurular", "delayed") in kinds          # sessiz bozulma → gecikmeli
    assert ("structure_change:TKBB:birlik_duzenlemeleri", "warning") in kinds  # erken uyarı, durumu bozmaz
    assert st.status == "delayed"


# ------------------------------------------------------------------------------------------------ çapraz kontrol


def _reg(sf, *, title, issuer, sources, publish, rg_date=None, reg_type="Yönetmelik"):
    with sf() as s:
        for code in sources:
            if s.get(Source, code) is None:
                s.add(Source(code=code, name=code))
        reg = Regulation(title=title, issuer=issuer, reg_type=reg_type, publish_date=publish,
                         primary_source_code=sources[0], canonical_key=title, processing_status="READY",
                         extra={"rg_date": rg_date} if rg_date else {})
        s.add(reg)
        s.flush()
        for i, code in enumerate(sources):
            raw = RawDocument(source_code=code, channel="k", external_id=f"{title}-{code}", url=f"https://{code}/{i}",
                              title=title, content_type="text/html", content_sha256="0" * 64, size_bytes=1,
                              storage_key="k", regulation_id=reg.id)
            s.add(raw)
            s.flush()
            s.add(RegulationSourceLink(regulation_id=reg.id, raw_document_id=raw.id, match_method="new"))
        s.commit()
        return reg.id


def test_cross_check_both_directions_and_alert_lifecycle(settings, env, monkeypatch):  # noqa: F811
    sf, _ = env
    sent = []
    monkeypatch.setattr(alerts_mod.httpx, "post", lambda url, json, timeout: sent.append(json))
    s2 = settings.model_copy(update={"alert_webhook_url": "https://izleme.kurum/hook"})
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    missing = _reg(sf, title="Bankaların Kredi İşlemlerine İlişkin Yönetmelikte Değişiklik", issuer="BDDK",
                   sources=["RESMI_GAZETE"], publish=date(2026, 9, 22))
    _reg(sf, title="Yeni kurul kararı (dün)", issuer="BDDK", sources=["RESMI_GAZETE"], publish=date(2026, 9, 24))
    rg_missing = _reg(sf, title="SPK Tebliği", issuer="SPK", sources=["SPK"], publish=date(2026, 9, 20),
                      rg_date="2026-09-21", reg_type="Tebliğ")
    _reg(sf, title="Ticaret tebliği", issuer="TICARET", sources=["RESMI_GAZETE"], publish=date(2026, 9, 20),
         reg_type="Tebliğ")                                                     # varsayılan çapraz kontrol dışı
    with sf() as s:
        res = run_monitor(s, s2, now)
        s.commit()
    cc = {f.key for f in res.findings if f.alert_type == "cross_check_miss"}
    assert cc == {f"cross_check_miss:BDDK:reg:{missing}", f"cross_check_miss:RESMI_GAZETE:reg:{rg_missing}"}
    assert {p["key"] for p in sent if p["event"] == "alert.opened"} >= cc
    # kurum kopyası geldi → alarm kapanır; ikinci çalıştırmada süren alarmlar güncellenir, tekrar açılmaz
    with sf() as s:
        raw = RawDocument(source_code="BDDK", channel="k", external_id="bddk-copy", url="https://BDDK/copy",
                          title="kopya", content_type="text/html", content_sha256="0" * 64, size_bytes=1,
                          storage_key="k", regulation_id=missing)
        s.add(raw)
        s.flush()
        s.add(RegulationSourceLink(regulation_id=missing, raw_document_id=raw.id, match_method="exact_key"))
        s.commit()
        res = run_monitor(s, s2, now + timedelta(minutes=10))
        s.commit()
        closed = s.scalars(select(Alert).where(Alert.key == f"cross_check_miss:BDDK:reg:{missing}")).one()
        assert closed.resolved_at is not None
        assert s.scalars(select(Alert).where(Alert.key == f"cross_check_miss:RESMI_GAZETE:reg:{rg_missing}",
                                             Alert.resolved_at.is_(None))).one().last_seen_at is not None
    assert any(p["event"] == "alert.resolved" for p in sent) and len(res.sync.opened) == 0


def test_ai_failure_and_health_summary(settings, env):  # noqa: F811
    sf, _ = env
    with sf() as s:
        s.add(Regulation(title="x", primary_source_code="BDDK", canonical_key="x", processing_status="AI_FAILED",
                         extra={"ai_attempts": 3, "ai_stage": "summary"}))
        s.commit()
        h = health_summary(s, settings, MON)
    assert h["overall"] == "down" and "Henüz tarama yapılmadı" in h["bannerText"]
    assert any(w["type"] == "ai_failure" and "summary" in w["message"] for w in h["warnings"])


# ------------------------------------------------------------------------------------------------ backfill / reprocess


def test_backfill_resmi_gazete_day_by_day(settings, env):  # noqa: F811
    sf, _ = env
    pages = {}
    for d in ("2026-09-20", "2026-09-21", "2026-09-22"):
        k = d.replace("-", "")
        pages[f"{BASE}/fihrist?tarih={d}"] = (fihrist((f"{BASE}/eskiler/{d[:4]}/{d[5:7]}/{k}-1.htm", f"Karar {d}")),
                                              "text/html")
        pages[f"{BASE}/fihrist?tarih={d}&mukerrer=1"] = (fihrist(), "text/html", 200, f"{BASE}/")
        pages[f"{BASE}/eskiler/{d[:4]}/{d[5:7]}/{k}-1.htm"] = ("<html><body>metin</body></html>", "text/html")
    fetchers = []

    def factory(source):
        f = DictFetcher(source, settings, pages)
        fetchers.append(f)
        return f

    rep = backfill(sf, settings, "RESMI_GAZETE", date(2026, 9, 20), date(2026, 9, 22), fetcher_factory=factory)
    assert rep.days == 3 and rep.new == 3
    asked = [u for f in fetchers for u in f.requested if "fihrist" in u]
    assert [u for u in asked if "mukerrer" not in u] == [f"{BASE}/fihrist?tarih=2026-09-2{i}" for i in (0, 1, 2)]
    with sf() as s:
        assert {r.published_at for r in s.scalars(select(RawDocument))} == {date(2026, 9, 20), date(2026, 9, 21),
                                                                            date(2026, 9, 22)}


def test_reprocess_classify_with_audit(settings, env):  # noqa: F811
    sf, _ = env
    reg_id = _reg(sf, title="Bankaların Kredi İşlemlerine İlişkin Yönetmelik", issuer="BDDK", sources=["BDDK"],
                  publish=date(2026, 9, 10))
    with sf() as s:
        s.get(Regulation, reg_id).processing_status = "BASELINE"
        s.commit()
    llm = FakeLLM(settings, lambda t, m, n: [rel()] * n if t == "relevance" else [sev("Yüksek")])
    out = reprocess(sf, settings, "classify", date(2026, 9, 1), date(2026, 9, 30), llm=llm)
    assert out["regulations"] == 1 and out["relevant"] == 1
    with sf() as s:
        assert s.get(Regulation, reg_id).processing_status == "RELEVANT"
        assert s.scalars(select(AuditEvent).where(AuditEvent.event_type == "reprocessed")).one().note.startswith(
            "Geriye dönük yeniden işleme: classify")
    with pytest.raises(ValueError):
        reprocess(sf, settings, "summarize", date(2026, 9, 1), date(2026, 9, 30), llm=None)


def test_admin_alert_endpoints(settings, env):  # noqa: F811
    sf, _ = env
    c = TestClient(create_app(settings.model_copy(update={"cors_origins": ""}), sf))
    assert c.post("/api/v1/admin/monitor/run").json()["opened"] == 9          # 9 kaynak henüz taranmadı
    alerts = c.get("/api/v1/admin/alerts").json()
    assert len(alerts) == 9 and {a["type"] for a in alerts} == {"fetch_error"}
    assert c.post("/api/v1/admin/reprocess", json={"stage": "extract", "from": "2026-09-10",
                                                   "to": "2026-09-01"}).status_code == 400
    assert c.post("/api/v1/admin/sources/YOK/backfill", json={"from": "2026-09-01", "to": "2026-09-02"}
                  ).status_code == 404
