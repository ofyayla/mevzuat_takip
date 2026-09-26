"""İK-4: kaynağa dayandırma (alıntı doğrulama), yürürlük tarihi, özet hattı (FakeLLM ile, gerçek belgelerle)."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.ai.classify import classify_pending
from app.ai.grounding import SourceText, find_effective_sentence, find_quote, resolve_effective_date
from app.ai.llm_client import FakeLLM
from app.ai.summary import split_chunks, summarize_pending
from app.db import Regulation, RegulationSummary
from app.processing.pipeline import DocumentProcessor
from tests.test_ai import rel, sev
from tests.test_processing import add_bank_mellat, add_raw, env  # noqa: F401

KARAR = ("Bankacılık Düzenleme ve Denetleme Kurumundan:\n\nKarar Sayısı: 11572\n\nKurulun 18.09.2026 tarihli "
         "toplantısında;\n\nBank Mellat Merkezi Tahran İstanbul Türkiye Merkez Şubesinin faaliyet izninin\n5411 sayılı "
         "Bankacılık Kanununun 71 inci maddesinin birinci fıkrasının (b) bendi\nçerçevesinde kaldırılmasına\n\n"
         "karar verilmiştir.")


# ------------------------------------------------------------------------------------------------ alıntı bulma


def test_find_quote_methods_and_offsets():
    src = [SourceText(7, KARAR)]
    q = "Bank Mellat Merkezi Tahran İstanbul Türkiye Merkez Şubesinin faaliyet izninin 5411 sayılı Bankacılık"
    e = find_quote(q, src)
    assert e["method"] == "exact" and e["raw_document_id"] == 7
    assert KARAR[e["char_start"]:e["char_end"]].replace("\n", " ") == q          # satır sonu içeren orijinal aralık
    # noktalama/tire/boşluk farkı: kelime dizisi aynı
    assert find_quote("Kurulun 18.09.2026 tarihli toplantısında: Bank Mellat", src)["method"] == "exact_words"
    # "…" ile birleştirilmiş iki birebir parça
    assert find_quote("Bank Mellat Merkezi … karar verilmiştir", src)["method"] == "exact"
    # bent atlanmış ama tüm kelimeler sırayla ve yakın: atlamalı
    elided = find_quote("Şubesinin faaliyet izninin 5411 sayılı Bankacılık Kanununun çerçevesinde kaldırılmasına", src)
    assert elided and elided["method"] == "elided"
    # uydurma / başka kelimeyle yazılmış ifadeler kabul edilmez
    assert find_quote("Bankanın tüm şubeleri kapatılacak ve mevduatlar TMSF'ye devredilecektir", src) is None
    assert find_quote("Bank Mellat şubesinin lisansı 5411 sayılı kanun uyarınca iptal edildi", src) is None
    assert find_quote("izninin", src) is None                                     # çok kısa alıntı


def test_effective_date_rules():
    pub = date(2026, 9, 24)
    assert resolve_effective_date("Bu Yönetmelik yayımı tarihinde yürürlüğe girer.", pub).value == pub
    assert resolve_effective_date("yayımı tarihinden itibaren altı ay sonra yürürlüğe girer", pub).value == \
        date(2027, 3, 24)
    assert resolve_effective_date("Bu Tebliğ yayımından 30 gün sonra yürürlüğe girer", pub).value == date(2026, 10, 24)
    assert resolve_effective_date("Bu Tebliğ 1/1/2027 tarihinde yürürlüğe girer.", pub).value == date(2027, 1, 1)
    chk = resolve_effective_date("Bu Karar yayımı tarihinde yürürlüğe girer.", pub, llm_value=date(2026, 10, 1))
    assert chk.value == pub and "tutarsız" in chk.note                           # ifade LLM'den önceliklidir
    text = "MADDE 25- (1) Bu Yönetmelik yayımı tarihinde yürürlüğe girer.\nMADDE 26- (1) … Bakan yürütür."
    assert find_effective_sentence(text) == "Bu Yönetmelik yayımı tarihinde yürürlüğe girer"
    assert find_effective_sentence("X Yönetmeliği yürürlükten kaldırılmıştır.") is None


def test_split_chunks_on_paragraphs():
    text = "\n".join(f"MADDE {i}- " + "kelime " * 50 for i in range(20))
    chunks = split_chunks(text, 1000)
    assert len(chunks) > 1 and all(len(c) <= 1000 for c in chunks) and "".join(chunks).count("MADDE") == 20


# ------------------------------------------------------------------------------------------------ hat


def _relevant_bank_mellat(settings, env):  # noqa: F811
    sf, storage = env
    add_bank_mellat(sf, storage)
    DocumentProcessor(settings, storage, use_default_ocr=False).process_pending(sf)
    classify_pending(sf, FakeLLM(settings, lambda t, m, n: [rel()] * n if t == "relevance" else [sev("Orta")]),
                     settings)
    with sf() as s:
        return s.scalars(select(Regulation).where(Regulation.processing_status == "RELEVANT")).one().id


GOOD = {"sentence": "BDDK, Bank Mellat İstanbul şubesinin faaliyet iznini kaldırdı.",
        "quotes": ["Bank Mellat Merkezi Tahran İstanbul Türkiye Merkez Şubesinin faaliyet izninin"]}
BAD = {"sentence": "Mevduatlar TMSF'ye devredilecektir.", "quotes": ["mevduatlar Tasarruf Mevduatı Sigorta Fonuna devredilir"]}
TOPIC = {"text": "Faaliyet izni", "quotes": ["faaliyet izninin 5411 sayılı Bankacılık Kanununun 71 inci maddesinin"]}


def summary_out(claims, topics=(TOPIC,), eff=None):
    return {"short_content": list(claims), "relevant_topics": list(topics),
            "effective_date": eff or {"value": None, "text": None, "quote": None}}


def test_summary_regenerates_then_removes_unverified(settings, env):  # noqa: F811
    sf, _ = env
    reg_id = _relevant_bank_mellat(settings, env)
    outs = iter([summary_out([GOOD, BAD]), summary_out([GOOD, BAD])])   # 2. denemede de düzeltmiyor
    llm = FakeLLM(settings, lambda t, m, n: [next(outs)])
    rep = summarize_pending(sf, llm, settings)
    assert rep.summarized == 1 and rep.removed_claims == 1
    feedback_call = llm.calls[1][1][-1]["content"]
    assert "DOĞRULANAMAYAN" in feedback_call and "TMSF" in feedback_call   # geri bildirimle yeniden üretim
    with sf() as s:
        reg = s.get(Regulation, reg_id)
        summ = s.scalars(select(RegulationSummary)).one()
    assert reg.processing_status == "SUMMARIZED"
    assert summ.short_content == GOOD["sentence"] and "TMSF" not in summ.short_content
    assert summ.unverified_claims == [{"kind": "sentence", "text": BAD["sentence"]}]
    assert summ.grounding_score == pytest.approx(2 / 3, abs=1e-3)       # 2 cümle + 1 konu, biri doğrulanamadı
    ev = summ.short_content_evidence[0]["evidence"][0]
    assert ev["method"] == "exact" and ev["char_end"] > ev["char_start"]
    labels = [lk["label"] for lk in summ.source_links]
    assert labels[0] == "Resmî Gazete" and "Belge dosyası (BDDK)" in labels and "Ek: Kurul Kararı" in labels


def test_summary_effective_date_and_versioning(settings, env):  # noqa: F811
    sf, _ = env
    reg_id = _relevant_bank_mellat(settings, env)
    # karar tarihi yürürlük hükmü değildir → kabul edilmez; metinde yürürlük cümlesi de yok → boş
    eff = {"value": "2026-09-18", "text": "18.09.2026", "quote": "Kurulun 18.09.2026 tarihli toplantısında"}
    llm = FakeLLM(settings, lambda t, m, n: [summary_out([GOOD], eff=eff)])
    summarize_pending(sf, llm, settings)
    with sf() as s:
        reg = s.get(Regulation, reg_id)
        assert reg.effective_date is None and s.scalars(select(RegulationSummary)).one().unverified_claims == []
    summarize_pending(sf, llm, settings, force=True)                      # yeniden üretim → v2, v1 korunur
    with sf() as s:
        rows = s.scalars(select(RegulationSummary).order_by(RegulationSummary.version)).all()
    assert [(r.version, r.is_current) for r in rows] == [(1, False), (2, True)]


def test_summary_effective_date_computed_from_publication(settings, env):  # noqa: F811
    sf, storage = env
    text = ("<html><body><p>Kişisel Verileri Koruma Kurumundan:</p><p>MADDE 1- Veri sorumluları aydınlatma "
            "yükümlülüğünü yerine getirirken bu Tebliğde belirtilen usul ve esaslara uymak zorundadır.</p>"
            "<p>MADDE 9- (1) Bu Tebliğ yayımı tarihinden itibaren altı ay sonra yürürlüğe girer.</p></body></html>")
    add_raw(sf, storage, source="KVKK", channel="mevzuat", external_id="t1", url="https://www.kvkk.gov.tr/t1",
            title="Aydınlatma Yükümlülüğü Hakkında Tebliğ", published=date(2026, 9, 10), content=text.encode())
    DocumentProcessor(settings, storage, use_default_ocr=False).process_pending(sf)
    classify_pending(sf, FakeLLM(settings, lambda t, m, n: [rel()] * n if t == "relevance" else [sev("Yüksek")]),
                     settings)
    claim = {"sentence": "Veri sorumluları yeni usul ve esaslara uymak zorundadır.",
             "quotes": ["Veri sorumluları aydınlatma yükümlülüğünü yerine getirirken bu Tebliğde belirtilen usul"]}
    # LLM yürürlük hükmünü bulamadı / yanlış tarih verdi → metindeki cümleden hesaplanır
    llm = FakeLLM(settings, lambda t, m, n: [summary_out([claim], topics=())])
    summarize_pending(sf, llm, settings)
    with sf() as s:
        reg = s.scalars(select(Regulation)).one()
        summ = s.scalars(select(RegulationSummary)).one()
    assert reg.effective_date == date(2027, 3, 10) and "altı ay sonra" in reg.effective_date_text
    assert summ.effective_date_evidence["basis"] == "relative"
    assert summ.effective_date_evidence["evidence"]["method"] == "exact"


def test_no_verified_sentence_fails_and_retries_in_summary_stage(settings, env):  # noqa: F811
    sf, _ = env
    reg_id = _relevant_bank_mellat(settings, env)
    bad_llm = FakeLLM(settings, lambda t, m, n: [summary_out([BAD], topics=())])
    rep = summarize_pending(sf, bad_llm, settings)
    assert rep.failed == 1
    with sf() as s:
        reg = s.get(Regulation, reg_id)
        assert reg.processing_status == "AI_FAILED" and reg.extra["ai_stage"] == "summary"
    # sınıflandırma, özet aşamasındaki hatayı yeniden sınıflandırmaz
    assert classify_pending(sf, FakeLLM(settings, lambda t, m, n: [rel()] * n), settings).processed == 0
    ok = FakeLLM(settings, lambda t, m, n: [summary_out([GOOD])])
    assert summarize_pending(sf, ok, settings).summarized == 1


def test_map_reduce_for_long_documents(settings, env):  # noqa: F811
    sf, _ = env
    reg_id = _relevant_bank_mellat(settings, env)
    s2 = settings.model_copy(update={"summary_max_input_chars": 300, "summary_chunk_chars": 250})

    def handler(task, messages, n):
        if task == "summary_notes":
            return [{"notes": [{"point": "İzin kaldırıldı.", "quotes": [GOOD["quotes"][0]]},
                               {"point": "Uydurma not.", "quotes": ["bu cümle kaynakta yoktur ve olmayacaktır"]}],
                     "effective_date": {"value": None, "text": None, "quote": None}}]
        user = messages[-1]["content"]
        assert "Uydurma not" not in user                                  # doğrulanamayan not birleşik özete girmez
        return [summary_out([GOOD])]

    llm = FakeLLM(s2, handler)
    rep = summarize_pending(sf, llm, s2)
    assert rep.summarized == 1 and {c[0] for c in llm.calls} == {"summary_notes", "summary"}
    with sf() as s:
        assert s.scalars(select(RegulationSummary).where(RegulationSummary.regulation_id == reg_id)).one().method \
            == "map_reduce"


def test_draft_type_is_deterministic(settings, env):  # noqa: F811
    sf, storage = env
    add_raw(sf, storage, source="BDDK", channel="duzenleme_taslaklari", external_id="1342",
            url="https://www.bddk.org.tr/Mevzuat/DokumanGetir/1342", published=date(2026, 9, 1),
            title="Bankaların Özkaynaklarına İlişkin Yönetmelikte Değişiklik Yapılmasına Dair Yönetmelik Taslağı",
            content=b"<html><body><p>Taslak metni</p></body></html>")
    DocumentProcessor(settings, storage, use_default_ocr=False).process_pending(sf)
    classify_pending(sf, FakeLLM(settings, lambda t, m, n: [rel(reg_type="Yönetmelik")] * n
                                 if t == "relevance" else [sev("Kritik")]), settings)
    with sf() as s:
        reg = s.scalars(select(Regulation)).one()
    assert reg.ai_reg_type == "Düzenleme Taslağı" and reg.severity == "Yüksek"      # taslak tavanı


def test_prompt_json_keeps_turkish_characters():
    """Notlar/örnekler prompt'a kaçışsız UTF-8 verilmeli; aksi halde model alıntıyı \\u011f dizileriyle bozuyor."""
    from app.ai.common import render

    out = render("reduce.v1.user.j2", title="T", issuer="BDDK", reg_type="Tebliğ", publish_date="2026-09-01",
                 relevance_rationale="-", feedback=None, effective=None,
                 notes=[{"point": "Amaç", "quotes": ["Bu Tebliğin amacı, bankaların menkul kıymetleştirme"]}])
    assert "Bu Tebliğin amacı, bankaların menkul kıymetleştirme" in out and "\\u011f" not in out


def test_no_text_waits_without_llm_call(settings, env):  # noqa: F811
    sf, storage = env
    from tests.test_processing import RG_PDF, fixture_bytes

    content, ctype = fixture_bytes("processing", RG_PDF)       # taranmış, OCR yok → metin boş
    add_raw(sf, storage, source="RESMI_GAZETE", channel="fihrist", external_id="20260919-5", url=RG_PDF,
            title="Bankacılık Düzenleme ve Denetleme Kurulunun 18/09/2026 Tarihli ve 11572 Sayılı Kararı",
            published=date(2026, 9, 19), content=content, content_type=ctype)
    DocumentProcessor(settings, storage, use_default_ocr=False).process_pending(sf)
    classify_pending(sf, FakeLLM(settings, lambda t, m, n: [rel()] * n if t == "relevance" else [sev()]), settings)
    llm = FakeLLM(settings, lambda t, m, n: [summary_out([GOOD])])
    rep = summarize_pending(sf, llm, settings)
    assert rep.awaiting_text == 1 and rep.failed == 0 and llm.calls == []
    with sf() as s:
        reg = s.scalars(select(Regulation)).one()
        assert reg.processing_status == "RELEVANT" and reg.extra["summary_blocked"] == "metin_yok"
