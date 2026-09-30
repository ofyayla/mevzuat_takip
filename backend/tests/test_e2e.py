"""İK-8 uçtan uca senaryo ve paralel çalışma değerlendirmesi.

Senaryo (plan §10 E2E): BDDK "Kuruluş Duyuruları" kanalı iki kez taranır. İlk taramada sitede duran eski duyuru
BASELINE olur (YZ'ye gitmez); ikinci taramada gelen yeni karar (gerçek Bank Mellat PDF'i) hattın tamamından geçer:
toplama → metin çıkarma → tekilleştirme → ilgililik → özet (kaynağa dayandırılmış) → birim önerisi → portalda
onay → denetim izi → Başkanlığın manuel tespitiyle karşılaştırma raporu → kaynak sağlığı.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.classify import classify_pending
from app.ai.llm_client import FakeLLM
from app.ai.summary import summarize_pending
from app.ai.unit_matching import match_pending
from app.api.main import create_app
from app.collectors.config import SourceConfig
from app.collectors.runner import SourceCollector
from app.db import ManualDetection, RawDocument, Regulation, Source
from app.evaluation.parallel import add_detection, normalize_row, parse_rows, report
from app.monitoring.service import run_monitor
from app.processing.pipeline import DocumentProcessor
from tests.conftest import DictFetcher
from tests.test_processing import BDDK_EK, env, fixture_bytes  # noqa: F401
from tests.test_units import UNITS

LIST = "https://www.bddk.org.tr/Duyuru/Liste/48"


def _listing(*rows):
    lis = "".join(f'<li><div class="kategoriContainer"><a href="/Duyuru/Detay/{i}"><span class="text">'
                  f'<span class="gorunenTarih">{d}</span>{t}</span></a></div></li>' for i, t, d in rows)
    return f'<html><body><div class="kategoriList"><ul>{lis}</ul></div></body></html>'


def _detail(i):
    return (f'<html><body><div id="content-container"><h3>Duyuru {i}</h3>'
            f'<a href="/Duyuru/EkGetir/{i}?ekId=1">Kurul Kararı</a></div></body></html>')


def test_end_to_end(settings, env):  # noqa: F811
    sf, storage = env
    source = SourceConfig.model_validate({
        "code": "BDDK", "name": "Bankacılık Düzenleme ve Denetleme Kurumu", "base_url": "https://www.bddk.org.tr",
        "expected": {"silence_tolerance_hours": 120, "business_days_only": True},
        "channels": [{"name": "kurulus_duyurulari", "strategy": "link_pattern", "category": "Kuruluş Duyurusu",
                      "attachment_pattern": "/Duyuru/EkGetir/", "content_selector": "#content-container",
                      "params": {"urls": [LIST], "scope": ".kategoriList", "href_pattern": "/Duyuru/Detay/\\d+",
                                 "id_pattern": "/Duyuru/Detay/(\\d+)", "date_selector": ".gorunenTarih",
                                 "title_strip": ["^\\d{2}\\.\\d{2}\\.\\d{4}\\s*"],
                                 "fields": {"karar_sayisi": "\\(\\d{2}\\.\\d{2}\\.\\d{4}\\s*-\\s*(\\d+)\\)"}}}]})
    pdf, _ = fixture_bytes("bddk", BDDK_EK)
    old = ("3290", "(12.03.2026 - 11433) SLM Yatırım Bankası A.Ş.'nin kuruluş izninin iptaline ilişkin Kurul Kararı",
           "18.03.2026")
    new = ("3304", "(18.09.2026 - 11572) Bank Mellat Merkezi Tahran İstanbul Türkiye Merkez Şubesinin faaliyet "
                   "izninin kaldırılmasına ilişkin Kurul Kararı", "18.09.2026")
    base = "https://www.bddk.org.tr"
    pages = {LIST: (_listing(old), "text/html"), f"{base}/Duyuru/Detay/3290": (_detail(3290), "text/html"),
             f"{base}/Duyuru/EkGetir/3290?ekId=1": (b"%PDF-1.4 eski", "application/pdf")}

    # 1) ilk tarama: eski içerik BASELINE
    col = SourceCollector(source, settings, sf, storage, fetcher=DictFetcher(source, settings, pages))
    t1 = datetime(2026, 9, 18, 7, tzinfo=timezone.utc)
    assert col.run(now=t1).channels[0].baseline
    # 2) ikinci tarama: yeni karar
    pages[LIST] = (_listing(new, old), "text/html")
    pages[f"{base}/Duyuru/Detay/3304"] = (_detail(3304), "text/html")
    pages[f"{base}/Duyuru/EkGetir/3304?ekId=1"] = (pdf, "application/pdf")
    rep = col.run(now=t1 + timedelta(hours=10))
    assert rep.new == 1 and not rep.channels[0].baseline
    with sf() as s:
        assert {(r.external_id, r.is_baseline) for r in s.scalars(select(RawDocument).where(
            RawDocument.role == "main"))} == {("3290", True), ("3304", False)}

    # 3) işleme
    pr = DocumentProcessor(settings, storage, use_default_ocr=False).process_pending(sf)
    assert pr.failed == 0 and pr.new_regulations == 2
    # 4) YZ: yalnızca yeni kayıt sınıflandırılır
    quote = "Bank Mellat Merkezi Tahran İstanbul Türkiye Merkez Şubesinin faaliyet izninin"
    risk = UNITS["units"][0]["responsibilities"][-1]

    def handler(task, messages, n):
        if task == "relevance":
            return [{"is_relevant": True, "relevance_score": 0.8, "reg_type": "Kurul Kararı",
                     "matched_topics": ["KURUMSAL", "SERMAYE_LIKIDITE"], "matched_criteria": ["banka"],
                     "rationale": "Bir bankanın faaliyet izni kaldırıldı.", "uncertain": False}] * n
        if task == "severity":
            return [{"severity": "Orta", "rationale": "Karşı taraf bilgisi.", "criteria": []}]
        if task == "summary":
            return [{"short_content": [{"sentence": "BDDK, Bank Mellat İstanbul şubesinin faaliyet iznini kaldırdı.",
                                        "quotes": [quote]}],
                     "relevant_topics": [{"text": "Faaliyet izni", "quotes": [quote]}],
                     "effective_date": {"value": None, "text": None, "quote": None}}]
        return [{"suggestions": [{"unit_code": "RISK_YONETIMI", "score": 0.8, "matched_responsibility": risk,
                                  "reason": "Karşı taraf limiti."}]}]

    llm = FakeLLM(settings, handler)
    assert classify_pending(sf, llm, settings).relevant == 1
    assert summarize_pending(sf, llm, settings).summarized == 1
    assert match_pending(sf, llm, settings).matched == 1

    # 5) portal: liste → detay → onay
    c = TestClient(create_app(settings.model_copy(update={"cors_origins": ""}), sf))
    items = c.get("/api/v1/regulations").json()["items"]
    assert len(items) == 1 and items[0]["summary"].startswith("BDDK, Bank Mellat")
    reg_id = items[0]["id"]
    c.post(f"/api/v1/regulations/{reg_id}/views")
    d = c.post(f"/api/v1/regulations/{reg_id}/decision", json={"decision": "approve"}).json()
    assert d["status"] == "Onaylandı"
    assert [e["eventType"] for e in d["auditTrail"]] == ["detected", "viewed", "approved"]
    assert c.get("/api/v1/admin/audit/verify").json()["ok"]

    # 6) paralel çalışma: Başkanlığın manuel tespitleri (biri sistemde yok)
    csv = ("Başlık;Kurum;Yayım Tarihi;Önem;Birimler\n"
           "Bank Mellat İstanbul şubesinin faaliyet izninin kaldırılması (11572 sayılı karar);BDDK;18.09.2026;Orta;"
           "RISK_YONETIMI\n"
           "Bankaların Likidite Karşılama Oranı Hesaplamasına İlişkin Yönetmelikte Değişiklik;BDDK;17.09.2026;Kritik;\n")
    with sf() as s:
        for row in parse_rows("manuel.csv", csv.encode("utf-8")):
            add_detection(s, normalize_row(row), "Uzman")
        s.commit()
        mds = s.scalars(select(ManualDetection).order_by(ManualDetection.id)).all()
        assert mds[0].matched_regulation_id == reg_id and mds[0].match_method == "exact_key"
        r = report(s, date(2026, 9, 1), date(2026, 9, 30))
    assert r["manual"] == {"total": 2, "detected": 1, "missed": 1, "miss_rate": 0.5,
                           "by_reason": {"not_collected": 1}, "missed_items": r["manual"]["missed_items"]}
    assert r["severity_agreement"] == {"n": 1, "exact": 1.0}
    assert r["unit_accuracy"] == {"n": 1, "top1": 1.0, "any": 1.0}
    assert r["system"]["shown"] == 1 and r["system"]["unnecessary_rate"] == 0.0

    # 7) izleme: BDDK taranıyor ve yeni içerik yakın zamanda geldi → ok
    with sf() as s:
        res = run_monitor(s, settings, t1 + timedelta(hours=12))
        s.commit()
    assert next(st for st in res.statuses if st.code == "BDDK").status == "ok"


def test_report_reasons_and_threshold_sweep(settings, env):  # noqa: F811
    sf, _ = env
    with sf() as s:
        s.merge(Source(code="RESMI_GAZETE", name="RG"))

        def reg(title, status, relevant, score, **kw):
            r = Regulation(title=title, issuer=kw.get("issuer", "BDDK"), primary_source_code="BDDK", canonical_key=title,
                           publish_date=date(2026, 9, 10), processing_status=status, is_relevant=relevant,
                           relevance_score=score, classified_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
                           classification={"relevance": {"has_text": True}}, review_status=kw.get("review", "Bekliyor"),
                           extra=kw.get("extra", {}))
            s.add(r)
            s.flush()
            return r

        shown = reg("Kredi Kartı Tebliği Değişikliği", "READY", True, 0.8)
        low = reg("Faktoring Yönetmeliği Değişikliği", "IRRELEVANT", False, 0.25)
        target = reg("Kaldıraç Oranı Yönetmeliği", "READY", True, 0.9)
        reg("Kaldıraç Oranı Yönetmeliği (kopya)", "MERGED", None, None, extra={"merged_into": target.id})
        lost_into = reg("Gizli hedef", "IRRELEVANT", False, 0.1)
        reg("Zamanaşımı Tebliği", "MERGED", None, None, extra={"merged_into": lost_into.id})
        reg("Reddedilen duyuru", "READY", True, 0.35, review="Reddedildi")
        base = reg("Eski Genelge", "BASELINE", None, None)
        for title in ("Kredi Kartı Tebliği Değişikliği", "Faktoring Yönetmeliği Değişikliği",
                      "Kaldıraç Oranı Yönetmeliği (kopya)", "Zamanaşımı Tebliği", "Eski Genelge",
                      "Hiç görülmemiş düzenleme"):
            add_detection(s, {"title": title, "issuer": "BDDK", "publish_date": date(2026, 9, 10), "url": None,
                              "severity": None, "unit_codes": [], "note": None}, "Uzman")
        s.commit()
        r = report(s, date(2026, 9, 1), date(2026, 9, 30))
    assert r["manual"]["total"] == 6 and r["manual"]["detected"] == 2          # doğrudan + birleştirme hedefinden
    assert r["manual"]["by_reason"] == {"below_threshold": 1, "dedupe_lost": 1, "baseline": 1, "not_collected": 1}
    assert r["system"]["rejected"] == 1 and r["system"]["unnecessary_rate"] == 1.0
    sweep = {x["threshold"]: x for x in r["threshold_sweep"]}
    # eşik 0.2'ye inerse "Faktoring" (0.25) görünür → kaçırma azalır; 0.4'te reddedilen 0.35'lik kayıt gizlenir
    assert sweep[0.2]["missed"] == sweep[0.3]["missed"] - 1 and low.id not in sweep[0.2]["manual_hidden"]
    assert sweep[0.4]["rejected_shown"] == 0 and sweep[0.3]["rejected_shown"] == 1
    assert shown.id and base.id


def test_parse_rows_excel_and_cp1254(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Başlık", "Kurum", "Yayım Tarihi", "Birimler"])
    ws.append(["Kredi Kartı Tebliği", "TCMB", datetime(2026, 9, 1), "PERAKENDE_KREDILER; ODEME_SISTEMLERI"])
    path = tmp_path / "m.xlsx"
    wb.save(path)
    rows = [normalize_row(r) for r in parse_rows("m.xlsx", path.read_bytes())]
    assert rows == [{"title": "Kredi Kartı Tebliği", "issuer": "TCMB", "publish_date": date(2026, 9, 1), "url": None,
                     "severity": None, "unit_codes": ["PERAKENDE_KREDILER", "ODEME_SISTEMLERI"], "note": None}]
    csv = "Başlık,Tarih\nŞirket Birleşmesi,15.09.2026\n".encode("cp1254")
    assert normalize_row(parse_rows("m.csv", csv)[0])["publish_date"] == date(2026, 9, 15)
