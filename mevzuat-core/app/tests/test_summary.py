"""İK-4: kaynağa dayandırma (alıntı doğrulama), yürürlük tarihi, özet hattı (FakeLLM ile, gerçek belgelerle)."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.ai.classify import classify_pending
from app.ai.grounding import SourceText, find_effective_sentence, find_quote, resolve_effective_date
from app.ai.llm_client import FakeLLM, LLMUnavailable
from app.ai.summary import AI_MAX_ATTEMPTS, split_chunks, summarize_pending
from app.db import Regulation, RegulationSummary
from app.processing.pipeline import DocumentProcessor
from tests.test_ai import LockSpy, _bank_and_tokat, _processed, handler_from, rel, sev
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


def test_effective_sentence_keeps_dotted_dates():
    pub = date(2026, 9, 24)
    # rakamlar arasındaki nokta cümleyi bitirmez: eşleşme "2027"den başlayıp tarihi kaybetmemeli
    text = "MADDE 12 – (1) Bu Yönetmelik 01.01.2027 tarihinde yürürlüğe girer.\nMADDE 13 – (1) … Kurul yürütür."
    sentence = find_effective_sentence(text)
    assert sentence == "Bu Yönetmelik 01.01.2027 tarihinde yürürlüğe girer"
    assert resolve_effective_date(sentence, pub).value == date(2027, 1, 1)
    assert find_effective_sentence("Bu Tebliğin 5.1 inci maddesi 1.7.2027 tarihinde yürürlüğe girecektir. Diğer") == \
        "Bu Tebliğin 5.1 inci maddesi 1.7.2027 tarihinde yürürlüğe girecektir"
    # yürürlük maddesi yoksa "…tarihinden itibaren uygulanır" yedektir
    genelge = "Bankalar bu esaslara uyar. Bu Genelge 01.03.2027 tarihinden itibaren uygulanır."
    sentence = find_effective_sentence(genelge)
    assert sentence == "Bu Genelge 01.03.2027 tarihinden itibaren uygulanır"
    assert resolve_effective_date(sentence, pub).value == date(2027, 3, 1)
    chk = resolve_effective_date("Bu Genelge yayımı tarihinden itibaren uygulanır", pub)
    assert (chk.value, chk.basis) == (pub, "on_publication")
    # yürürlük maddesi varken tek bir maddenin ertelenmiş uygulaması yürürlük sayılmaz
    both = ("GEÇİCİ MADDE 1 – (1) 5 inci madde 01.07.2027 tarihinden itibaren uygulanır.\n"
            "MADDE 20 – (1) Bu Yönetmelik yayımı tarihinde yürürlüğe girer.")
    assert find_effective_sentence(both) == "Bu Yönetmelik yayımı tarihinde yürürlüğe girer"


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


# ------------------------------------------------------------------------------------------------ toplu iş sırası


TOKAT = {"sentence": "Tıp fakültesi sınav notlarına ilişkin hükümler değişti.", "quotes": ["Öğrencilerin sınav notları"]}


def _two_relevant(settings, env):  # noqa: F811
    """Banka kararı + Tokat yönetmeliği, ikisi de RELEVANT (banka kaydı id sırasında önde)."""
    sf, _ = env
    _processed(settings, env)
    classify_pending(sf, FakeLLM(settings, handler_from([rel()])), settings)
    return _bank_and_tokat(sf)


def _summary_llm(settings):
    return FakeLLM(settings, lambda t, m, n: [summary_out([TOKAT], topics=()) if "Tokat" in m[-1]["content"]
                                              else summary_out([GOOD])])


def _set_extra(sf, reg_id, status, **extra):
    with sf() as s:
        reg = s.get(Regulation, reg_id)
        reg.processing_status, reg.extra = status, {**(reg.extra or {}), **extra}
        s.commit()


def test_text_waiting_regulations_do_not_block_new_ones(settings, env):  # noqa: F811
    sf, _ = env
    bank, tokat = _two_relevant(settings, env)
    _set_extra(sf, bank, "RELEVANT", summary_blocked="metin_yok")
    rep = summarize_pending(sf, _summary_llm(settings), settings, limit=1)
    assert rep.ids == [tokat]                                         # metin bekleyen kayıt sona kalır
    # sonraki koşuda metin bekleyen yine denenir (metni sonradan gelmiş olabilir)
    assert summarize_pending(sf, _summary_llm(settings), settings, limit=1).ids == [bank]


def test_exhausted_summary_failures_do_not_block_retries(settings, env):  # noqa: F811
    sf, _ = env
    bank, tokat = _two_relevant(settings, env)
    _set_extra(sf, bank, "AI_FAILED", ai_stage="summary", ai_attempts=AI_MAX_ATTEMPTS)
    _set_extra(sf, tokat, "AI_FAILED", ai_stage="summary", ai_attempts=1)
    rep = summarize_pending(sf, _summary_llm(settings), settings, limit=1)
    assert rep.ids == [tokat] and rep.processed == 1
    with sf() as s:
        assert s.get(Regulation, bank).processing_status == "AI_FAILED"


def test_unexpected_summary_error_fails_one_regulation(settings, env, monkeypatch):  # noqa: F811
    import app.ai.summary as summary_mod

    sf, _ = env
    bank, tokat = _two_relevant(settings, env)
    original = summary_mod.summarize_regulation

    def summarize(session, llm, reg, *a, **kw):
        if reg.id == bank:
            raise KeyError("beklenmedik")
        return original(session, llm, reg, *a, **kw)

    monkeypatch.setattr(summary_mod, "summarize_regulation", summarize)
    rep = summarize_pending(sf, _summary_llm(settings), settings)
    assert (rep.processed, rep.failed, rep.ids) == (2, 1, [tokat])
    with sf() as s:
        reg = s.get(Regulation, bank)
        assert reg.processing_status == "AI_FAILED" and reg.extra["ai_stage"] == "summary"
        assert reg.extra["ai_attempts"] == 1 and reg.extra["ai_error"].startswith("KeyError")


# ------------------------------------------------------------------------------------------------ ikinci tur (PR-B)


def test_llm_unavailable_does_not_count_attempt_and_stops_batch(settings, env):  # noqa: F811
    sf, _ = env
    bank, tokat = _two_relevant(settings, env)
    _set_extra(sf, bank, "AI_FAILED", ai_stage="summary", ai_attempts=1)

    def down(task, messages, n):
        raise LLMUnavailable("zaman aşımı")

    llm = FakeLLM(settings, down)
    rep = summarize_pending(sf, llm, settings)
    assert rep.unavailable and (rep.processed, rep.failed, rep.summarized) == (0, 0, 0)
    assert len(llm.calls) == 1                     # ilk kayıtta (yeni RELEVANT önce gelir) kesildi, AI_FAILED'a geçilmedi
    with sf() as s:
        failed, fresh = s.get(Regulation, bank), s.get(Regulation, tokat)
        assert failed.processing_status == "AI_FAILED" and failed.extra["ai_attempts"] == 1   # deneme artmadı
        assert fresh.processing_status == "RELEVANT" and "ai_attempts" not in (fresh.extra or {})
    assert summarize_pending(sf, FakeLLM(settings, down), settings, ids=[bank]).unavailable   # AI_FAILED kayıtta da
    with sf() as s:
        assert s.get(Regulation, bank).extra["ai_attempts"] == 1
    # sunucu geri gelince ikisi de özetlenir
    assert summarize_pending(sf, _summary_llm(settings), settings).ids == [tokat, bank]   # yeni kayıt önce
    with sf() as s:
        assert all("ai_unavailable_strikes" not in (s.get(Regulation, rid).extra or {}) for rid in (bank, tokat))


def test_locked_or_changed_regulation_is_skipped(settings, env, monkeypatch):  # noqa: F811
    sf, _ = env
    bank, tokat = _two_relevant(settings, env)
    spy = LockSpy(monkeypatch, locked={bank})                            # başka işçi banka kaydını özetliyor
    rep = summarize_pending(sf, _summary_llm(settings), settings)
    assert (rep.processed, rep.skipped, rep.ids) == (1, 1, [tokat])
    spy.assert_skip_locked(bank, tokat)
    with sf() as s:
        assert s.get(Regulation, bank).processing_status == "RELEVANT"
    # seçimden sonra durumu değişen kayıt: SQL koşulu Python'da yeniden denetlenir, LLM çağrılmaz
    monkeypatch.undo()
    _set_extra(sf, tokat, "RELEVANT")
    inner, done = _summary_llm(settings).handler, []

    def handler(task, messages, n):
        if "Mellat" in messages[-1]["content"] and not done:             # banka özetlenirken Tokat'ı başkası bitirir
            done.append(1)                                               # ilk çağrıda: dış oturum henüz yazmadı
            _set_extra(sf, tokat, "SUMMARIZED")
        return inner(task, messages, n)

    llm = FakeLLM(settings, handler)
    rep = summarize_pending(sf, llm, settings)
    assert (rep.processed, rep.skipped, rep.ids) == (1, 1, [bank])
    assert not any("Tokat" in c[1][-1]["content"] for c in llm.calls)


def _integrity_error(orig_message):
    from sqlalchemy.exc import IntegrityError

    return IntegrityError("INSERT INTO regulation_summary (regulation_id, version) VALUES (?, ?)", (1, 1),
                          Exception(orig_message))


@pytest.mark.parametrize("message", [
    "UNIQUE constraint failed: regulation_summary.regulation_id, regulation_summary.version",   # SQLite
    'duplicate key value violates unique constraint "uq_summary_version"',                      # PostgreSQL
])
def test_duplicate_summary_version_counts_as_already_done(settings, env, monkeypatch, message):  # noqa: F811
    import app.ai.summary as summary_mod

    sf, _ = env
    bank, tokat = _two_relevant(settings, env)
    original = summary_mod.summarize_regulation

    def summarize(session, llm, reg, *a, **kw):
        if reg.id == bank:
            raise _integrity_error(message)
        return original(session, llm, reg, *a, **kw)

    monkeypatch.setattr(summary_mod, "summarize_regulation", summarize)
    rep = summarize_pending(sf, _summary_llm(settings), settings)
    assert (rep.processed, rep.skipped, rep.failed, rep.ids) == (1, 1, 0, [tokat])
    with sf() as s:
        reg = s.get(Regulation, bank)
        assert reg.processing_status == "RELEVANT" and "ai_attempts" not in (reg.extra or {})


def test_other_integrity_errors_still_fail_the_regulation(settings, env, monkeypatch):  # noqa: F811
    import app.ai.summary as summary_mod

    sf, _ = env
    bank, tokat = _two_relevant(settings, env)
    original = summary_mod.summarize_regulation

    def summarize(session, llm, reg, *a, **kw):
        if reg.id == bank:
            raise _integrity_error("NOT NULL constraint failed: regulation_summary.short_content")
        return original(session, llm, reg, *a, **kw)

    monkeypatch.setattr(summary_mod, "summarize_regulation", summarize)
    rep = summarize_pending(sf, _summary_llm(settings), settings)
    assert (rep.processed, rep.skipped, rep.failed, rep.ids) == (2, 0, 1, [tokat])
    with sf() as s:
        assert s.get(Regulation, bank).extra["ai_attempts"] == 1
