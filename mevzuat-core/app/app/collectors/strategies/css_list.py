"""CSS seçicili liste sayfası stratejisi.

params:
  urls: [liste sayfaları; {year} şablonu desteklenir]
  item: her öğenin kapsayıcısı (CSS)
  link: öğe içindeki bağlantı (CSS, varsayılan 'a[href]')
  title: öğe içindeki başlık (CSS, varsayılan: bağlantı metni)
  date: öğe içindeki tarih elemanı (CSS, opsiyonel)
  date_regex: öğe metninde tarih arayan regex (opsiyonel; ilk grup)
  summary: özet elemanı (CSS, opsiyonel)
  fields: {ad: regex} — öğe metninden ek alanlar (ör. karar sayısı)
  id_pattern: URL'den kimlik regex'i
  pagination: {param: 'page', start: 2, max_pages: 1}  — ilk sayfa urls'deki adres; ek sayfalar ?param=N
"""
from __future__ import annotations

import re

from app.collectors.base import ChannelContext, ChannelResult, ItemRef
from app.collectors.dates import parse_tr_date
from app.collectors.strategies._html import absolutize, clean, external_id, node_signature, parse_html
from app.collectors.strategies._urls import expand_urls


class CssListStrategy:
    name = "css_list"

    def list_items(self, ctx: ChannelContext) -> ChannelResult:
        p = ctx.channel.params
        pag = p.get("pagination") or {}
        items: list[ItemRef] = []
        seen: set[str] = set()
        signatures: list[str] = []
        pages = 0
        for base in expand_urls(p["urls"], ctx.now):
            page_urls = [(base, None)]
            if pag:
                start = pag.get("start", 2)
                page_urls += [(base, {pag["param"]: n}) for n in range(start, start + pag.get("max_pages", 0))]
            for url, params in page_urls:
                resp = ctx.fetcher.get(url, params=params)
                pages += 1
                tree = parse_html(resp.text, strip_chrome=p.get("strip_chrome", True))
                nodes = tree.css(p["item"])
                signatures.append(node_signature(nodes))
                for node in nodes:
                    ref = self._item(ctx, node, resp.final_url)
                    if ref and ref.url not in seen:
                        seen.add(ref.url)
                        items.append(ref)
                if pag and not nodes:
                    break
        return ChannelResult(items=items, pages_fetched=pages, signature=signatures[0] if signatures else "")

    def _item(self, ctx: ChannelContext, node, page_url: str) -> ItemRef | None:
        p = ctx.channel.params
        a = node if node.tag == "a" and "link" not in p else node.css_first(p.get("link", "a[href]"))
        if a is None or not a.attributes.get("href"):
            return None
        href = a.attributes["href"]
        if href.startswith(("javascript:", "mailto:", "#")):
            return None
        url = absolutize(page_url, href)
        title_node = node.css_first(p["title"]) if p.get("title") else a
        title = clean(title_node.text(separator=" ")) if title_node else ""
        if not title:
            title = clean(a.attributes.get("title"))
        text = clean(node.text(separator=" "))
        published = None
        if p.get("date") and (dn := node.css_first(p["date"])):
            published = parse_tr_date(dn.text())
        if published is None and p.get("date_regex") and (m := re.search(p["date_regex"], text)):
            published = parse_tr_date(m.group(1) if m.groups() else m.group(0))
        summary = None
        if p.get("summary") and (sn := node.css_first(p["summary"])):
            summary = clean(sn.text(separator=" ")) or None
        extra = {}
        for name, rx in (p.get("fields") or {}).items():
            if m := re.search(rx, text):
                extra[name] = clean(m.group(1) if m.groups() else m.group(0))
        for rx in p.get("title_strip", []):
            title = clean(re.sub(rx, "", title))
        if p.get("title_prefix") and title:
            title = p["title_prefix"] + title
        return ItemRef(source=ctx.source.code, channel=ctx.channel.name,
                       external_id=external_id(url, p.get("id_pattern")), url=url, title=title,
                       published_at=published, category=ctx.channel.category, summary=summary, extra=extra)
