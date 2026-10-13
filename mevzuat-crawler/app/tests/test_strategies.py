"""Strateji parametrelerinin birim testleri (HMB/GİB/KGK için eklenen: POST gövdesi, inline HTML, uç nokta
başına sorgu ve adres şablonu, data-href)."""
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from app.collectors.config import SourceConfig
from app.collectors.http import Fetcher, Response, json_body, request_key
from app.collectors.runner import list_channel
from tests.conftest import DictFetcher

NOW = datetime(2026, 10, 13, 9, tzinfo=timezone.utc)


def source(channel: dict, **kw) -> SourceConfig:
    return SourceConfig.model_validate({"code": "X", "name": "X", "base_url": "https://www.ornek.gov.tr",
                                        "channels": [channel], **kw})


class RecordingFetcher(Fetcher):
    """Her GET isteğine ``responder(url)`` yanıtı verir; istenen adresleri saklar."""

    def __init__(self, src, settings, responder):
        super().__init__(src, settings, sleep=lambda _: None)
        self.responder, self.urls = responder, []

    def _request_with_retry(self, method, url, headers, body):
        self.urls.append(url)
        return Response(url, url, 200, {"content-type": "application/json"}, self.responder(url))


# ------------------------------------------------------------------------------------------------ json_api

def test_json_api_post_bodies_and_inline_html(settings):
    url = "https://www.ornek.gov.tr/api/list?page=0&size=5"
    ch = {"name": "c", "strategy": "json_api", "category": "Tebliğ", "params": {
        "method": "POST", "urls": [url], "body": [{"kanunId": 1}, {"kanunId": 2}],
        "items_path": "resultContainer.content", "id": "t-{id}", "url": "{siteLink}", "title": "title",
        "date": ["resmiGazeteTarih"], "inline_html": "description", "version_fields": ["updated_at"]}}
    src = source(ch)

    def page(*rows):
        return json.dumps({"resultContainer": {"content": list(rows)}}), "application/json"

    row = {"id": 7, "title": "A <Tebliğ>", "siteLink": "https://www.ornek.gov.tr/t/7", "description": "<p>metin</p>",
           "resmiGazeteTarih": "2026-10-11T00:00", "updated_at": "2026-10-11T10:28:58"}
    f = DictFetcher(src, settings, {
        request_key("POST", url, json_body({"kanunId": 1})): page(row),
        request_key("POST", url, json_body({"kanunId": 2})): page(row, {**row, "id": 8, "title": "B"}),
    })
    res = list_channel(src, src.channel("c"), f, now=NOW)
    assert f.requested == [request_key("POST", url, json_body({"kanunId": k})) for k in (1, 2)]
    assert [i.external_id for i in res.items] == ["t-7", "t-8"]          # gövdeler arası tekilleştirme
    a = res.items[0]
    assert a.url == "https://www.ornek.gov.tr/t/7" and a.published_at.isoformat() == "2026-10-11"
    assert a.version_key == "2026-10-11T10:28:58" and a.inline_content_type == "text/html"
    assert a.inline_content == ("<html><head><meta charset='utf-8'><title>A &lt;Tebliğ&gt;</title></head><body>"
                                "<h1>A &lt;Tebliğ&gt;</h1><p>metin</p></body></html>").encode()


def test_json_api_get_unchanged(settings):
    url = "https://www.ornek.gov.tr/api/all"
    ch = {"name": "c", "strategy": "json_api", "params": {"urls": [url], "id": "{id}", "url": "https://www.ornek.gov.tr/f/{id}"}}
    src = source(ch)
    f = DictFetcher(src, settings, {url: ('[{"id": 1, "title": "Başlık"}]', "application/json")})
    item = list_channel(src, src.channel("c"), f, now=NOW).items[0]
    assert f.requested == [url] and item.inline_content is None        # GET, detay ayrıca indirilir


# ------------------------------------------------------------------------------------------------ wordpress_api

def _wp_post(pid, slug, cats):
    return {"id": pid, "date": "2026-10-07T17:30:00", "modified": "2026-10-07T15:03:58", "slug": slug,
            "link": f"https://api.ornek.gov.tr/2026/10/07/{slug}/", "title": {"rendered": "Başlık &#8211; 1"},
            "content": {"rendered": "<p>x</p>"}, "categories": cats, "type": "post"}


