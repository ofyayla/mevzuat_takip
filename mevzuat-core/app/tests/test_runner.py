"""SourceCollector: yeni/değişen tespiti, sürümleme, ekler, dış host, erteleme, boş liste uyarısı."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.collectors.config import SourceConfig
from app.collectors.http import FetchError, TransientFetchError
from app.collectors.runner import SourceCollector
from app.collectors.store import SqlCrawlStore, merge_channel_stats
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
    return SourceCollector(source, settings, SqlCrawlStore(sf, FileSystemStorage(settings.raw_storage_dir)),
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
    assert c.store.last_success("ORNEK") == t0
    c.run(now=t0 + timedelta(hours=1))
    assert c.store.last_success("ORNEK") == t0 + timedelta(hours=1)


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


def test_history_from_decides_baseline_by_publish_date(settings):
    """Baseline sınırı: kanal ilk taramada olsa da karar yayım tarihine göre verilir; tarihsiz öğe kanala uyar."""
    src = make_source()
    rows = [("/d/1", "Eski", "24.09.2026"), ("/d/2", "Sınırda", "25.09.2026"), ("/d/3", "Yeni", "26.09.2026"),
            ("/d/4", "Tarihsiz", "")]
    pages = {LIST: (listing(*rows), "text/html")}
    pages.update({f"https://ornek.gov.tr/d/{i}": (f"<p>{i}</p>", "text/html") for i in range(1, 5)})
    c, sf = collector(settings, src, pages)
    rep = c.run(history_from=date(2026, 9, 25))
    assert rep.channels[0].baseline                      # kanal hâlâ ilk taramada
    assert {r.external_id: r.is_baseline for r in docs(sf)} == {
        "/d/1": True, "/d/2": False, "/d/3": False, "/d/4": True}


def test_history_from_does_not_flag_old_items_as_new_after_baseline_closed(settings):
    """Baseline kapanmış kanalda geriye dönük taramada sınırdan eski öğe NEW'e düşmemeli (YZ'ye gitmemeli)."""
    src = make_source()
    pages = {LIST: (listing(("/d/1", "Bir", "25.09.2026")), "text/html"),
             "https://ornek.gov.tr/d/1": ("<p>1</p>", "text/html"),
             "https://ornek.gov.tr/d/2": ("<p>2</p>", "text/html"),
             "https://ornek.gov.tr/d/3": ("<p>3</p>", "text/html")}
    c, sf = collector(settings, src, pages)
    assert c.run().channels[0].baseline_complete
    pages[LIST] = (listing(("/d/1", "Bir", "25.09.2026"), ("/d/2", "Eski", "10.07.2026"),
                           ("/d/3", "Ağustos", "05.08.2026")), "text/html")
    c.run(history_from=date(2026, 8, 1))
    got = {r.external_id: r.is_baseline for r in docs(sf)}
    assert got == {"/d/1": True, "/d/2": True, "/d/3": False}


def test_merge_channel_stats_keeps_last_signature_and_stops_early():
    rows = [{"a": {"signature": "", "error": "x", "baseline_complete": True}},
            {"a": {"signature": "s1"}, "b": {"signature": "s2"}},
            {"c": {"signature": "s3"}}]
    consumed = []
    merged = merge_channel_stats((consumed.append(r) or r for r in rows), ["a", "b"])
    assert merged["a"] == {"signature": "s1", "error": "x", "baseline_complete": True}
    assert len(consumed) == 2 and "c" not in merged      # istenen kanallar bulununca okuma durdu
    assert rows[0]["a"]["signature"] == ""                 # girdi değiştirilmedi


def test_structure_change_detected_after_channel_error(settings):
    """Hata alan kanalın boş imzası, sonraki taramada yapı karşılaştırmasını atlatmamalı."""
    other = "https://ornek.gov.tr/diger"
    src = SourceConfig.model_validate({
        "code": "ORNEK", "name": "Örnek", "base_url": "https://ornek.gov.tr",
        "channels": [{"name": "duyurular", "strategy": "css_list",
                      "params": {"urls": [LIST], "item": "ul.liste li", "title": "h5", "date": ".t"}},
                     {"name": "diger", "strategy": "css_list",
                      "params": {"urls": [other], "item": "ul.liste li", "title": "h5", "date": ".t"}}]})
    pages = {LIST: (listing(("/d/1", "Bir", "25.09.2026")), "text/html"),
             other: (listing(("/d/2", "İki", "25.09.2026")), "text/html"),
             "https://ornek.gov.tr/d/1": ("<p>1</p>", "text/html"),
             "https://ornek.gov.tr/d/2": ("<p>2</p>", "text/html")}
    c, sf = collector(settings, src, pages)
    c.run()
    saved = pages.pop(LIST)
    assert c.run().status == "partial"                   # duyurular liste hatası aldı
    pages[LIST] = (saved[0].replace("<li>", "<li class='yeni'>"), "text/html")
    rep = c.run()
    assert rep.channels[0].structure_changed and not rep.channels[1].structure_changed


def test_channel_absent_for_long_keeps_baseline_state(settings):
    """Kanal 50'den fazla taramada görünmese de baseline'ı tamamlanmış sayılmaya devam eder."""
    src = make_source()
    pages = {LIST: (listing(("/d/1", "Bir", "25.09.2026")), "text/html"),
             "https://ornek.gov.tr/d/1": ("<p>1</p>", "text/html"),
             "https://ornek.gov.tr/d/2": ("<p>2</p>", "text/html")}
    c, sf = collector(settings, src, pages)
    t0 = datetime(2026, 9, 25, 8, tzinfo=timezone.utc)
    assert c.run(now=t0).channels[0].baseline_complete
    with sf() as s:                                      # başka kanalların taramaları
        s.add_all(FetchRun(source_code="ORNEK", started_at=t0 + timedelta(hours=i + 1), status="success",
                           channels={"baska": {"signature": "x"}}) for i in range(60))
        s.commit()
    pages[LIST] = (listing(("/d/1", "Bir", "25.09.2026"), ("/d/2", "İki", "26.09.2026")), "text/html")
    rep = c.run(now=t0 + timedelta(days=5))
    assert not rep.channels[0].baseline and rep.new == 1
    assert [r.is_baseline for r in docs(sf) if r.external_id == "/d/2"] == [False]


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


# ------------------------------------------------------------------------------------------------ dayanıklılık
class FlakyFetcher(DictFetcher):
    """Belirli URL'lerde verilen istisnayı fırlatan sahte istemci (``errors`` değiştirilerek iyileştirilebilir)."""

    def __init__(self, source, settings, pages, errors):
        super().__init__(source, settings, pages)
        self.errors = errors

    def _request_with_retry(self, method, url, headers, body):
        if url in self.errors:
            self.requested.append(url)
            raise self.errors[url]
        return super()._request_with_retry(method, url, headers, body)


