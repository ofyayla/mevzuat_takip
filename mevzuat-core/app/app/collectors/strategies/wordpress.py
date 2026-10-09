"""WordPress REST API stratejisi (MASAK: masak.hmb.gov.tr/portal/v2/ — React ön yüzün kullandığı API).

HTML kazıma gerektirmez; ``modified_after`` ile artımlı çalışır ve içerik yanıtla birlikte gelir.

params:
  api_base: https://masak.hmb.gov.tr/portal/v2/
  endpoints: [posts, pages]
  public_link_rewrite: [kaynak_önek, hedef_önek] — API 'link' alanındaki iç host'u herkese açık adrese çevirir
  per_page: 50, max_pages: 3
  lookback_days: ilk çalıştırmada geriye bakış (varsayılan ayarlardaki collect_default_lookback_days)
  overlap_hours: artımlı çalıştırmada güvenlik payı (varsayılan 6)
"""
from __future__ import annotations

import hashlib
import html
from datetime import timedelta

from app.collectors.base import ChannelContext, ChannelResult, ItemRef
from app.collectors.dates import parse_iso_datetime
from app.collectors.strategies._html import clean

_FIELDS = "id,date,modified,slug,link,title,content,excerpt,categories,type,parent"


class WordPressApiStrategy:
    name = "wordpress_api"

    def list_items(self, ctx: ChannelContext) -> ChannelResult:
        p = ctx.channel.params
        # Yuvarlama: aynı pencere içindeki tekrar çalıştırmalar aynı sorguyu üretir (önbellek/test tekrarlanabilirliği)
        if ctx.since is not None:
            after = (ctx.since - timedelta(hours=p.get("overlap_hours", 6))).replace(minute=0, second=0, microsecond=0)
        else:
            after = (ctx.now - timedelta(days=p.get("lookback_days", ctx.fetcher.settings.collect_default_lookback_days))
                     ).replace(hour=0, minute=0, second=0, microsecond=0)
        after_param = after.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
        rewrite = p.get("public_link_rewrite")
        items: list[ItemRef] = []
        keys: set[str] = set()
        pages = 0
        for endpoint in p.get("endpoints", ["posts"]):
            for page in range(1, p.get("max_pages", 3) + 1):
                resp = ctx.fetcher.get(p["api_base"].rstrip("/") + "/" + endpoint, accept="application/json", params={
                    "per_page": p.get("per_page", 50), "page": page, "orderby": "modified", "order": "desc",
                    "modified_after": after_param, "_fields": _FIELDS,
                }, allow_status=(400,))  # WP, son sayfadan sonrası için 400 döner
                pages += 1
                data = resp.json() if resp.status == 200 else []
                if not isinstance(data, list) or not data:
                    break
                for post in data:
                    keys.update(post.keys())
                    items.append(self._item(ctx, endpoint, post, rewrite))
                if len(data) < p.get("per_page", 50):
                    break
        signature = hashlib.sha1(",".join(sorted(keys)).encode()).hexdigest()[:16] if keys else ""
        return ChannelResult(items=items, pages_fetched=pages, signature=signature)

    def _item(self, ctx: ChannelContext, endpoint: str, post: dict, rewrite) -> ItemRef:
        link = post.get("link") or ""
        if rewrite and link.startswith(rewrite[0]):
            link = rewrite[1] + link[len(rewrite[0]):]
        content = (post.get("content") or {}).get("rendered") or ""
        title = clean(html.unescape((post.get("title") or {}).get("rendered") or ""))
        published = parse_iso_datetime(post.get("date"))
        doc = f"<html><head><meta charset='utf-8'><title>{html.escape(title)}</title></head><body>" \
              f"<h1>{html.escape(title)}</h1>{content}</body></html>"
        return ItemRef(
            source=ctx.source.code, channel=ctx.channel.name, external_id=f"{endpoint}:{post['id']}",
            url=link, title=title, published_at=published.date() if published else None,
            category=ctx.channel.category, version_key=post.get("modified"),
            inline_content=doc.encode("utf-8"), inline_content_type="text/html",
            extra={"wp_type": post.get("type"), "wp_categories": post.get("categories"), "slug": post.get("slug")},
        )