def test_wordpress_query_public_url_and_category_map(settings):
    ch = {"name": "c", "strategy": "wordpress_api", "category": "Duyuru", "params": {
        "api_base": "https://www.ornek.gov.tr/portal/v2/", "endpoints": ["posts", "pages"],
        "query": {"posts": {"categories_exclude": "20"}},
        "public_url": {"posts": "https://www.ornek.gov.tr/duyuru/{slug}"},
        "public_link_rewrite": ["https://api.ornek.gov.tr/", "https://www.ornek.gov.tr/"],
        "category_map": {"35": "Kamu Finansmanı", 22: "Haber"}}}
    src = source(ch)

    def responder(url):
        if "/posts" in url:
            return json.dumps([_wp_post(1, "nakit", [35]), _wp_post(2, "haber", [1, 22]),
                               _wp_post(3, "diger", [5])]).encode()
        return json.dumps([{**_wp_post(9, "sayfa", []), "type": "page"}]).encode()

    f = RecordingFetcher(src, settings, responder)
    items = {i.external_id: i for i in list_channel(src, src.channel("c"), f, now=NOW).items}
    posts_q = parse_qs(urlsplit(f.urls[0]).query)
    assert posts_q["categories_exclude"] == ["20"] and posts_q["orderby"] == ["modified"]
    assert "categories_exclude" not in parse_qs(urlsplit(f.urls[1]).query)   # yalnız posts uç noktası
    assert items["posts:1"].url == "https://www.ornek.gov.tr/duyuru/nakit"     # şablon, link'in önüne geçer
    assert items["pages:9"].url == "https://www.ornek.gov.tr/2026/10/07/sayfa/"  # şablon yok → rewrite
    assert items["posts:1"].category == "Kamu Finansmanı"
    assert items["posts:2"].category == "Haber"                                  # int/str anahtar fark etmez
    assert items["posts:3"].category == "Duyuru"                                 # eşleşme yok → kanal kategorisi
    assert items["posts:1"].title == "Başlık – 1"


# ------------------------------------------------------------------------------------------------ link_pattern

def test_link_pattern_data_href(settings):
    url = "https://www.ornek.gov.tr/mevzuat"
    page = """<html><body><div class="icerik">
      <a class="x" data-href="/Detail/5132/Teblig" target="_self"><span>Yetkilendirme Tebliği</span></a>
      <a href="/Detail/5133/Egitim">Eğitim Tebliği</a>
      <a data-href="  ">boş</a>
      <a href="/Detail/5134/Sicil" data-href="/Detail/9999/Yanlis">Sicil Tebliği</a>
    </div></body></html>"""
    base = {"urls": [url], "href_pattern": "/Detail/\\d+", "id_pattern": "/Detail/(\\d+)", "date_regex": None}
    src = source({"name": "c", "strategy": "link_pattern", "params": {
        **base, "link_selector": "a[href], a[data-href]", "href_attrs": ["href", "data-href"]}})
    f = DictFetcher(src, settings, {url: (page, "text/html; charset=utf-8")})
    # selectolax virgüllü seçicide sonuçları seçici sırasıyla döndürür (belge sırası değil); sıra önemsiz
    items = sorted(list_channel(src, src.channel("c"), f, now=NOW).items, key=lambda i: i.external_id)
    assert [(i.external_id, i.title) for i in items] == [
        ("5132", "Yetkilendirme Tebliği"), ("5133", "Eğitim Tebliği"), ("5134", "Sicil Tebliği")]  # href önce
    assert items[0].url == "https://www.ornek.gov.tr/Detail/5132/Teblig"

    src = source({"name": "c", "strategy": "link_pattern", "params": base})   # varsayılan: yalnız href
    f = DictFetcher(src, settings, {url: (page, "text/html; charset=utf-8")})
    assert sorted(i.external_id for i in list_channel(src, src.channel("c"), f, now=NOW).items) == ["5133", "5134"]