def flaky(settings, source, pages, errors, **kw):
    sf = make_sessionmaker(settings.database_url)
    store = SqlCrawlStore(sf, FileSystemStorage(settings.raw_storage_dir))
    return SourceCollector(source, settings, store, fetcher=FlakyFetcher(source, settings, pages, errors), **kw), sf


def run_statuses(sf):
    with sf() as s:
        return [r.status for r in s.scalars(select(FetchRun).order_by(FetchRun.id))]


def rows3():
    return [(f"/d/{i}", f"Duyuru {i}", "25.09.2026") for i in (1, 2, 3)]


def pages3():
    pages = {LIST: (listing(*rows3()), "text/html")}
    pages.update({f"https://ornek.gov.tr/d/{i}": (f"<p>{i}</p>", "text/html") for i in (1, 2, 3)})
    return pages


def test_unexpected_item_error_does_not_cut_the_run(settings):
    """Sarmalanmamış bir hata (ör. httpx RemoteProtocolError) kalan öğeleri/kanalları durdurmamalı."""
    c, sf = flaky(settings, make_source(), pages3(), {"https://ornek.gov.tr/d/2": RuntimeError("boom")})
    rep = c.run()
    ch = rep.channels[0]
    assert rep.new == 2 and ch.failed == 1 and rep.status == "partial"
    assert run_statuses(sf) == ["partial"]
    assert not ch.baseline_complete                       # geçici hata: baseline açık kalır, öğe yeniden denenir
    del c.fetcher.errors["https://ornek.gov.tr/d/2"]      # sorun giderildi
    rep = c.run()
    assert rep.new == 1 and rep.channels[0].baseline_complete
    assert all(r.is_baseline for r in docs(sf))           # geç gelen eski öğe "yeni düzenleme" olmadı


