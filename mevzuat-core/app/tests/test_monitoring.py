"""İK-7: iş günü takvimi, kaynak sağlığı kontrolleri, çapraz kontrol, alarm yaşam döngüsü, backfill, reprocess."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.llm_client import FakeLLM
from app.api.main import create_app
from app.db import Alert, AuditEvent, FetchRun, JobRun, RawDocument, Regulation, RegulationSourceLink, Source
from app.monitoring import alerts as alerts_mod
from app.monitoring.calendar import business_hours_between, is_business_day
from app.monitoring.checks import check_source
from app.monitoring.reprocess import backfill, failed_regulations, local_store, reprocess, retry_failed
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

    rep = backfill(local_store(sf, settings), settings, "RESMI_GAZETE", date(2026, 9, 20), date(2026, 9, 22), fetcher_factory=factory)
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


def _fail(sf, reg_id, stage, attempts=3, relevant=None):
    with sf() as s:
        reg = s.get(Regulation, reg_id)
        reg.processing_status, reg.is_relevant = "AI_FAILED", relevant
        reg.extra = {"ai_stage": stage, "ai_attempts": attempts, "ai_error": "zaman aşımı"}
        s.commit()


def test_retry_failed_reruns_exhausted_regulation_from_failed_stage(settings, env):  # noqa: F811
    sf, _ = env
    exhausted = _reg(sf, title="Bankaların Kredi İşlemlerine İlişkin Yönetmelik", issuer="BDDK", sources=["BDDK"],
                     publish=date(2026, 9, 10))
    other = _reg(sf, title="Kredi Kartı Tebliği", issuer="BDDK", sources=["BDDK"], publish=date(2026, 9, 11))
    _fail(sf, exhausted, "relevance")
    _fail(sf, other, "summary", relevant=True)
    llm = FakeLLM(settings, lambda t, m, n: [rel()] * n if t == "relevance" else [sev("Yüksek")])
    out = retry_failed(sf, settings, llm=llm, stages=["classify"])
    assert out["retried"] == 1 and out["by_stage"] == {"classify": 1} and out["still_failed"] == 0
    with sf() as s:
        reg = s.get(Regulation, exhausted)
        assert reg.is_relevant and reg.processing_status != "AI_FAILED" and "ai_attempts" not in reg.extra
        assert s.get(Regulation, other).processing_status == "AI_FAILED"      # aşama filtresi dışında kaldı
        assert s.scalars(select(AuditEvent).where(AuditEvent.regulation_id == exhausted,
                                                  AuditEvent.event_type == "reprocessed")).one().note.endswith("classify")
    out = retry_failed(sf, settings, llm=llm)                                   # kalan: özet aşaması hatası
    assert out["by_stage"] == {"summarize": 1} and out["recovered"] == 1 and not out["llm_unavailable"]


def test_retry_failed_keeps_failures_retryable_when_llm_is_down(settings, env):  # noqa: F811
    from app.ai.llm_client import LLMUnavailable

    sf, _ = env
    reg_id = _reg(sf, title="Bankaların Kredi İşlemlerine İlişkin Yönetmelik", issuer="BDDK", sources=["BDDK"],
                  publish=date(2026, 9, 10))
    _fail(sf, reg_id, "relevance")

    def down(task, messages, n):
        raise LLMUnavailable("zaman aşımı")

    out = retry_failed(sf, settings, llm=FakeLLM(settings, down))
    assert out["llm_unavailable"] and out["still_failed"] == 1
    with sf() as s:
        assert s.get(Regulation, reg_id).extra["ai_attempts"] == 0              # sayaç sıfırlandı, hata sayılmadı
    with pytest.raises(ValueError):
        retry_failed(sf, settings, llm=None)
    with pytest.raises(ValueError):
        retry_failed(sf, settings, llm=FakeLLM(settings, down), stages=["extract"])


def test_failed_regulations_filters_by_source_and_ids(settings, env):  # noqa: F811
    sf, _ = env
    bddk = _reg(sf, title="BDDK Kararı", issuer="BDDK", sources=["BDDK"], publish=date(2026, 9, 10))
    spk = _reg(sf, title="SPK Kararı", issuer="SPK", sources=["SPK"], publish=date(2026, 9, 10))
    _fail(sf, bddk, "unit_match", relevant=True)
    _fail(sf, spk, "summary", relevant=True)
    with sf() as s:
        assert failed_regulations(s) == {bddk: "match", spk: "summarize"}
        assert failed_regulations(s, source="SPK") == {spk: "summarize"}
        assert failed_regulations(s, ids=[bddk], stages=["match"]) == {bddk: "match"}
        assert failed_regulations(s, stages=["classify"]) == {}


def test_admin_retry_failed_endpoint(settings, env):  # noqa: F811
    sf, _ = env
    c = TestClient(create_app(settings.model_copy(update={"cors_origins": "", "llm_provider": "none"}), sf))
    assert c.post("/api/v1/admin/retry-failed").status_code == 400                # LLM yapılandırılmamış
    c = TestClient(create_app(settings.model_copy(update={"cors_origins": "", "llm_provider": "openai"}), sf))
    assert c.post("/api/v1/admin/retry-failed").json() == {"status": "nothing_to_do", "count": 0}
    with sf() as s:
        assert s.scalars(select(JobRun)).first() is None                          # yapılacak iş yoksa kayıt açılmaz


def _baseline_reg(sf, title, source, publish):
    reg_id = _reg(sf, title=title, issuer=source, sources=[source], publish=publish)
    with sf() as s:
        s.get(Regulation, reg_id).processing_status = "BASELINE"
        for raw in s.scalars(select(RawDocument).where(RawDocument.regulation_id == reg_id)):
            raw.published_at = publish
        s.commit()
    return reg_id


def test_plan_rebaseline_counts_converted_records_and_decides_crawl(settings, env):  # noqa: F811
    from app.services.rebaseline import plan_rebaseline

    sf, _ = env
    _baseline_reg(sf, "Eski Karar", "BDDK", date(2026, 7, 20))
    _baseline_reg(sf, "Ağustos Kararı", "BDDK", date(2026, 8, 5))
    with sf() as s:
        plan = plan_rebaseline(s, settings, date(2026, 8, 1), today=date(2026, 10, 19))
    rows = {r["source"]: r for r in plan["sources"]}
    assert plan["converted"] == 1 and rows["BDDK"]["converted"] == 1
    assert rows["BDDK"]["action"] == "covered" and "BDDK" not in plan["crawl"]      # arşiv sınırdan eskiye uzanıyor
    assert rows["RESMI_GAZETE"]["action"] == "crawl" and "RESMI_GAZETE" in plan["crawl"]
    assert rows["RESMI_GAZETE"]["range"] == {"from": "2026-08-01", "to": "2026-10-19"}   # hiç taranmamış: sınırdan bugüne


def test_plan_rebaseline_rg_range_ends_before_the_earliest_crawled_day(settings, env):  # noqa: F811
    from app.services.rebaseline import plan_rebaseline, _rg_chunks

    sf, _ = env
    _baseline_reg(sf, "RG Kararı", "RESMI_GAZETE", date(2026, 9, 20))
    with sf() as s:
        row = {r["source"]: r for r in plan_rebaseline(s, settings, date(2026, 8, 1), today=date(2026, 10, 19))["sources"]}
    assert row["RESMI_GAZETE"]["range"] == {"from": "2026-08-01", "to": "2026-09-19"}
    chunks = _rg_chunks({"from": "2026-08-01", "to": "2026-10-19"})                  # 80 gün → 62 + 18
    assert chunks == [(date(2026, 8, 1), date(2026, 10, 1)), (date(2026, 10, 2), date(2026, 10, 19))]


def test_apply_rebaseline_converts_runs_ai_chain_and_is_idempotent(settings, env, monkeypatch):  # noqa: F811
    from app.services import rebaseline as rb

    sf, _ = env
    old = _baseline_reg(sf, "Eski Karar", "BDDK", date(2026, 7, 20))
    new = _baseline_reg(sf, "Bankaların Kredi İşlemlerine İlişkin Yönetmelik", "BDDK", date(2026, 8, 5))
    undated = _baseline_reg(sf, "Tarihsiz Karar", "BDDK", None)
    crawls = []
    monkeypatch.setattr(rb, "_crawl", lambda sf_, st, code, d_from, d_to, cutoff, actor:
                        crawls.append((code, d_from, cutoff)) or {"source": code, "status": "done"})
    llm = FakeLLM(settings, lambda t, m, n: [rel()] * n if t == "relevance" else [sev("Yüksek")])
    out = rb.apply_rebaseline(sf, settings, date(2026, 8, 1), llm=llm)
    assert out["converted"] == 1 and not out["llm_unavailable"] and sum(out["by_status"].values()) == 1
    with sf() as s:
        assert s.get(Regulation, old).processing_status == "BASELINE"        # sınırdan eski: baseline kalır
        assert s.get(Regulation, undated).processing_status == "BASELINE"    # tarihsiz: dokunulmaz
        reg = s.get(Regulation, new)
        assert reg.is_relevant and reg.processing_status not in ("BASELINE", "NEW", "AI_FAILED")
        assert rb.get_cutoff(s) == date(2026, 8, 1)
        assert s.scalars(select(AuditEvent).where(AuditEvent.regulation_id == new,
                                                  AuditEvent.event_type == "reprocessed")).one().note.startswith("Baseline sınırı")
    assert ("RESMI_GAZETE", date(2026, 8, 1), date(2026, 8, 1)) in crawls   # taranmamış kaynak sınırdan başlar
    assert not any(code == "BDDK" for code, *_ in crawls)
    assert rb.apply_rebaseline(sf, settings, date(2026, 8, 1), llm=llm)["converted"] == 0   # ikinci çalıştırma: iş yok
    with pytest.raises(ValueError):
        rb.apply_rebaseline(sf, settings, date(2026, 8, 1), llm=None)


def test_apply_rebaseline_keeps_records_new_when_llm_is_down(settings, env, monkeypatch):  # noqa: F811
    from app.ai.llm_client import LLMUnavailable
    from app.services import rebaseline as rb

    sf, _ = env
    new = _baseline_reg(sf, "Ağustos Kararı", "BDDK", date(2026, 8, 5))
    monkeypatch.setattr(rb, "_crawl", lambda *a: {"status": "done"})

    def down(task, messages, n):
        raise LLMUnavailable("zaman aşımı")

    out = rb.apply_rebaseline(sf, settings, date(2026, 8, 1), llm=FakeLLM(settings, down))
    assert out["llm_unavailable"]
    with sf() as s:
        assert s.get(Regulation, new).processing_status == "NEW"      # zamanlanmış classify işi alır


def test_rebaseline_saves_progress_and_resume_continues_without_losing_or_repeating_work(settings, env, monkeypatch):  # noqa: F811
    from app.services import rebaseline as rb
    from app.services.jobs import create_job

    sf, _ = env
    cutoff = date(2026, 8, 1)
    new = _baseline_reg(sf, "Bankaların Kredi İşlemlerine İlişkin Yönetmelik", "BDDK", date(2026, 8, 5))
    crawls = []

    def fake_crawl(sf_, st, code, d_from, d_to, cutoff_, actor):
        crawls.append(code)
        return {"source": code, "from": d_from.isoformat(), "to": d_to.isoformat(), "status": "done"}

    monkeypatch.setattr(rb, "_crawl", fake_crawl)
    llm = FakeLLM(settings, lambda t, m, n: [rel()] * n if t == "relevance" else [sev("Yüksek")])
    # 1. koşu: kayıtlar NEW yapıldı ama süreç YZ'ye geçmeden öldü (iş "interrupted", ilerleme kayıtlı)
    with sf() as s:
        jid = create_job(s, "rebaseline", requested_by="t", params={"cutoff": cutoff.isoformat()})
    with sf() as s:
        reg = s.get(Regulation, new)
        reg.processing_status, reg.extra = "NEW", {"rebaselined_at": cutoff.isoformat()}
        job = s.get(JobRun, jid)
        job.status, job.result = "interrupted", {"crawl": [{"source": "BDDK", "status": "done", "key": rb.crawl_key(
            cutoff, "BDDK", cutoff, date.today())}]}
        s.commit()
    # SPK taraması yapılmış sayılsın; RG ve diğerleri yapılmamış
    spk_key = rb.crawl_key(cutoff, "SPK", cutoff, date.today())
    with sf() as s:
        s.get(JobRun, jid).result = {"crawl": [{"source": "SPK", "status": "done", "key": spk_key}]}
        s.commit()
    out = rb.resume_interrupted(sf, settings, llm=llm)
    assert out["status"] == "resumed" and out["resumedFrom"] == jid
    assert "SPK" not in crawls and "RESMI_GAZETE" in crawls                  # biten tarama tekrarlanmadı
    with sf() as s:
        reg = s.get(Regulation, new)
        assert reg.is_relevant and reg.processing_status not in ("NEW", "AI_FAILED")   # önceki koşunun NEW'i de işlendi
        resumed = s.get(JobRun, out["jobId"])
        assert resumed.status == "success" and resumed.result["phase"] == "done"
        assert {e["source"] for e in resumed.result["crawl"]} >= {"SPK", "RESMI_GAZETE"}
        assert next(e for e in resumed.result["crawl"] if e["source"] == "SPK")["skipped"]
    assert rb.resume_interrupted(sf, settings, llm=llm)["status"] == "nothing_to_resume"   # artık kesilmiş iş yok


def test_resume_interrupted_gives_up_after_repeated_interruptions(settings, env):  # noqa: F811
    from app.services import rebaseline as rb
    from app.services.jobs import create_job

    sf, _ = env
    with sf() as s:
        for _ in range(rb.MAX_AUTO_RESUMES + 1):
            jid = create_job(s, "rebaseline", params={"cutoff": "2026-08-01"})
            s.get(JobRun, jid).status = "interrupted"
        s.commit()
    llm = FakeLLM(settings, lambda t, m, n: [rel()] * n)
    assert rb.resume_interrupted(sf, settings, llm=llm)["status"] == "gave_up"


def test_submit_crawl_request_once_dedupes_pending_running_and_done(mdb):
    from app.mongo import REQUESTS, finish_crawl_request, submit_crawl_request_once

    first, created = submit_crawl_request_once(mdb, "backfill", "BDDK", {"history_from": "2026-08-01"}, "k1")
    assert created
    assert submit_crawl_request_once(mdb, "backfill", "BDDK", {}, "k1") == (first, False)       # bekliyor
    doc = mdb[REQUESTS].find_one_and_update({"status": "pending"}, {"$set": {"status": "running"}})
    assert submit_crawl_request_once(mdb, "backfill", "BDDK", {}, "k1") == (first, False)       # çalışıyor
    finish_crawl_request(mdb, doc["_id"], ok=True)
    assert submit_crawl_request_once(mdb, "backfill", "BDDK", {}, "k1") == (first, False)       # bitti
    assert submit_crawl_request_once(mdb, "backfill", "BDDK", {}, "k2")[1]                      # başka anahtar: yeni
    finish_crawl_request(mdb, doc["_id"], ok=False, error="x")
    assert submit_crawl_request_once(mdb, "backfill", "BDDK", {}, "k1")[1]                      # başarısız: yeniden açılır


def test_admin_rebaseline_rejects_second_job_while_one_is_running(settings, env):  # noqa: F811
    from app.services.jobs import create_job

    sf, _ = env
    with sf() as s:
        create_job(s, "rebaseline", params={"cutoff": "2026-08-01"})                            # queued
    c = TestClient(create_app(settings.model_copy(update={"cors_origins": "", "llm_provider": "openai"}), sf))
    r = c.post("/api/v1/admin/rebaseline", json={"cutoff": "2026-08-01"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "rebaseline_running"


def test_admin_rebaseline_endpoints(settings, env):  # noqa: F811
    sf, _ = env
    _baseline_reg(sf, "Ağustos Kararı", "BDDK", date(2026, 8, 5))
    c = TestClient(create_app(settings.model_copy(update={"cors_origins": "", "llm_provider": "none"}), sf))
    assert c.get("/api/v1/admin/rebaseline").json() == {"current": None}
    prev = c.get("/api/v1/admin/rebaseline?cutoff=2026-08-01").json()
    assert prev["converted"] == 1 and prev["cutoff"] == "2026-08-01" and prev["sources"]
    assert c.post("/api/v1/admin/rebaseline", json={"cutoff": "2026-08-01"}).status_code == 400   # LLM yapılandırılmamış
    c = TestClient(create_app(settings.model_copy(update={"cors_origins": "", "llm_provider": "openai"}), sf))
    assert c.post("/api/v1/admin/rebaseline", json={"cutoff": "2999-01-01"}).status_code == 400    # gelecek tarih
    with sf() as s:
        assert s.scalars(select(JobRun)).first() is None


def test_track_job_success_skip_and_failure(settings, env, monkeypatch):  # noqa: F811
    from app.services import jobs

    monkeypatch.setattr(jobs, "HEARTBEAT_S", 3600)     # kalp atışı iş parçacığı testte hiçbir şey yazmasın
    sf, _ = env

    def make():
        with sf() as s:
            return jobs.create_job(s, "reprocess", params={"a": 1}, requested_by="t")

    ok, skipped, bad = make(), make(), make()
    with sf() as s:
        assert s.get(JobRun, ok).status == "queued"
    with jobs.track_job(sf, ok) as h:
        h.result = {"n": 1}
    with jobs.track_job(sf, skipped) as h:
        h.skip("meşgul")
    with pytest.raises(ValueError):
        with jobs.track_job(sf, bad):
            raise ValueError("x")
    with sf() as s:
        a, b, c = (s.get(JobRun, i) for i in (ok, skipped, bad))
        assert (a.status, a.result) == ("success", {"n": 1}) and a.started_at and a.finished_at and a.worker
        assert (b.status, b.error, b.result) == ("skipped", "meşgul", None)
        assert (c.status, c.error) == ("failed", "ValueError: x")


def test_sweep_stale_jobs_and_local_runs(settings, env):  # noqa: F811
    from app.services.jobs import sweep_stale

    sf, _ = env
    now = datetime(2026, 10, 19, 12, tzinfo=UTC)
    old = now - timedelta(hours=10)
    with sf() as s:
        def job(**kw):
            j = JobRun(**{"kind": "reprocess", "created_at": now - timedelta(minutes=30), **kw})
            s.add(j)
            s.flush()
            return j.id

        dead = job(status="running", started_at=now - timedelta(minutes=30), heartbeat_at=now - timedelta(minutes=20))
        alive = job(status="running", started_at=now - timedelta(minutes=30), heartbeat_at=now - timedelta(minutes=1))
        stale_q = job(status="queued", kind="run", created_at=now - timedelta(hours=7))
        fresh_q = job(status="queued", kind="run", created_at=now - timedelta(hours=1))
        job(status="running", kind="backfill", source_code="BDDK", started_at=now - timedelta(minutes=30),
            heartbeat_at=now - timedelta(minutes=1))                               # BDDK için canlı backfill
        s.add_all([FetchRun(source_code="SPK", started_at=old, status="running"),   # sahipsiz
                   FetchRun(source_code="BDDK", started_at=old, status="running"),  # backfill: started_at tarama günü
                   FetchRun(source_code="KVKK", started_at=now - timedelta(minutes=5), status="running")])
        s.commit()
    remote = settings.model_copy(update={"crawl_mode": "remote"})
    with sf() as s:   # uzak modda tarama kayıtlarına dokunulmaz (crawler kendi kalıntılarını temizler)
        assert sweep_stale(s, remote, now) == {"jobs": 2, "runs": 0}
        s.commit()
    with sf() as s:
        assert sweep_stale(s, settings.model_copy(update={"crawl_mode": "local"}), now) == {"jobs": 0, "runs": 1}
        s.commit()
    with sf() as s:
        st = {i: s.get(JobRun, i).status for i in (dead, alive, stale_q, fresh_q)}
        assert st == {dead: "interrupted", alive: "running", stale_q: "interrupted", fresh_q: "queued"}
        assert "durdu" in s.get(JobRun, dead).error and s.get(JobRun, dead).finished_at
        runs = {r.source_code: r for r in s.scalars(select(FetchRun))}
        assert runs["SPK"].status == "failed" and "interrupted" in runs["SPK"].errors[-1] and runs["SPK"].finished_at
        assert runs["BDDK"].status == "running" and runs["KVKK"].status == "running"


def test_admin_alert_endpoints(settings, env):  # noqa: F811
    sf, _ = env
    c = TestClient(create_app(settings.model_copy(update={"cors_origins": ""}), sf))
    assert c.post("/api/v1/admin/monitor/run").json()["opened"] == 12         # 12 kaynak henüz taranmadı
    alerts = c.get("/api/v1/admin/alerts").json()
    assert len(alerts) == 12 and {a["type"] for a in alerts} == {"fetch_error"}
    assert c.post("/api/v1/admin/reprocess", json={"stage": "extract", "from": "2026-09-10",
                                                   "to": "2026-09-01"}).status_code == 400
    assert c.post("/api/v1/admin/sources/YOK/backfill", json={"from": "2026-09-01", "to": "2026-09-02"}
                  ).status_code == 404
