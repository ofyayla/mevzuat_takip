"""İK-2: metin çıkarma, OCR, tekilleştirme — gerçek belgelerle (tests/fixtures/processing, bddk).

Ana senaryo (canlı veriden): BDDK Kurulunun 18.09.2026 tarihli 11572 sayılı kararı (Bank Mellat faaliyet izninin
kaldırılması) üç ayrı yerde yayımlandı:
  - RG 19.09.2026, "Bankacılık Düzenleme ve Denetleme Kurulunun 18/09/2026 Tarihli ve 11572 Sayılı Kararı"
    (taranmış PDF — metin katmanı yalnızca "Resmî Gazete Sayı : …" üst bilgisi)
  - BDDK "RG'de yayımlanan Kurul Kararları" listesi: "(18.09.2026 - 11572) Bank Mellat …" (metinli PDF)
  - BDDK "Kuruluş Duyuruları": aynı başlık, detay sayfası + ek PDF
Hepsi tek düzenleme kaydına inmeli; resmi yayım tarihi RG tarihi olmalı.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from app.db import RawDocument, Regulation, RegulationSourceLink, Source, make_sessionmaker
from app.processing.dedupe import merge_regulations, split_document
from app.processing.extract import extract
from app.processing.ocr import AzureDocumentIntelligenceOcr, OcrError, OcrResult, page_ranges
from app.processing.pipeline import DocumentProcessor
from app.storage import FileSystemStorage
from tests.conftest import FIXTURES

RG_PDF = "https://www.resmigazete.gov.tr/eskiler/2026/09/20260919-5.pdf"
RG_HTM = "https://www.resmigazete.gov.tr/eskiler/2026/09/20260918-9.htm"
BDDK_KARAR = "https://www.bddk.org.tr/Mevzuat/DokumanGetir/1347"
BDDK_DETAY = "https://www.bddk.org.tr/Duyuru/Detay/3304"
BDDK_EK = "https://www.bddk.org.tr/Duyuru/EkGetir/3304?ekId=937"
KARAR_TITLE = ("Bank Mellat Merkezi Tahran İstanbul Türkiye Merkez Şubesinin faaliyet izninin kaldırılmasına "
               "ilişkin Kurul Kararı")


def fixture_bytes(folder: str, url: str) -> tuple[bytes, str]:
    m = json.loads((FIXTURES / folder / "manifest.json").read_text("utf-8"))[url]
    return (FIXTURES / folder / m["file"]).read_bytes(), m["content_type"].split(";")[0]


class FakeOcr:
    name = "fake"

    def __init__(self, pages: dict[int, str] | None = None, fail: bool = False):
        self.pages, self.fail, self.calls = pages or {}, fail, []

    def analyze(self, content, content_type, pages=None):
        self.calls.append(pages)
        if self.fail:
            raise OcrError("servis kapalı")
        return OcrResult({n: self.pages.get(n, "") for n in (pages or [1])}, confidence=0.93, word_count=40)


@pytest.fixture
def env(settings):
    sf = make_sessionmaker(settings.database_url)
    storage = FileSystemStorage(settings.raw_storage_dir)
    with sf() as s:
        for code in ("RESMI_GAZETE", "BDDK", "KVKK", "SPK"):
            s.add(Source(code=code, name=code))
        s.commit()
    return sf, storage


def add_raw(sf, storage, *, source, channel, external_id, url, title, published=None, content=b"",
            content_type="text/html", role="main", parent_id=None, baseline=False, extra=None, version=1) -> int:
    key, sha = storage.put(content)
    with sf() as s:
        raw = RawDocument(source_code=source, channel=channel, external_id=external_id, url=url, final_url=url,
                          title=title, published_at=published, content_type=content_type, content_sha256=sha,
                          size_bytes=len(content), storage_key=key, role=role, parent_id=parent_id,
                          is_baseline=baseline, extra=extra or {}, version=version,
                          fetched_at=datetime(2026, 9, 25, 18, tzinfo=timezone.utc))
        s.add(raw)
        s.commit()
        return raw.id


def add_fixture(sf, storage, folder, url, **kw) -> int:
    content, ctype = fixture_bytes(folder, url)
    return add_raw(sf, storage, url=url, content=content, content_type=ctype, **kw)


def add_bank_mellat(sf, storage) -> dict[str, int]:
    ids = {}
    ids["rg"] = add_fixture(sf, storage, "processing", RG_PDF, source="RESMI_GAZETE", channel="fihrist",
                            external_id="20260919-5", published=date(2026, 9, 19),
                            title="Bankacılık Düzenleme ve Denetleme Kurulunun 18/09/2026 Tarihli ve 11572 "
                                  "Sayılı Kararı")
    ids["karar"] = add_fixture(sf, storage, "bddk", BDDK_KARAR, source="BDDK", channel="rg_kurul_kararlari",
                               external_id="1347", published=date(2026, 9, 18), title=KARAR_TITLE,
                               extra={"karar_tarihi": "18.09.2026", "karar_sayisi": "11572"})
    ids["duyuru"] = add_fixture(sf, storage, "bddk", BDDK_DETAY, source="BDDK", channel="kurulus_duyurulari",
                                external_id="3304", published=date(2026, 9, 18), title=KARAR_TITLE,
                                extra={"karar_tarihi": "18.09.2026", "karar_sayisi": "11572"})
    ids["ek"] = add_fixture(sf, storage, "bddk", BDDK_EK, source="BDDK", channel="kurulus_duyurulari",
                            external_id="3304#ek", published=date(2026, 9, 18), title="Kurul Kararı",
                            role="attachment", parent_id=ids["duyuru"])
    return ids


def regs(sf):
    with sf() as s:
        return s.scalars(select(Regulation).order_by(Regulation.id)).all()


def links(sf):
    with sf() as s:
        return {lk.raw_document_id: lk for lk in s.scalars(select(RegulationSourceLink))}


# ------------------------------------------------------------------------------------------------ çıkarma


def test_extract_rg_htm_keeps_structure(settings):
    content, ctype = fixture_bytes("processing", RG_HTM)
    res = extract(content, ctype, "main", settings, None, content_selector="body")
    lines = res.text.split("\n")
    assert "Hazine ve Maliye Bakanlığından:" in lines          # kurum satırı ayrı satır (kurum tespiti için)
    assert any(line.startswith("MADDE 1") for line in lines)     # madde yapısı korunur
    assert "Sayı : 33374" in res.text


def test_extract_text_pdf_and_scanned_detection(settings):
    content, ctype = fixture_bytes("bddk", BDDK_KARAR)
    res = extract(content, ctype, "main", settings, None)
    assert res.method == "pdf_text" and res.page_count == 1 and not res.ocr_pending_pages
    assert "Karar Sayısı: 11572" in res.text
    # RG PDF'i taranmış: ilk sayfada ~120 karakterlik üst bilgi var ama sayfa görüntü → OCR bekliyor
    content, ctype = fixture_bytes("processing", RG_PDF)
    res = extract(content, ctype, "main", settings, None)
    assert res.ocr_pending_pages == [1]
    assert res.text == ""   # metin katmanı ToUnicode'suz fonttan gelen kontrol karakterleri: çöp sayılır


def test_extract_scanned_pdf_with_ocr(settings):
    content, ctype = fixture_bytes("processing", RG_PDF)
    ocr = FakeOcr({1: "Bankacılık Düzenleme ve Denetleme Kurumundan:\nKarar Sayısı: 11572\n" + "x" * 300})
    res = extract(content, ctype, "main", settings, ocr)
    assert ocr.calls == [[1]]                                   # yalnızca taranmış sayfa gönderilir
    assert res.method == "ocr" and res.ocr_confidence == 0.93 and "11572" in res.text


def test_extract_bddk_detail_uses_content_selector(settings):
    content, ctype = fixture_bytes("bddk", BDDK_DETAY)
    res = extract(content, ctype, "main", settings, None, content_selector="#content-container")
    assert res.method == "html_selector" and "Bank Mellat" in res.text
    assert "hoş geldiniz" not in res.text                       # trafilatura'nın seçtiği çerez penceresi değil


# ------------------------------------------------------------------------------------------------ tekilleştirme


def test_same_decision_in_rg_and_bddk_becomes_one_regulation(settings, env):
    sf, storage = env
    ids = add_bank_mellat(sf, storage)
    rep = DocumentProcessor(settings, storage, ocr=None, use_default_ocr=False).process_pending(sf)
    assert rep.failed == 0 and rep.linked == 4
    [reg] = regs(sf)
    assert reg.issuer == "BDDK" and reg.reg_type == "Kurul Kararı" and reg.processing_status == "NEW"
    assert reg.publish_date == date(2026, 9, 19)                # resmi yayım tarihi: RG
    assert reg.extra["decision_no"] == "11572" and reg.extra["rg_issue"] == "33375"
    lk = links(sf)
    assert lk[ids["rg"]].match_method == "new"
    assert lk[ids["karar"]].match_method == "exact_key" and lk[ids["karar"]].matched_key == "karar:BDDK:11572"
    assert lk[ids["duyuru"]].match_method == "exact_key"
    assert lk[ids["ek"]].match_method == "attachment"
    assert rep.new_regulations == 1 and rep.merged == 2


def test_same_source_same_title_is_not_merged(settings, env):
    sf, storage = env
    for i, d in ((1, date(2026, 8, 14)), (2, date(2026, 8, 16))):
        add_raw(sf, storage, source="BDDK", channel="duyurular", external_id=str(i), url=f"https://www.bddk.org.tr/d/{i}",
                title="Dolandırıcılık Hakkında Basın Duyurusu", published=d,
                content=f"<html><body><p>Duyuru {i}: vatandaşlarımızın dikkatine</p></body></html>".encode())
    DocumentProcessor(settings, storage, use_default_ocr=False).process_pending(sf)
    assert len(regs(sf)) == 2


def test_cross_source_title_match_and_issuer_guard(settings, env):
    sf, storage = env
    title = "Kişisel Verilerin Yurt Dışına Aktarılmasına İlişkin Usul ve Esaslar Hakkında Yönetmelik"
    body = "<html><body><p>Kişisel Verileri Koruma Kurumundan:</p><p>MADDE 1- Bu Yönetmeliğin amacı …</p></body></html>"
    add_raw(sf, storage, source="RESMI_GAZETE", channel="fihrist", external_id="20260910-3",
            url="https://www.resmigazete.gov.tr/eskiler/2026/09/20260910-3.htm", title="–– " + title,
            published=date(2026, 9, 10), content=body.encode())
    # aynı başlık, farklı kaynak → zayıf (başlık) anahtarıyla birleşir
    add_raw(sf, storage, source="KVKK", channel="mevzuat", external_id="5257-9", url="https://www.kvkk.gov.tr/x/9",
            title=title, published=date(2026, 9, 11), content=body.encode())
    # benzer başlık ama farklı kurum
    add_raw(sf, storage, source="SPK", channel="basin_duyurulari", external_id="z", url="https://spk.gov.tr/z",
            title="Yurt Dışına Aktarılmasına İlişkin Usul ve Esaslar Hakkında Yönetmelik Taslağı Duyurusu",
            published=date(2026, 9, 12), content=b"<html><body><p>Taslak duyurusu metni</p></body></html>")
    DocumentProcessor(settings, storage, use_default_ocr=False).process_pending(sf)
    r = regs(sf)
    assert len(r) == 2
    assert r[0].issuer == "KVKK" and links(sf)[2].match_method == "exact_key"
    assert links(sf)[2].matched_key.startswith("title:KVKK:")
    # farklı kurumun (SPK) benzer başlıklı duyurusu aday bile olmaz: kurum uyuşmazlığı
    assert r[1].issuer == "SPK" and not r[1].needs_dedupe_review and links(sf)[3].match_method == "new"


def test_review_band_confirmer(settings, env):
    sf, storage = env
    add_raw(sf, storage, source="RESMI_GAZETE", channel="fihrist", external_id="a", url="https://rg/a",
            title="Bankaların Kredi İşlemlerine İlişkin Yönetmelikte Değişiklik Yapılmasına Dair Yönetmelik",
            published=date(2026, 9, 10), content=b"<html><body><p>Metin A</p></body></html>")
    kw = dict(source="BDDK", channel="mevzuat_duyurulari", title="Bankaların Kredi İşlemlerine İlişkin Yönetmelik "
              "Değişikliği Hakkında Duyuru", published=date(2026, 9, 11),
              content=b"<html><body><p>Metin B farkli</p></body></html>")
    add_raw(sf, storage, external_id="b", url="https://bddk/b", **kw)
    seen = []

    def confirmer(doc, reg, score):
        seen.append(round(score, 2))
        return None                                             # karar verilemedi → insan onayı

    DocumentProcessor(settings, storage, use_default_ocr=False, confirmer=confirmer).process_pending(sf)
    r = regs(sf)
    assert seen and settings.dedupe_review_threshold <= seen[0] < settings.dedupe_auto_threshold
    assert len(r) == 2 and r[1].needs_dedupe_review and r[1].extra["dedupe_candidates"][0]["regulation_id"] == r[0].id
    # onay: birleştir
    with sf() as s:
        merge_regulations(s, r[1].id, r[0].id)
        s.commit()
    r = regs(sf)
    assert r[1].processing_status == "MERGED" and not r[0].needs_dedupe_review
    assert {lk.regulation_id for lk in links(sf).values()} == {r[0].id}

    # aynı durumda LLM "evet" derse doğrudan bağlanır (farklı bir kaynaktan üçüncü yayın)
    add_raw(sf, storage, **{**kw, "source": "KVKK", "external_id": "c", "url": "https://kvkk/c"})
    DocumentProcessor(settings, storage, use_default_ocr=False,
                      confirmer=lambda d, reg, sc: True).process_pending(sf)
    assert links(sf)[3].match_method == "llm_confirmed"


def test_new_version_and_split(settings, env):
    sf, storage = env
    ids = add_bank_mellat(sf, storage)
    p = DocumentProcessor(settings, storage, use_default_ocr=False)
    p.process_pending(sf)
    # BDDK karar PDF'inin yeni sürümü → aynı düzenleme, içerik güncellemesi kaydı
    v2 = add_raw(sf, storage, source="BDDK", channel="rg_kurul_kararlari", external_id="1347", url=BDDK_KARAR,
                 title=KARAR_TITLE, published=date(2026, 9, 18), content=b"%PDF-1.4 bozuk", version=2,
                 content_type="application/pdf", extra={"karar_sayisi": "11572"})
    rep = p.process_pending(sf)
    assert links(sf)[v2].match_method == "same_document" and rep.new_regulations == 0
    [reg] = regs(sf)
    assert reg.extra["content_updates"][0]["raw_document_id"] == v2
    with sf() as s:
        assert s.get(RawDocument, v2).extraction_method == "unsupported"   # bozuk PDF hata değil, boş metin

    # yanlış birleştirme: kuruluş duyurusunu (ve ekini) ayır
    with sf() as s:
        new_id = split_document(s, ids["duyuru"])
        s.commit()
    lk = links(sf)
    assert lk[ids["duyuru"]].regulation_id == new_id == lk[ids["ek"]].regulation_id
    assert lk[ids["karar"]].regulation_id == reg.id and lk[ids["duyuru"]].match_method == "manual"


def test_baseline_regulation_not_new(settings, env):
    sf, storage = env
    add_raw(sf, storage, source="SPK", channel="bultenler", external_id="b1", url="https://spk.gov.tr/b1",
            title="SPK Bülteni 2026/1", published=date(2026, 1, 5), baseline=True,
            content=b"<html><body><p>Eski bulten</p></body></html>")
    rep = DocumentProcessor(settings, storage, use_default_ocr=False).process_pending(sf)
    assert regs(sf)[0].processing_status == "BASELINE" and regs(sf)[0].reg_type == "Bülten"
    assert rep.new_regulations == 1


def test_ocr_failure_retries_then_gives_up(settings, env):
    sf, storage = env
    content, ctype = fixture_bytes("processing", RG_PDF)
    rid = add_raw(sf, storage, source="RESMI_GAZETE", channel="fihrist", external_id="20260919-5", url=RG_PDF,
                  title="Bankacılık Düzenleme ve Denetleme Kurulunun 18/09/2026 Tarihli ve 11572 Sayılı Kararı",
                  published=date(2026, 9, 19), content=content, content_type=ctype)
    p = DocumentProcessor(settings, storage, ocr=FakeOcr(fail=True))
    for _ in range(settings.process_max_attempts + 1):
        rep = p.process_pending(sf)
    with sf() as s:
        raw = s.get(RawDocument, rid)
        assert raw.processing_status == "EXTRACT_FAILED" and raw.extra["extract_attempts"] == 3
    assert rep.processed == 0                                   # deneme hakkı bitti, artık seçilmiyor
    # servis düzelince yeniden işleme: OCR metni + bağlama
    ok = DocumentProcessor(settings, storage, ocr=FakeOcr({1: "Bankacılık Düzenleme ve Denetleme Kurumundan:\n"
                                                               "Karar Sayısı: 11572\n" + "metin " * 80}))
    with sf() as s:
        raw = s.get(RawDocument, rid)
        raw.extra = {}
        ok.process(s, raw)
        s.commit()
        assert raw.processing_status == "LINKED" and raw.extraction_method == "ocr"
        assert s.get(Regulation, raw.regulation_id).issuer == "BDDK"


def test_reextract_fills_ocr_pending(settings, env):
    sf, storage = env
    content, ctype = fixture_bytes("processing", RG_PDF)
    rid = add_raw(sf, storage, source="RESMI_GAZETE", channel="fihrist", external_id="20260919-5", url=RG_PDF,
                  title="Kurul Kararı", published=date(2026, 9, 19), content=content, content_type=ctype)
    DocumentProcessor(settings, storage, use_default_ocr=False).process_pending(sf)
    with sf() as s:
        assert s.get(RawDocument, rid).extra["ocr_pending_pages"] == [1]
    rep = DocumentProcessor(settings, storage, ocr=FakeOcr({1: "OCR METNİ " * 40})).reextract(sf)
    with sf() as s:
        raw = s.get(RawDocument, rid)
        assert rep.processed == 1 and raw.processing_status == "LINKED" and "OCR METNİ" in raw.text
        assert "ocr_pending_pages" not in raw.extra


# ------------------------------------------------------------------------------------------------ Azure DI istemcisi

ANALYZE_RESULT = {
    "status": "succeeded",
    "analyzeResult": {
        "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
        "content": "Bankacılık Düzenleme ve Denetleme Kurumundan:\nKarar Sayısı: 11572\nİkinci sayfa",
        "pages": [
            {"pageNumber": 1, "spans": [{"offset": 0, "length": 65}],
             "words": [{"content": "Bankacılık", "confidence": 0.99}, {"content": "11572", "confidence": 0.9}]},
            {"pageNumber": 3, "spans": [{"offset": 66, "length": 13}],
             "words": [{"content": "İkinci", "confidence": 0.8}]},
        ],
    },
}


def test_azure_document_intelligence_client(settings):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(202, headers={"Operation-Location": "https://di.kurum/op/1", "Retry-After": "1"})
        if len(calls) == 2:
            return httpx.Response(200, json={"status": "running"})
        return httpx.Response(200, json=ANALYZE_RESULT)

    s = settings.model_copy(update={"ocr_provider": "azure_di", "ocr_endpoint": "https://di.kurum/",
                                    "ocr_api_key": "gizli"})
    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"Ocp-Apim-Subscription-Key": "gizli"})
    ocr = AzureDocumentIntelligenceOcr(s, client=client, sleep=lambda _: None)
    res = ocr.analyze(b"%PDF-1.4", "application/pdf", pages=[1, 3])
    post = calls[0]
    assert str(post.url).startswith("https://di.kurum/documentintelligence/documentModels/prebuilt-read:analyze?")
    assert post.url.params["api-version"] == "2024-11-30" and post.url.params["pages"] == "1,3"
    assert post.url.params["locale"] == "tr-TR" and post.headers["Ocp-Apim-Subscription-Key"] == "gizli"
    assert json.loads(post.content)["base64Source"] == "JVBERi0xLjQ="
    assert res.pages[1].startswith("Bankacılık Düzenleme") and res.pages[3] == "İkinci sayfa"
    assert res.confidence == pytest.approx((0.99 + 0.9 + 0.8) / 3, abs=1e-3)


def test_azure_client_errors(settings):
    s = settings.model_copy(update={"ocr_provider": "azure_di", "ocr_endpoint": "https://di.kurum"})

    def make(handler):
        return AzureDocumentIntelligenceOcr(s, client=httpx.Client(transport=httpx.MockTransport(handler)),
                                            sleep=lambda _: None)

    with pytest.raises(OcrError, match="401"):
        make(lambda r: httpx.Response(401, json={"error": {"code": "401"}})).analyze(b"x", "application/pdf")
    with pytest.raises(OcrError, match="başarısız"):
        make(lambda r: httpx.Response(202, headers={"Operation-Location": "https://di/op"}) if r.method == "POST"
             else httpx.Response(200, json={"status": "failed", "error": {"code": "InvalidContent"}})
             ).analyze(b"x", "application/pdf")


def test_page_ranges():
    assert page_ranges([1, 2, 3, 7, 9, 10]) == "1-3,7,9-10" and page_ranges([5]) == "5"
