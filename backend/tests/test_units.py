"""İK-5: birim bilgi tabanı, öneri doğrulama, eşleştirme hattı ve öğrenme döngüsü (FakeLLM ile)."""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import select

from app.ai.llm_client import FakeLLM
from app.ai.summary import summarize_pending
from app.ai.unit_matching import (
    _stem_match,
    keyword_candidates,
    match_pending,
    record_unit_decision,
    select_examples,
    sync_units,
    validate_suggestions,
)
from app.db import FewshotExample, Regulation, Unit, UnitSuggestion
from app.settings import BACKEND_DIR
from tests.test_processing import env  # noqa: F401
from tests.test_summary import GOOD, _relevant_bank_mellat, summary_out

UNITS = yaml.safe_load((BACKEND_DIR / "config" / "units.yaml").read_text("utf-8"))


def test_units_yaml_consistency():
    taxonomy = yaml.safe_load((BACKEND_DIR / "config" / "taxonomy.yaml").read_text("utf-8"))
    topic_codes = {t["code"] for t in taxonomy["topics"]}
    codes = [u["code"] for u in UNITS["units"]]
    assert len(codes) == len(set(codes)) == 44
    for u in UNITS["units"]:
        assert re.fullmatch(r"[A-Z_]+", u["code"]) and len(u["responsibilities"]) >= 2, u["code"]
        assert set(u["regulatory_areas"]) <= topic_codes, u["code"]
        # her düzenlemeye uyan genel görev maddesi varsayılan birim etkisi yaratır (canlı denemede görüldü)
        for r in u["responsibilities"]:
            assert not re.search(r"mevzuat değişiklikleri|yasal düzenlemelere uyum", r.lower()), (u["code"], r)


def test_sync_units_upsert_and_deactivate(settings, env, tmp_path):  # noqa: F811
    sf, _ = env
    with sf() as s:
        assert sync_units(s, settings) == (44, 0)
        s.commit()
    small = tmp_path / "units.yaml"
    small.write_text(yaml.safe_dump({"version": "t", "units": UNITS["units"][:3]}, allow_unicode=True), "utf-8")
    with sf() as s:
        assert sync_units(s, settings.model_copy(update={"units_file": small})) == (3, 41)
        s.commit()
        assert len(s.scalars(select(Unit).where(Unit.active.is_(True))).all()) == 3


def test_stem_keyword_match():
    words = re.findall(r"\w+", "kredi kartı limitlerinin belirlenmesi")
    assert _stem_match("kredi kartı limiti", words) and _stem_match("kredi kartı", words)
    assert not _stem_match("şube satış", re.findall(r"\w+", "merkez şubesinin faaliyet izni"))
    units = [SimpleNamespace(code="A", keywords=["kredi kartı limiti", "taksit"]),
             SimpleNamespace(code="B", keywords=["sukuk"])]
    assert keyword_candidates(units, "Kredi Kartı Limitlerinin Belirlenmesi") == \
        [{"code": "A", "hits": ["kredi kartı limiti"]}]


def _s(code, score, resp, reason="gerekçe"):
    return SimpleNamespace(unit_code=code, score=score, matched_responsibility=resp, reason=reason)


