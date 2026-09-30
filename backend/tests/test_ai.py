"""İK-3: LLM katmanı, ilgililik/güven skoru, önem kuralları, sınıflandırma hattı (FakeLLM ile, ağ gerekmez)."""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.ai.classify import classify_pending
from app.ai.common import effective_settings, set_runtime_setting
from app.ai.dedupe_confirm import make_confirmer
from app.ai.evaluation import run_eval
from app.ai.llm_client import FakeLLM, LLMError, OpenAICompatibleLLM, split_reasoning, strict_schema
from app.ai.relevance import RelevanceOut
from app.ai.severity import apply_rules
from app.db import LlmCall, Regulation
from app.processing.pipeline import DocumentProcessor
from tests.test_processing import add_bank_mellat, add_raw, env  # noqa: F401 — env fixture'ı paylaşılır


def rel(is_rel=True, score=0.9, uncertain=False, reg_type="Kurul Kararı", topics=("KURUMSAL",)):
    return {"is_relevant": is_rel, "relevance_score": score, "reg_type": reg_type, "matched_topics": list(topics),
            "matched_criteria": ["bankalar"], "rationale": "Bankayı doğrudan ilgilendiriyor.", "uncertain": uncertain}


def sev(level="Orta"):
    return {"severity": level, "rationale": "Kısa gerekçe.", "criteria": ["x"]}


def handler_from(relevance_outputs, severity_level="Orta"):
    def handler(task, messages, n):
        if task == "relevance":
            return (relevance_outputs * n)[:n] if len(relevance_outputs) < n else relevance_outputs[:n]
        if task == "severity":
            return [sev(severity_level)]
        raise AssertionError(task)
    return handler


# ------------------------------------------------------------------------------------------------ istemci


def test_strict_schema_and_reasoning_split():
    sch = strict_schema(RelevanceOut)
    assert sch["additionalProperties"] is False and set(sch["required"]) == set(RelevanceOut.model_fields)
    assert "minimum" not in json.dumps(sch)
    assert split_reasoning('<think>düşünce</think>\n{"a": 1}') == ('{"a": 1}', "düşünce")
    assert split_reasoning('düşünce</think>{"a": 1}') == ('{"a": 1}', "düşünce")   # parser kapalı, açılış yok
    assert split_reasoning('```json\n{"a": 1}\n```')[0] == '{"a": 1}'
    assert split_reasoning('İşte yanıt: {"a": 1} tamam')[0] == '{"a": 1}'


def test_complete_json_repair_cache_and_logging(settings, env):  # noqa: F811
    sf, _ = env
    answers = iter(['{"is_relevant": true}', json.dumps(rel()), json.dumps(rel())])   # ilk yanıt eksik → onarma
    llm = FakeLLM(settings, lambda t, m, n: [next(answers)])
    msgs = [{"role": "user", "content": "x"}]
    with sf() as s:
        res = llm.complete_json("relevance", "p1", msgs, RelevanceOut, session=s)
        assert res.outputs[0].is_relevant and not res.cached and len(llm.calls) == 2
        again = llm.complete_json("relevance", "p1", msgs, RelevanceOut, session=s)
        assert again.cached and len(llm.calls) == 2                  # önbellek: LLM yeniden çağrılmaz
        llm.complete_json("relevance", "p2", msgs, RelevanceOut, session=s)   # başka prompt sürümü → yeni çağrı
        assert len(llm.calls) == 3
        s.commit()
    bad = FakeLLM(settings, lambda t, m, n: ["yanıt yok"])
    with sf() as s:
        with pytest.raises(LLMError):
            bad.complete_json("relevance", "p1", [{"role": "user", "content": "y"}], RelevanceOut, session=s)
        s.commit()
        rows = s.scalars(select(LlmCall).order_by(LlmCall.id)).all()
    assert [r.status for r in rows] == ["ok", "ok", "invalid"]


