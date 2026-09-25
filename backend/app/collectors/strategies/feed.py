"""RSS/Atom beslemesi stratejisi (ör. TCMB Basın Duyuruları Atom beslemesi).

params:
  urls: [besleme adresleri]
  id_pattern: URL'den kimlik çıkaran regex (opsiyonel)
"""
from __future__ import annotations

import hashlib

import feedparser

from app.collectors.base import ChannelContext, ChannelResult, ItemRef
from app.collectors.dates import parse_tr_date
from app.collectors.strategies._html import absolutize, clean, external_id
from app.collectors.strategies._urls import expand_urls


class FeedStrategy:
    name = "feed"

    def list_items(self, ctx: ChannelContext) -> ChannelResult:
        p = ctx.channel.params
        items: list[ItemRef] = []
        pages = 0
        warnings: list[str] = []
        keys: set[str] = set()
        for url in expand_urls(p["urls"], ctx.now):
            resp = ctx.fetcher.get(url, accept="application/atom+xml, application/rss+xml, application/xml")
            pages += 1
            feed = feedparser.parse(resp.content)
            if feed.bozo and not feed.entries:
                warnings.append(f"{url}: besleme ayrıştırılamadı ({feed.bozo_exception})")
            keys.add(f"{feed.version}:{','.join(sorted(feed.feed.keys()))}")
            for e in feed.entries:
                link = absolutize(ctx.source.base_url, e.get("link", ""))
                if not link:
                    continue
                if link.startswith("http://") and ctx.source.base_url.startswith("https://"):
                    link = "https://" + link.removeprefix("http://")  # TCMB beslemesi http bağlantı veriyor
                # Bazı kurumlar (TCMB) Türkçe tarih yazdığı için feedparser'ın ayrıştırmasına güvenmiyoruz
                published = parse_tr_date(e.get("published") or e.get("updated"))
                items.append(ItemRef(
                    source=ctx.source.code, channel=ctx.channel.name,
                    external_id=external_id(link, p.get("id_pattern")),
                    url=link, title=clean(e.get("title")), published_at=published,
                    category=ctx.channel.category, summary=clean(e.get("summary")) or None,
                    version_key=e.get("updated") or None,
                    extra={"feed_id": clean(e.get("id"))},
                ))
        signature = hashlib.sha1("|".join(sorted(keys)).encode()).hexdigest()[:16]
        return ChannelResult(items=items, pages_fetched=pages, signature=signature, warnings=warnings)
