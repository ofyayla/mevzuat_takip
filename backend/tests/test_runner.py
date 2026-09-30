"""SourceCollector: yeni/değişen tespiti, sürümleme, ekler, dış host, erteleme, boş liste uyarısı."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.collectors.config import SourceConfig
from app.collectors.runner import SourceCollector
from app.db import FetchRun, RawDocument, make_sessionmaker
from app.storage import FileSystemStorage
from tests.conftest import DictFetcher

LIST = "https://ornek.gov.tr/duyurular"


def listing(*rows):
    lis = "".join(f'<li><a href="{href}"><h5>{title}</h5></a><span class="t">{d}</span></li>' for href, title, d in rows)
    return f"<html><body><ul class='liste'>{lis}</ul></body></html>"


def make_source(**channel_overrides) -> SourceConfig:
    ch = {"name": "duyurular", "strategy": "css_list", "attachment_pattern": r"\.pdf$",
          "params": {"urls": [LIST], "item": "ul.liste li", "title": "h5", "date": ".t"}}
    ch.update(channel_overrides)
    return SourceConfig.model_validate({"code": "ORNEK", "name": "Örnek Kurum", "base_url": "https://ornek.gov.tr",
                                        "channels": [ch]})


def collector(settings, source, pages):
    sf = make_sessionmaker(settings.database_url)
    return SourceCollector(source, settings, sf, FileSystemStorage(settings.raw_storage_dir),
                           fetcher=DictFetcher(source, settings, pages)), sf


def docs(sf):
    with sf() as s:
        return s.scalars(select(RawDocument).order_by(RawDocument.id)).all()


def test_new_unchanged_changed_cycle(settings):
    src = make_source()
    pages = {
        LIST: (listing(("/d/1", "Birinci Duyuru", "24.09.2026"), ("/d/2", "İkinci Duyuru", "25.09.2026")), "text/html"),
        "https://ornek.gov.tr/d/1": ("<html><body>bir <a href='/ek/1.pdf'>ek</a></body></html>", "text/html"),
        "https://ornek.gov.tr/d/2": ("<html><body>iki</body></html>", "text/html"),
        "https://ornek.gov.tr/ek/1.pdf": (b"%PDF-1.4 ek", "application/pdf"),
    }
    c, sf = collector(settings, src, pages)
    rep = c.run()
    assert rep.status == "success" and rep.new == 2
    rows = docs(sf)
    assert [r.role for r in rows] == ["main", "attachment", "main"]
    assert rows[1].parent_id == rows[0].id
    assert rows[0].published_at.isoformat() == "2026-09-24"
    assert FileSystemStorage(settings.raw_storage_dir).get(rows[1].storage_key) == b"%PDF-1.4 ek"

    # 2. çalıştırma: liste aynı → detay istenmez
    c.fetcher.requested.clear()
    rep = c.run()
    assert rep.new == rep.changed == 0 and c.fetcher.requested == [LIST]

    # 3. çalıştırma: başlık değişti ama içerik aynı → yeni sürüm açılmaz
    pages[LIST] = (listing(("/d/1", "Birinci Duyuru (güncel)", "24.09.2026"),
                           ("/d/2", "İkinci Duyuru", "25.09.2026")), "text/html")
    rep = c.run()
    assert rep.changed == 0 and len([r for r in docs(sf) if r.role == "main"]) == 2

    # 4. çalıştırma: içerik değişti → sürüm 2, eski sürüm is_latest=False
    pages[LIST] = (listing(("/d/1", "Birinci Duyuru (düzeltme)", "24.09.2026"),
                           ("/d/2", "İkinci Duyuru", "25.09.2026")), "text/html")
    pages["https://ornek.gov.tr/d/1"] = ("<html><body>bir — düzeltilmiş</body></html>", "text/html")
    rep = c.run()
    assert rep.changed == 1
    v = [r for r in docs(sf) if r.external_id == "/d/1" and r.role == "main"]
    assert [(r.version, r.is_latest) for r in v] == [(1, False), (2, True)]


def test_external_host_items_stored_as_listing_snapshot(settings):
    src = make_source()
    pages = {LIST: (listing(("https://www.resmigazete.gov.tr/eskiler/2026/09/20260925-1.pdf", "Kurul Kararı",
                             "25.09.2026")), "text/html")}
    c, sf = collector(settings, src, pages)
    rep = c.run()
    assert rep.new == 1
    row = docs(sf)[0]
    assert row.role == "listing" and row.content_type == "application/json"
    assert len(c.fetcher.requested) == 1  # dış siteye istek atılmadı


def test_budget_defers_remaining_items(settings):
    src = make_source()
    rows = [(f"/d/{i}", f"Duyuru {i}", "25.09.2026") for i in range(5)]
    pages = {LIST: (listing(*rows), "text/html")}
    pages.update({f"https://ornek.gov.tr/d/{i}": (f"<p>{i}</p>", "text/html") for i in range(5)})
    c, sf = collector(settings, src, pages)
    rep = c.run(max_details=2)
    assert rep.new == 2 and rep.channels[0].deferred == 3
    rep = c.run(max_details=10)
    assert rep.new == 3 and rep.channels[0].unchanged == 2


def test_empty_listing_sets_structure_alert_and_channel_error_is_isolated(settings):
    src = SourceConfig.model_validate({
        "code": "ORNEK", "name": "Örnek", "base_url": "https://ornek.gov.tr",
        "channels": [
            {"name": "bos", "strategy": "css_list", "params": {"urls": [LIST], "item": ".yok"}},
            {"name": "hatali", "strategy": "css_list", "params": {"urls": ["https://ornek.gov.tr/404"], "item": "li"}},
        ]})
    c, sf = collector(settings, src, {LIST: ("<html><body><p>yeni şablon</p></body></html>", "text/html")})
    rep = c.run()
    assert rep.status == "partial"
    bos, hatali = rep.channels
    assert bos.empty_listing and not bos.error
    assert hatali.error and "404" in hatali.error
    with sf() as s:
        run = s.scalars(select(FetchRun)).one()
        assert run.structure_alert and run.errors


def test_since_is_last_successful_run(settings):
    src = make_source()
    pages = {LIST: (listing(("/d/1", "Bir", "25.09.2026")), "text/html"),
             "https://ornek.gov.tr/d/1": ("<p>1</p>", "text/html")}
    c, sf = collector(settings, src, pages)
    t0 = datetime(2026, 9, 25, 8, tzinfo=timezone.utc)
    c.run(now=t0)
    with sf() as s:
        assert c._last_success(s) == t0
    c.run(now=t0 + timedelta(hours=1))
    with sf() as s:
        assert c._last_success(s) == t0 + timedelta(hours=1)


def test_first_run_is_baseline_until_backlog_done(settings):
    src = make_source()
    rows = [(f"/d/{i}", f"Duyuru {i}", "25.09.2026") for i in range(3)]
    pages = {LIST: (listing(*rows), "text/html")}
    pages.update({f"https://ornek.gov.tr/d/{i}": (f"<p>{i} <a href='/ek/{i}.pdf'>ek</a></p>", "text/html")
                  for i in range(4)})
    pages.update({f"https://ornek.gov.tr/ek/{i}.pdf": (f"%PDF {i}".encode(), "application/pdf") for i in range(4)})
    c, sf = collector(settings, src, pages)
    rep = c.run(max_details=2)                       # ilk tarama, 1 öğe ertelendi
    assert rep.channels[0].baseline and not rep.channels[0].baseline_complete
    rep = c.run(max_details=10)                      # birikim bitti
    assert rep.channels[0].baseline and rep.channels[0].baseline_complete
    # Eski içeriğin ekleri de BASELINE olmalı; yoksa YZ hattına "yeni" diye düşer
    assert {(r.role, r.is_baseline) for r in docs(sf)} == {("main", True), ("attachment", True)}
    # artık sitede yeni çıkan içerik (ve eki) normal akışa girer
    pages[LIST] = (listing(*rows, ("/d/3", "Yeni Duyuru", "26.09.2026")), "text/html")
    rep = c.run()
    assert not rep.channels[0].baseline and rep.new == 1
    new = [r for r in docs(sf) if r.external_id.startswith("/d/3")]
    assert {(r.role, r.is_baseline) for r in new} == {("main", False), ("attachment", False)}


def test_refresh_after_days_detects_in_place_update(settings):
    """Liste satırı hiç değişmeyen ama metni yerinde güncellenen belge (konsolide metin) yakalanmalı."""
    src = make_source(refresh_after_days=14, refresh_max_per_run=1)
    pages = {
        LIST: (listing(("/d/1", "Yönetmelik A", ""), ("/d/2", "Yönetmelik B", "")), "text/html"),
        "https://ornek.gov.tr/d/1": ("<html><body>A metni</body></html>", "text/html"),
        "https://ornek.gov.tr/d/2": ("<html><body>B metni</body></html>", "text/html"),
    }
    c, sf = collector(settings, src, pages)
    c.run()
    t0 = datetime.now(timezone.utc)

    # Süre dolmadan: yalnızca liste istenir
    c.fetcher.requested.clear()
    c.run(now=t0 + timedelta(days=1))
    assert c.fetcher.requested == [LIST]

    # Süre doldu, içerik aynı: yeniden indirilir ama sürüm açılmaz; çalıştırma başına en fazla 1 öğe
    c.fetcher.requested.clear()
    rep = c.run(now=t0 + timedelta(days=15))
    assert rep.channels[0].refreshed == 1 and rep.changed == 0
    assert c.fetcher.requested == [LIST, "https://ornek.gov.tr/d/1"]
    c.fetcher.requested.clear()
    c.run(now=t0 + timedelta(days=15))   # d/1 az önce kontrol edildi → sıra d/2'de
    assert c.fetcher.requested == [LIST, "https://ornek.gov.tr/d/2"]

    # d/1 metni yerinde değişti; bir sonraki kontrol döneminde yeni sürüm açılır
    pages["https://ornek.gov.tr/d/1"] = ("<html><body>A metni — değişiklik</body></html>", "text/html")
    rep = c.run(now=t0 + timedelta(days=40))
    assert rep.changed == 1
    v = [(r.version, r.is_latest) for r in docs(sf) if r.external_id == "/d/1"]
    assert v == [(1, False), (2, True)]


def test_mevzuat_gov_links_fetched_as_full_text(settings):
    src = SourceConfig.model_validate({
        "code": "ORNEK", "name": "Örnek", "base_url": "https://ornek.gov.tr",
        "allowed_hosts": ["www.mevzuat.gov.tr", "mevzuat.gov.tr"],
        "channels": [{"name": "mevzuat", "strategy": "link_pattern",
                      "params": {"urls": [LIST], "href_pattern": "mevzuat\\.gov\\.tr", "date_regex": None}}]})
    old = "http://www.mevzuat.gov.tr/Metin.Aspx?MevzuatKod=7.5.11180&MevzuatIliski=0&sourceXmlSearch=x"
    new = "https://mevzuat.gov.tr/mevzuat?MevzuatNo=11180&MevzuatTur=7&MevzuatTertip=5"
    iframe = ("https://www.mevzuat.gov.tr/anasayfa/MevzuatFihristDetayIframe?"
              "MevzuatTur=7&MevzuatNo=11180&MevzuatTertip=5")
    pages = {
        LIST: (f"<html><body><a href='{old}'>Banka Kartları Yönetmeliği</a>"
               f"<a href='{new}'>Banka Kartları Yönetmeliği</a></body></html>", "text/html"),
        iframe: ("<html><body>MADDE 1 – Amaç</body></html>", "text/html"),
    }
    c, sf = collector(settings, src, pages)
    rep = c.run()
    assert rep.new == 1                                   # iki biçim tek belge
    row = docs(sf)[0]
    assert row.external_id == "mevzuat.gov.tr:7.5.11180" and row.role == "main"
    assert row.final_url == iframe and row.extra["mevzuat_tur"] == 7