def test_run_is_always_finished_even_when_the_store_fails(settings, monkeypatch):
    c, sf = flaky(settings, make_source(), pages3(), {})
    monkeypatch.setattr(c.store, "add_version", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mongo koptu")))
    rep = c.run()
    assert rep.status == "failed" and "mongo koptu" in rep.channels[0].error
    assert run_statuses(sf) == ["failed"]

    monkeypatch.setattr(c.store, "latest_documents", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        c.run()
    assert run_statuses(sf) == ["failed", "failed"]       # hiçbir kayıt 'running' kalmadı


def test_attachments_with_identical_content_do_not_collide(settings):
    pages = {LIST: (listing(("/d/1", "Bir", "25.09.2026")), "text/html"),
             "https://ornek.gov.tr/d/1": ("<p>bir <a href='/ek/1.pdf'>a</a> <a href='/ek/2.pdf'>b</a></p>", "text/html"),
             "https://ornek.gov.tr/ek/1.pdf": (b"%PDF ayni", "application/pdf"),
             "https://ornek.gov.tr/ek/2.pdf": (b"%PDF ayni", "application/pdf")}
    c, sf = collector(settings, make_source(), pages)
    rep = c.run()
    assert rep.status == "success" and rep.new == 1
    assert [r.role for r in docs(sf)] == ["main", "attachment"]


def test_new_version_retires_attachments_of_previous_version(settings):
    pages = {LIST: (listing(("/d/1", "Bir", "25.09.2026")), "text/html"),
             "https://ornek.gov.tr/d/1": ("<p>v1 <a href='/ek/1.pdf'>a</a></p>", "text/html"),
             "https://ornek.gov.tr/ek/1.pdf": (b"%PDF bir", "application/pdf")}
    c, sf = collector(settings, make_source(), pages)
    c.run()
    pages[LIST] = (listing(("/d/1", "Bir (düzeltme)", "25.09.2026")), "text/html")
    pages["https://ornek.gov.tr/d/1"] = ("<p>v2 <a href='/ek/1.pdf'>a</a></p>", "text/html")
    c.run()
    assert [(r.role, r.version, r.is_latest) for r in docs(sf)] == [
        ("main", 1, False), ("attachment", 1, False), ("main", 2, True), ("attachment", 2, True)]


def test_empty_listing_does_not_close_baseline_until_it_persists(settings):
    """İlk taramada seçici bozuk/site boş dönerse baseline kapanmamalı; sonradan gelen birikim 'yeni' sayılmasın."""
    s2 = settings.model_copy(update={"baseline_empty_runs": 2})
    pages = {LIST: (listing(), "text/html")}
    c, sf = collector(s2, make_source(), pages)
    rep = c.run()
    assert rep.channels[0].baseline and not rep.channels[0].baseline_complete

    pages.update(pages3())                               # site düzeldi, 3 eski belge göründü
    rep = c.run()
    assert rep.new == 3 and rep.channels[0].baseline and rep.channels[0].baseline_complete
    assert all(r.is_baseline for r in docs(sf))          # YZ hattına "yeni düzenleme" olarak girmedi

    # Gerçekten boş kalan kanal: ardışık N boş koşudan sonra kapanır (aksi halde ilk gerçek öğe sessizce baseline olurdu)
    c2, _ = collector(s2, make_source().model_copy(update={"code": "BOS"}), {LIST: (listing(), "text/html")})
    assert [c2.run().channels[0].baseline_complete for _ in range(3)] == [False, True, True]


def test_source_that_may_legitimately_be_empty_closes_baseline_immediately(settings):
    src = SourceConfig.model_validate({
        "code": "ORNEK", "name": "Örnek", "base_url": "https://ornek.gov.tr", "expected": {"min_items_per_run": 0},
        "channels": [{"name": "d", "strategy": "css_list", "params": {"urls": [LIST], "item": "ul.liste li",
                                                                       "title": "h5", "date": ".t"}}]})
    c, _ = collector(settings, src, {LIST: (listing(), "text/html")})
    assert c.run().channels[0].baseline_complete


def test_transient_failure_keeps_baseline_open_but_permanent_404_does_not(settings):
    from app.collectors.http import TransientFetchError

    pages = pages3()
    del pages["https://ornek.gov.tr/d/2"]                 # kalıcı 404
    errors = {"https://ornek.gov.tr/d/1": TransientFetchError("u", "HTTP 503", 503)}
    c, sf = flaky(settings, make_source(), pages, errors)
    ch = c.run().channels[0]
    assert (ch.failed, ch.gave_up, ch.baseline_complete) == (1, 1, False)
    del c.fetcher.errors["https://ornek.gov.tr/d/1"]
    ch = c.run().channels[0]
    assert (ch.failed, ch.gave_up, ch.baseline_complete) == (0, 1, True)   # 404'lü ölü bağlantı baseline'ı tutmaz


def test_baseline_is_force_closed_after_max_runs(settings):
    from app.collectors.http import TransientFetchError

    s2 = settings.model_copy(update={"baseline_max_runs": 2})
    c, _ = flaky(s2, make_source(), pages3(), {"https://ornek.gov.tr/d/1": TransientFetchError("u", "HTTP 503", 503)})
    assert c.run().channels[0].baseline_complete is False
    ch = c.run().channels[0]
    assert ch.baseline_complete and any("zorla" in w for w in ch.warnings)


def test_incomplete_listing_keeps_baseline_open_and_marks_run_partial(settings, monkeypatch):
    from app.collectors import runner as runner_mod

    real = runner_mod.list_channel

    def incomplete(*a, **k):
        res = real(*a, **k)
        res.incomplete = True
        return res

    monkeypatch.setattr(runner_mod, "list_channel", incomplete)
    c, sf = collector(settings, make_source(), pages3())
    rep = c.run()
    assert rep.status == "partial" and not rep.channels[0].baseline_complete


def test_stop_flag_leaves_run_partial_without_closing_baseline(settings):
    c, sf = flaky(settings, make_source(), pages3(), {})
    c.should_stop = lambda: len(c.fetcher.requested) >= 2          # liste + ilk detaydan sonra dur
    rep = c.run()
    ch = rep.channels[0]
    assert ch.stopped and rep.new == 1 and rep.status == "partial" and not ch.baseline_complete
    c.should_stop = lambda: False
    assert c.run().new == 2                                         # kalan öğeler sonraki koşuda tamamlanır


def test_run_errors_explain_partial_status(settings):
    """`partial` nedeni (öğe hatası) fetch_run.errors ve RunReport.errors içinde okunabilir olmalı."""
    c, sf = flaky(settings, make_source(), pages3(), {"https://ornek.gov.tr/d/2": RuntimeError("boom")})
    rep = c.run()
    assert rep.status == "partial" and rep.errors and "1 öğe geçici hatayla indirilemedi" in rep.errors[0]
    with sf() as s:
        assert s.scalars(select(FetchRun)).one().errors == rep.errors


# ------------------------------------------------------------------------------------------------ ek borcu + devre kesici
EK1 = "https://ornek.gov.tr/ek/1.pdf"


def ek_pages(*eks):
    links = "".join(f"<a href='{e}'>ek</a>" for e in eks)
    pages = {LIST: (listing(("/d/1", "Birinci Duyuru", "24.09.2026")), "text/html"),
             "https://ornek.gov.tr/d/1": (f"<html><body>bir {links}</body></html>", "text/html")}
    pages.update({e: (b"%PDF-1.4 " + e.encode(), "application/pdf") for e in eks})
    return pages


def parent_of(sf):
    return next(r for r in docs(sf) if r.role == "main")


def test_failed_attachment_is_tracked_then_recovered_without_refetching_main(settings):
    reset = TransientFetchError(EK1, "ConnectError: Connection reset by peer")
    c, sf = flaky(settings, make_source(), ek_pages(EK1), {EK1: reset})
    rep = c.run()
    ch = rep.channels[0]
    assert rep.status == "partial" and ch.att_pending == 1
    assert any("1 ek indirilemedi" in e for e in rep.errors)
    missing = parent_of(sf).extra["missing_attachments"]
    assert len(missing) == 1 and missing[0]["url"] == EK1 and not missing[0]["permanent"]
    assert [r.role for r in docs(sf)] == ["main"]            # ek yok

    # hemen ardından: bekleme süresi dolmadı → ek için istek atılmaz, ama koşu hâlâ "partial" görünür
    c.fetcher.requested.clear()
    rep = c.run()
    assert EK1 not in c.fetcher.requested and rep.channels[0].att_pending == 1 and rep.status == "partial"

    # engel kalktı, bekleme geçti → yalnız ek alınır; ana belge yeniden indirilmez, yeni sürüm açılmaz
    del c.fetcher.errors[EK1]
    c.fetcher.requested.clear()
    rep = c.run(now=datetime.now(timezone.utc) + timedelta(hours=2))
    ch = rep.channels[0]
    assert ch.att_recovered == 1 and ch.att_pending == 0 and rep.status == "success"
    assert "https://ornek.gov.tr/d/1" not in c.fetcher.requested and EK1 in c.fetcher.requested
    rows = docs(sf)
    assert [r.role for r in rows] == ["main", "attachment"] and rows[1].parent_id == rows[0].id
    assert rows[0].version == rows[1].version == 1 and rows[1].is_latest
    assert parent_of(sf).extra["missing_attachments"] == []
    rep = c.run(now=datetime.now(timezone.utc) + timedelta(hours=3))      # artık borç yok
    assert rep.status == "success" and len(docs(sf)) == 2


def test_host_circuit_breaker_stops_hammering_an_unreachable_host(settings):
    eks = [f"https://ornek.gov.tr/ek/{i}.pdf" for i in range(1, 6)]
    reset = {e: TransientFetchError(e, "ConnectError: Connection reset by peer") for e in eks}
    c, sf = flaky(settings, make_source(), ek_pages(*eks), reset)
    rep = c.run()
    attempted = [u for u in c.fetcher.requested if u in eks]
    assert len(attempted) == settings.collect_host_breaker_threshold      # kalan 2 ek için istek atılmadı
    missing = parent_of(sf).extra["missing_attachments"]
    assert len(missing) == 5 and all(not m["permanent"] for m in missing)
    assert any("devre kesici" in m["last_error"] for m in missing[3:])
    assert rep.channels[0].att_pending == 5
    assert c.fetcher.host_blocked(eks[0])


def test_permanent_attachment_error_is_recorded_but_not_retried_and_does_not_degrade_run(settings):
    c, sf = flaky(settings, make_source(), ek_pages(EK1), {EK1: FetchError(EK1, "HTTP 404", 404)})
    rep = c.run()
    ch = rep.channels[0]
    assert rep.status == "success" and ch.att_gave_up == 1 and ch.att_pending == 0
    m = parent_of(sf).extra["missing_attachments"][0]
    assert m["permanent"] and m["next_try_at"] is None
    c.fetcher.requested.clear()
    c.run(now=datetime.now(timezone.utc) + timedelta(days=3))
    assert EK1 not in c.fetcher.requested


def test_attachment_gives_up_after_max_attempts(settings):
    settings.collect_attachment_max_attempts = 2
    c, sf = flaky(settings, make_source(), ek_pages(EK1), {EK1: TransientFetchError(EK1, "reset")})
    c.run()
    rep = c.run(now=datetime.now(timezone.utc) + timedelta(hours=2))   # 2. başarısız deneme → vazgeçildi
    m = parent_of(sf).extra["missing_attachments"][0]
    assert m["attempts"] == 2 and m["permanent"]
    assert rep.channels[0].att_gave_up == 1 and rep.channels[0].att_pending == 0


def test_attachments_over_the_limit_are_recorded_not_silently_dropped(settings):
    settings.collect_max_attachments = 1
    eks = ["https://ornek.gov.tr/ek/1.pdf", "https://ornek.gov.tr/ek/2.pdf"]
    c, sf = collector(settings, make_source(), ek_pages(*eks))
    rep = c.run()
    missing = parent_of(sf).extra["missing_attachments"]
    assert [m["url"] for m in missing] == [eks[1]] and missing[0]["permanent"] and "sınırı" in missing[0]["last_error"]
    ch = rep.channels[0]                                         # hata değil: koşu başarılı, hata listesi temiz
    assert rep.status == "success" and rep.errors == [] and ch.att_over_limit == 1 and ch.att_gave_up == 0
    assert any("ek sınırı" in w for w in ch.warnings)


# ------------------------------------------------------------------------------------------------ ek onarımı
def forget_missing(sf):
    """Eski sürümün bıraktığı hasarı taklit eder: ek kaybolmuş ama arşivde bunu gösteren kayıt yok."""
    with sf() as s:
        row = s.scalars(select(RawDocument).where(RawDocument.role == "main")).one()
        row.extra = {k: v for k, v in row.extra.items() if k != "missing_attachments"}
        s.commit()


def test_repair_recovers_attachments_lost_before_tracking_existed(settings):
    c, sf = flaky(settings, make_source(), ek_pages(EK1), {EK1: TransientFetchError(EK1, "reset")})
    c.run()
    forget_missing(sf)
    del c.fetcher.errors[EK1]
    c.fetcher.requested.clear()

    rep = c.repair_attachments(dry_run=True)                       # yalnızca sayar
    assert rep.docs_with_gaps == 1 and rep.gaps == 1 and rep.recovered == 0
    assert EK1 not in c.fetcher.requested and [r.role for r in docs(sf)] == ["main"]

    rep = c.repair_attachments()
    assert rep.recovered == 1 and rep.pending == 0 and EK1 in c.fetcher.requested
    rows = docs(sf)
    assert [r.role for r in rows] == ["main", "attachment"] and rows[1].parent_id == rows[0].id
    assert rows[1].fetch_run_id is None and rows[0].version == rows[1].version == 1
    assert parent_of(sf).extra["missing_attachments"] == []
    assert run_statuses(sf) == ["partial"]                         # onarım fetch_run açmaz (last_success kaymaz)

    c.fetcher.requested.clear()
    rep = c.repair_attachments()                                   # idempotent
    assert rep.docs_with_gaps == 0 and rep.recovered == 0 and EK1 not in c.fetcher.requested


def test_repair_ignores_backoff_but_respects_given_up_unless_reopened(settings):
    settings.collect_attachment_max_attempts = 1                   # ilk hatada vazgeçilir
    c, sf = flaky(settings, make_source(), ek_pages(EK1), {EK1: TransientFetchError(EK1, "reset")})
    c.run()
    assert parent_of(sf).extra["missing_attachments"][0]["permanent"]
    del c.fetcher.errors[EK1]
    c.fetcher.requested.clear()
    rep = c.repair_attachments()
    assert rep.gave_up == 1 and rep.recovered == 0 and EK1 not in c.fetcher.requested
    rep = c.repair_attachments(reopen=True)
    assert rep.recovered == 1 and EK1 in c.fetcher.requested
    assert parent_of(sf).extra["missing_attachments"] == []


def test_repair_failure_keeps_tracking_and_raising_the_limit_recovers_overflow(settings):
    settings.collect_max_attachments = 1
    eks = ["https://ornek.gov.tr/ek/1.pdf", "https://ornek.gov.tr/ek/2.pdf"]
    c, sf = flaky(settings, make_source(), ek_pages(*eks), {eks[1]: TransientFetchError(eks[1], "reset")})
    c.run()
    assert [m["url"] for m in parent_of(sf).extra["missing_attachments"]] == [eks[1]]       # sınır nedeniyle
    rep = c.repair_attachments(max_attachments=5)                  # ek 2 hâlâ erişilemez
    assert rep.pending == 1 and rep.recovered == 0
    m = parent_of(sf).extra["missing_attachments"][0]
    assert not m["permanent"] and m["attempts"] == 1
    del c.fetcher.errors[eks[1]]
    rep = c.repair_attachments(max_attachments=5)
    assert rep.recovered == 1 and parent_of(sf).extra["missing_attachments"] == []
    assert sorted(r.url for r in docs(sf) if r.role == "attachment") == eks


# ------------------------------------------------------------------------------------------------ kesinti telafisi (sayfalama)
def test_outage_catch_up_collects_items_that_scrolled_past_page_zero(settings):
    import json as _json

    from app.collectors.http import json_body, request_key

    base = "https://ornek.gov.tr/api/list?page={p}&size=2"
    src = SourceConfig.model_validate({"code": "ORNEK", "name": "Örnek", "base_url": "https://ornek.gov.tr", "channels": [
        {"name": "api", "strategy": "json_api", "params": {
            "method": "POST", "urls": [base.format(p=0)], "body": {}, "items_path": "content", "id": "x-{id}",
            "url": "https://ornek.gov.tr/x/{id}", "max_pages": 5}}]})

    def page(*ids):
        return _json.dumps({"content": [{"id": i, "title": f"Başlık {i}"} for i in ids]}), "application/json"

    pages = {f"https://ornek.gov.tr/x/{i}": (f"<p>{i}</p>", "text/html") for i in range(1, 7)}
    pages[request_key("POST", base.format(p=0), json_body({}))] = page(2, 1)
    c, sf = collector(settings, src, pages)
    rep = c.run()                                            # baseline: yalnız sayfa 0, geçmiş çekilmez
    assert rep.new == 2 and rep.channels[0].baseline_complete and rep.channels[0].pages == 1

    # kesinti: sitede 4 yeni öğe çıktı, bizim sayfa 0 artık tamamen yeni; eski öğeler sayfa 2'de
    for n, ids in enumerate([(6, 5), (4, 3), (2, 1)]):
        pages[request_key("POST", base.format(p=n), json_body({}))] = page(*ids)
    rep = c.run()
    ch = rep.channels[0]
    assert rep.new == 4 and ch.pages == 3 and not ch.incomplete and rep.status == "success"
    assert sorted(r.external_id for r in docs(sf)) == [f"x-{i}" for i in range(1, 7)]
    assert not any(r.is_baseline for r in docs(sf) if r.external_id in {"x-3", "x-4", "x-5", "x-6"})


# ------------------------------------------------------------------------------------------------ kanallar arası kimlik çakışması
def test_same_numeric_id_in_two_channels_does_not_overwrite_history(settings):
    """BDDK örneği: /Duyuru/Detay/951 ile /Mevzuat/DokumanGetir/951 aynı external_id'yi üretir. Biri diğerinin geçmişini
    ezip her koşuda sahte 'değişen' sürüm üretmemeli; çakışma görünür kılınmalı."""
    ch = lambda name, path: {"name": name, "strategy": "css_list", "params": {   # noqa: E731
        "urls": [f"https://ornek.gov.tr/{name}"], "item": "ul.liste li", "title": "h5", "date": ".t",
        "id_pattern": r"/(?:Duyuru|Mevzuat)/\w+/(\d+)"}}
    src = SourceConfig.model_validate({"code": "ORNEK", "name": "Örnek", "base_url": "https://ornek.gov.tr",
                                       "channels": [ch("duyuru", "Duyuru"), ch("mevzuat", "Mevzuat")]})
    pages = {"https://ornek.gov.tr/duyuru": (listing(("/Duyuru/Detay/951", "Duyuru 951", "24.09.2026")), "text/html"),
             "https://ornek.gov.tr/mevzuat": (listing(("/Mevzuat/DokumanGetir/951", "Rehber 951", "24.09.2026")),
                                              "text/html"),
             "https://ornek.gov.tr/Duyuru/Detay/951": ("<p>duyuru</p>", "text/html"),
             "https://ornek.gov.tr/Mevzuat/DokumanGetir/951": ("<p>rehber</p>", "text/html")}
    c, sf = collector(settings, src, pages)
    rep = c.run()
    assert rep.new == 1 and rep.channels[1].id_conflicts == 1 and rep.status == "partial"
    assert any("başka kanalın belgesiyle çakışıyor" in e for e in rep.errors)
    for _ in range(3):                                         # eskiden her koşuda sürüm artıyordu
        rep = c.run()
        assert rep.changed == 0 and rep.new == 0
    rows = docs(sf)
    assert [(r.channel, r.version) for r in rows] == [("duyuru", 1)]


def test_id_prefix_separates_id_spaces(settings):
    from app.collectors.strategies._html import external_id

    assert external_id("https://x/Duyuru/Detay/951", r"/Duyuru/Detay/(\d+)") == "951"
    assert external_id("https://x/Mevzuat/Detay/951", r"/Mevzuat/Detay/(\d+)", "mevzuat-") == "mevzuat-951"
    assert external_id("https://x/a/b?x=1", None, "m-") == "m-/a/b?x=1"