def test_validate_suggestions(settings):
    units = {u["code"]: SimpleNamespace(code=u["code"], responsibilities=u["responsibilities"])
             for u in UNITS["units"]}
    risk = units["RISK_YONETIMI"].responsibilities[1]
    kept, dropped = validate_suggestions([
        _s("RISK_YONETIMI", 0.95, risk[:60]),                                   # kısmi alıntı → tam madde
        _s("HAZINE", 0.2, units["HAZINE"].responsibilities[0]),                 # düşük skor
        _s("MEVZUAT_UYUM", 0.8, "Bankanın tüm mevzuat değişikliklerinin takip edilmesi"),  # görevde yok
        _s("RISK_YONETIMI", 0.7, risk),                                         # tekrar
        _s("PERAKENDE_KREDILER", 0.6, units["PERAKENDE_KREDILER"].responsibilities[1]),
        _s("KREDI_POLITIKALARI", 0.6, units["KREDI_POLITIKALARI"].responsibilities[0]),
        _s("KREDI_IZLEME", 0.5, units["KREDI_IZLEME"].responsibilities[0]),
        _s("FINANSAL_RAPORLAMA", 0.4, units["FINANSAL_RAPORLAMA"].responsibilities[0]),
    ], units, settings)
    assert [k["unit_code"] for k in kept] == ["RISK_YONETIMI", "PERAKENDE_KREDILER", "KREDI_POLITIKALARI",
                                              "KREDI_IZLEME"]                  # en fazla 4, skora göre
    assert kept[0]["matched_responsibility"] == risk
    # skora göre sırayla değerlendirilir; 4 öneriye ulaşınca en düşük skorlu HAZINE hiç değerlendirilmez
    assert {d["unit_code"]: d["why"] for d in dropped} == {"MEVZUAT_UYUM": "görev tanımında karşılığı yok"}
    _, dropped = validate_suggestions([_s("HAZINE", 0.2, units["HAZINE"].responsibilities[0])], units, settings)
    assert dropped == [{"unit_code": "HAZINE", "score": 0.2, "why": "düşük skor"}]


def test_area_mismatch_is_dropped(settings):
    units = {u["code"]: SimpleNamespace(code=u["code"], responsibilities=u["responsibilities"],
                                        regulatory_areas=u["regulatory_areas"]) for u in UNITS["units"]}
    aml = units["MEVZUAT_UYUM"].responsibilities[2]
    kept, dropped = validate_suggestions([_s("MEVZUAT_UYUM", 0.9, aml), _s("HAZINE", 0.8,
                                          units["HAZINE"].responsibilities[0])], units, settings, {"KREDI_FON"})
    assert kept == [] and {d["why"] for d in dropped} == {"alan uyuşmazlığı"}
    kept, _ = validate_suggestions([_s("MEVZUAT_UYUM", 0.9, aml)], units, settings, {"AML"})
    assert [k["unit_code"] for k in kept] == ["MEVZUAT_UYUM"]
    kept, _ = validate_suggestions([_s("MEVZUAT_UYUM", 0.9, aml)], units, settings, set())  # konu yoksa kontrol yok
    assert kept


def _summarized(settings, env):  # noqa: F811
    sf, _ = env
    reg_id = _relevant_bank_mellat(settings, env)
    summarize_pending(sf, FakeLLM(settings, lambda t, m, n: [summary_out([GOOD])]), settings)
    _set_topics(sf, reg_id, ["SERMAYE_LIKIDITE", "KAMBIYO", "KURUMSAL"])
    return reg_id


def _set_topics(sf, reg_id, topics):
    with sf() as s:
        reg = s.get(Regulation, reg_id)
        cls = dict(reg.classification)
        cls["relevance"] = {**cls["relevance"], "topics": topics}
        reg.classification = cls
        s.commit()


def test_match_pipeline_ready_and_prompt(settings, env):  # noqa: F811
    sf, _ = env
    reg_id = _summarized(settings, env)
    risk = UNITS["units"][0]["responsibilities"][-1]                            # karşı taraf limitleri

    def handler(task, messages, n):
        assert task == "unit_match"
        system = messages[0]["content"]
        assert "## RISK_YONETIMI — Risk Yönetimi Başkanlığı" in system and "Temsah" not in system  # kişi adı yok
        assert GOOD["sentence"] in messages[-1]["content"]                     # özet girdide
        return [{"suggestions": [
            {"unit_code": "RISK_YONETIMI", "score": 0.8, "matched_responsibility": risk,
             "reason": "Karşı taraf bankanın izni kaldırıldı."},
            {"unit_code": "ULUSLARARASI_BANKACILIK", "score": 0.7,
             "matched_responsibility": "Muhabir bankacılık ilişkilerinin yönetimi", "reason": "Muhabir ilişkisi."}]}]

    rep = match_pending(sf, FakeLLM(settings, handler), settings)
    assert (rep.processed, rep.matched, rep.suggestions) == (1, 1, 2)
    with sf() as s:
        reg = s.get(Regulation, reg_id)
        sugg = s.scalars(select(UnitSuggestion).order_by(UnitSuggestion.rank)).all()
    assert reg.processing_status == "READY" and reg.extra["unit_match"]["status"] == "önerildi"
    assert [(x.rank, x.unit_code, x.origin, x.is_active) for x in sugg] == [
        (1, "RISK_YONETIMI", "ai", True), (2, "ULUSLARARASI_BANKACILIK", "ai", True)]
    # yeniden çalıştırma: yeni iş yok; --force ile eski öneriler pasifleşir
    assert match_pending(sf, FakeLLM(settings, handler), settings).processed == 0
    match_pending(sf, FakeLLM(settings, handler), settings, force=True)
    with sf() as s:
        assert len(s.scalars(select(UnitSuggestion).where(UnitSuggestion.is_active.is_(True))).all()) == 2
        assert len(s.scalars(select(UnitSuggestion)).all()) == 4