def test_openai_compatible_request_shape(settings):
    captured = {}

    def create(**kw):
        captured.update(kw)
        msg = SimpleNamespace(content=json.dumps(rel()), reasoning_content="adım adım", model_extra={})
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10))

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    vs = settings.model_copy(update={"llm_provider": "vllm", "llm_model": "Qwen/Qwen3.6-35B-A3B-FP8"})
    llm = OpenAICompatibleLLM(vs, client=client)
    res = llm.complete_json("relevance", "p", [{"role": "user", "content": "x"}], RelevanceOut, n=3)
    assert captured["response_format"]["type"] == "json_schema" and captured["n"] == 3
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert captured["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}
    assert res.reasoning == "adım adım" and res.prompt_tokens == 50
    llm.complete_json("dedupe_confirm", "p", [{"role": "user", "content": "x"}], RelevanceOut)
    assert captured["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False  # basit görev
    oa = OpenAICompatibleLLM(settings.model_copy(update={"llm_provider": "openai"}), client=client)
    oa.complete_json("relevance", "p", [{"role": "user", "content": "x"}], RelevanceOut)
    assert "extra_body" not in captured or captured.get("model") == "gpt-4.1-mini"


# ------------------------------------------------------------------------------------------------ kurallar


def test_severity_rules():
    rules = {"max_by_type": {"Basın Duyurusu": "Orta"}, "critical_patterns": ["idari para cezası"],
             "immediate_effect_patterns": ["yayımı tarihinde yürürlüğe girer"], "bank_addressee_patterns": ["bankalar"]}
    assert apply_rules("Orta", "Tebliğ", "… idari para cezası uygulanır", rules) == \
        ("Yüksek", ["taban:kritik_kalip:idari para cezası"])
    assert apply_rules("Düşük", "Yönetmelik", "Bankalar … Bu Yönetmelik yayımı tarihinde yürürlüğe girer.", rules)[0] \
        == "Yüksek"
    assert apply_rules("Kritik", "Basın Duyurusu", "idari para cezası", rules) == \
        ("Orta", ["tavan:Basın Duyurusu→Orta"])                        # Başkanlık tavanı tabandan önceliklidir
    assert apply_rules("Kritik", "Yönetmelik", "metin", rules) == ("Kritik", [])
    # taslak: LLM türü "Tebliğ" dese de başlıktaki "Taslağı" tavanı uygulanır
    rules["max_by_title"] = {"taslağı": "Yüksek"}
    assert apply_rules("Kritik", "Tebliğ", "sermaye yeterliliği", rules,
                       titles="Takas Riskine Esas Tutarın Hesaplanmasına İlişkin Tebliğ Taslağı") == \
        ("Yüksek", ["tavan:başlık 'taslağı'→Yüksek"])


# ------------------------------------------------------------------------------------------------ hat


def _processed(settings, env):  # noqa: F811
    sf, storage = env
    add_bank_mellat(sf, storage)
    add_raw(sf, storage, source="RESMI_GAZETE", channel="fihrist", external_id="20260925-3",
            url="https://www.resmigazete.gov.tr/eskiler/2026/09/20260925-3.htm", published=date(2026, 9, 25),
            title="Tokat Gaziosmanpaşa Üniversitesi Tıp Fakültesi Eğitim-Öğretim ve Sınav Yönetmeliğinde Değişiklik "
                  "Yapılmasına Dair Yönetmelik",
            content=("<html><body><p>Tokat Gaziosmanpaşa Üniversitesinden:</p><p>MADDE 1- "
                     + "Öğrencilerin sınav notları … " * 20 + "</p></body></html>").encode())
    add_raw(sf, storage, source="BDDK", channel="baseline", external_id="old", url="https://www.bddk.org.tr/old",
            title="Eski duyuru", published=date(2020, 1, 1), baseline=True, content=b"<html><body>eski</body></html>")
    DocumentProcessor(settings, storage, use_default_ocr=False).process_pending(sf)
    with sf() as s:
        return {r.title[:20]: r.id for r in s.scalars(select(Regulation))}


def test_classify_pipeline(settings, env):  # noqa: F811
    sf, _ = env
    ids = _processed(settings, env)
    tokat = next(v for k, v in ids.items() if k.startswith("Tokat"))

    def handler(task, messages, n):
        user = messages[-1]["content"]
        if task == "relevance":
            if "Tokat" in user:
                return [rel(False, 0.05, reg_type="Yönetmelik", topics=())] * n
            return [rel(True, 0.9)] * n
        return [sev("Orta")]

    llm = FakeLLM(settings, handler)
    rep = classify_pending(sf, llm, settings)
    assert rep.processed == 2 and rep.relevant == 1 and rep.irrelevant == 1   # BASELINE sınıflandırılmaz
    with sf() as s:
        bank = s.scalars(select(Regulation).where(Regulation.issuer == "BDDK", Regulation.processing_status != "BASELINE")).one()
        assert bank.processing_status == "RELEVANT" and bank.confidence_band == "Yüksek"
        assert bank.relevance_score == pytest.approx(0.6 * 0.9 + 0.3 * 1.0 + 0.1 * bank.classification["relevance"]["rules"])
        # başka bir kuruluşun izin kaldırma kararı: kural tabanı uygulanmaz, LLM derecesi kalır
        assert bank.severity == "Orta" and bank.classification["severity"] == {"llm": "Orta", "rules": []}
        assert bank.classification["relevance"]["votes"] == [True, True, True]
        uni = s.get(Regulation, tokat)
        assert uni.processing_status == "IRRELEVANT" and uni.severity is None
        # BDDK metni ekten (PDF) de gelir: relevance isteğinde karar metni bulunmalı
    rel_call = next(c for c in llm.calls if c[0] == "relevance" and "Bank Mellat" in c[1][-1]["content"])
    assert "Karar Sayısı: 11572" in rel_call[1][-1]["content"] and "<kaynak_metin>" in rel_call[1][-1]["content"]
    assert llm.calls[0][2] == settings.relevance_samples                  # self-consistency n=3


def test_high_sensitivity_split_votes_and_uncertain(settings, env):  # noqa: F811
    sf, _ = env
    _processed(settings, env)
    outs = iter([[rel(True, 0.3), rel(False, 0.1), rel(False, 0.1)],       # oylar bölündü
                 [rel(False, 0.1, uncertain=True)] * 3])                     # ilgisiz ama emin değil
    llm = FakeLLM(settings, lambda t, m, n: next(outs) if t == "relevance" else [sev("Düşük")])
    classify_pending(sf, llm, settings)
    with sf() as s:
        regs = s.scalars(select(Regulation).where(Regulation.processing_status.in_(("RELEVANT", "IRRELEVANT")))
                         .order_by(Regulation.id)).all()
    # ikisi de eşik altında ama yüksek duyarlılık gereği portala düşer, güven "Düşük"
    assert [(r.processing_status, r.confidence_band) for r in regs] == [("RELEVANT", "Düşük")] * 2
    assert regs[0].relevance_score < settings.relevance_threshold
    assert regs[0].classification["relevance"]["split_votes"] and regs[1].classification["relevance"]["model_uncertain"]


def test_llm_failure_marks_ai_failed_and_retries(settings, env):  # noqa: F811
    sf, _ = env
    _processed(settings, env)
    broken = FakeLLM(settings, lambda t, m, n: ["bozuk"] * n)
    for _ in range(4):
        rep = classify_pending(sf, broken, settings)
    with sf() as s:
        failed = s.scalars(select(Regulation).where(Regulation.processing_status == "AI_FAILED")).all()
        assert len(failed) == 2 and all(r.extra["ai_attempts"] == 3 for r in failed)
        assert s.scalars(select(LlmCall).where(LlmCall.status == "invalid")).first() is not None  # iz korunur
    assert rep.processed == 0                                            # deneme hakkı bitti
    ok = FakeLLM(settings, handler_from([rel()], "Yüksek"))
    assert classify_pending(sf, ok, settings, force=True, ids=[r.id for r in failed]).relevant == 2


def test_runtime_settings_override(settings, env):  # noqa: F811
    sf, _ = env
    with sf() as s:
        set_runtime_setting(s, "relevance_threshold", "0.55", by="test")
        s.commit()
        assert effective_settings(s, settings).relevance_threshold == 0.55
        with pytest.raises(KeyError):
            set_runtime_setting(s, "llm_api_key", "x")


def test_dedupe_confirmer_uses_llm(settings, env):  # noqa: F811
    sf, storage = env
    add_raw(sf, storage, source="RESMI_GAZETE", channel="fihrist", external_id="a", url="https://rg/a",
            title="Bankaların Kredi İşlemlerine İlişkin Yönetmelikte Değişiklik Yapılmasına Dair Yönetmelik",
            published=date(2026, 9, 10), content=b"<html><body><p>Metin A</p></body></html>")
    add_raw(sf, storage, source="BDDK", channel="mevzuat_duyurulari", external_id="b", url="https://bddk/b",
            title="Bankaların Kredi İşlemlerine İlişkin Yönetmelik Değişikliği Hakkında Duyuru",
            published=date(2026, 9, 11), content=b"<html><body><p>Metin B farkli</p></body></html>")
    llm = FakeLLM(settings, lambda t, m, n: [{"same": True, "rationale": "Aynı değişiklik."}])
    DocumentProcessor(settings, storage, use_default_ocr=False,
                      confirmer=make_confirmer(llm, sf, settings)).process_pending(sf)
    with sf() as s:
        assert len(s.scalars(select(Regulation)).all()) == 1
    assert llm.calls[0][0] == "dedupe_confirm" and "Metin A" in llm.calls[0][1][-1]["content"]


def test_eval_harness(settings, tmp_path):
    data = [
        {"id": "1", "title": "Bankaların Likidite Karşılama Oranı Hakkında Yönetmelik", "issuer": "BDDK",
         "issuer_code": "BDDK", "sources": ["BDDK"], "expected_relevant": True, "expected_severity": "Kritik"},
        {"id": "2", "title": "Üniversite Sınav Yönetmeliği", "sources": ["RESMI_GAZETE"], "expected_relevant": False},
        {"id": "3", "title": "Kredi Kartı Tebliği", "sources": ["TCMB"], "expected_relevant": True,
         "expected_severity": "Yüksek"},
    ]
    f = tmp_path / "set.jsonl"
    f.write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in data), "utf-8")

    def handler(task, messages, n):
        u = messages[-1]["content"]
        if task == "severity":
            return [sev("Kritik")]
        if "Kredi Kartı" in u:
            return [rel(False, 0.1)] * n                                   # kaçırma
        return [rel("Likidite" in u, 0.9 if "Likidite" in u else 0.05)] * n

    rep = run_eval(f, FakeLLM(settings, handler), settings)
    m = rep.metrics()
    assert (m["tp"], m["fn"], m["tn"], m["fp"]) == (1, 1, 1, 0) and m["recall"] == 0.5
    assert rep.severity_accuracy() == {"n": 1, "exact": 1.0, "within_1": 1.0}
    assert "KAÇIRILAN" in rep.render() and "Kredi Kartı" in rep.render()


def test_include_baseline_backfill(settings, env):  # noqa: F811
    sf, _ = env
    _processed(settings, env)
    llm = FakeLLM(settings, handler_from([rel(False, 0.05, topics=())]))
    assert classify_pending(sf, llm, settings).processed == 2                        # BASELINE atlanır
    rep = classify_pending(sf, llm, settings, include_baseline=True)
    assert rep.processed == 1                                                         # yalnızca kalan BASELINE
    with sf() as s:
        assert s.scalars(select(Regulation).where(Regulation.processing_status == "BASELINE")).first() is None