def test_no_suggestion_and_unknown_code(settings, env):  # noqa: F811
    sf, _ = env
    reg_id = _summarized(settings, env)
    # kapalı liste dışı kod: şema reddeder → onarma da başarısız → AI_FAILED (unit_match aşaması)
    bad = FakeLLM(settings, lambda t, m, n: [{"suggestions": [{"unit_code": "HUKUK_ISLERI", "score": 0.9,
                                                               "matched_responsibility": "x", "reason": "y"}]}])
    assert match_pending(sf, bad, settings).failed == 1
    with sf() as s:
        reg = s.get(Regulation, reg_id)
        assert reg.processing_status == "AI_FAILED" and reg.extra["ai_stage"] == "unit_match"
    # yeniden denemede boş öneri: varsayılan birim atanmaz, "önerilemedi"
    empty = FakeLLM(settings, lambda t, m, n: [{"suggestions": []}])
    rep = match_pending(sf, empty, settings)
    assert rep.no_suggestion == 1
    with sf() as s:
        reg = s.get(Regulation, reg_id)
        assert reg.processing_status == "READY" and reg.extra["unit_match"]["status"] == "önerilemedi"
        assert s.scalars(select(UnitSuggestion)).first() is None


def test_learning_loop_examples(settings, env):  # noqa: F811
    sf, _ = env
    reg_id = _summarized(settings, env)
    with sf() as s:
        reg = s.get(Regulation, reg_id)
        record_unit_decision(s, reg, ["RISK_YONETIMI"], corrected=True)
        s.add(FewshotExample(task="unit_match", input_text="Üniversite yönetmeliği | konular: eğitim",
                             expected_output={"units": ["EGITIM_KARIYER"]}, origin="user_decision", weight=1.0))
        s.commit()
        ex = select_examples(s, "Bank Mellat şubesinin faaliyet izni kaldırıldı | konular: Faaliyet izni", 1)
    assert ex == [{"input_text": ex[0]["input_text"], "units": ["RISK_YONETIMI"], "origin": "user_correction"}]
    seen = {}

    def handler(task, messages, n):
        seen["user"] = messages[-1]["content"]
        return [{"suggestions": []}]

    match_pending(sf, FakeLLM(settings, handler), settings)
    assert "Geçmiş örnek 1 (user_correction)" in seen["user"] and "RISK_YONETIMI" in seen["user"]


def test_awaiting_text_regulation_gets_units_but_stays_relevant(settings, env):  # noqa: F811
    from tests.test_summary import test_no_text_waits_without_llm_call

    sf, _ = env
    test_no_text_waits_without_llm_call(settings, env)
    with sf() as s:
        _set_topics(sf, s.scalars(select(Regulation)).one().id, ["SERMAYE_LIKIDITE"])
    risk = UNITS["units"][0]["responsibilities"][1]
    llm = FakeLLM(settings, lambda t, m, n: [{"suggestions": [{"unit_code": "RISK_YONETIMI", "score": 0.7,
                                                               "matched_responsibility": risk, "reason": "r"}]}])
    assert match_pending(sf, llm, settings).matched == 1
    assert "özet yok" in llm.calls[0][1][-1]["content"]
    with sf() as s:
        assert s.scalars(select(Regulation)).one().processing_status == "RELEVANT"


@pytest.mark.parametrize("code", ["MEVZUAT_UYUM", "KATILIM_ILKELERI", "RISK_YONETIMI"])
def test_key_units_present(code):
    assert code in {u["code"] for u in UNITS["units"]}
