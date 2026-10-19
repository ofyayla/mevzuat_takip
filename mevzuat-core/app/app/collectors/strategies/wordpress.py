"""WordPress REST API stratejisi (MASAK: masak.hmb.gov.tr/portal/v2/, HMB: www.hmb.gov.tr/portal/v2/ — React ön
yüzlerin kullandığı API).

HTML kazıma gerektirmez; ``modified_after`` ile artımlı çalışır ve içerik yanıtla birlikte gelir.

params:
  api_base: https://masak.hmb.gov.tr/portal/v2/
  endpoints: [posts, pages]
  query: {uç_nokta: {param: değer}} — uç nokta başına ek sorgu (ör. posts: {categories_exclude: "20"})
  public_link_rewrite: [kaynak_önek, hedef_önek] — API 'link' alanındaki iç host'u herkese açık adrese çevirir
  public_url: {uç_nokta: şablon} — herkese açık adres şablonu ({slug}, {id}); verilirse 'link' kullanılmaz
              (HMB: link 'api.hmb.gov.tr/2026/10/08/slug/' biçiminde, ön yüz '/duyuru/{slug}' bekliyor)
  category_map: {wp_kategori_id: kategori adı} — öğenin ilk eşleşen kategorisi; yoksa kanalın category değeri
  per_page: 50, max_pages: 3
  lookback_days: ilk çalıştırmada geriye bakış (varsayılan ayarlardaki collect_default_lookback_days)
  (geçmişi tara modunda — ctx.depth_until — ``modified_after`` sınır tarihidir ve sayfa sınırı yükseltilir)
  overlap_hours: artımlı çalıştırmada güvenlik payı (varsayılan 6)
"""
from __future__ import annotations

import hashlib
import html
from datetime import datetime, time, timedelta

from app.collectors.base import ChannelContext, ChannelResult, ItemRef
from app.collectors.dates import parse_iso_datetime
from app.collectors.strategies._html import clean, inline_document

DEEP_MAX_PAGES = 40   # geçmişi tara (ctx.depth_until): uç nokta başına sayfa üst sınırı

_FIELDS = "id,date,modified,slug,link,title,content,excerpt,categories,type,parent"


class WordPressApiStrategy:
    name = "wordpress_api"

    def list_items(self, ctx: ChannelContext) -> ChannelResult:
        p = ctx.channel.params
        # Yuvarlama: aynı pencere içindeki tekrar çalıştırmalar aynı sorguyu üretir (önbellek/test tekrarlanabilirliği)
        max_pages = p.get("max_pages", 3)
        if ctx.depth_until is not None:   # geçmişi tara: sınır tarihinden itibaren, sayfa sınırı yükseltilmiş
            after = datetime.combine(ctx.depth_until, time.min)
            max_pages = max(max_pages, DEEP_MAX_PAGES)
        elif ctx.since is not None:
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
            for page in range(1, max_pages + 1):
                resp = ctx.fetcher.get(p["api_base"].rstrip("/") + "/" + endpoint, accept="application/json", params={
                    "per_page": p.get("per_page", 50), "page": page, "orderby": "modified", "order": "desc",
                    "modified_after": after_param, "_fields": _FIELDS, **(p.get("query") or {}).get(endpoint, {}),
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
        p = ctx.channel.params
        link = post.get("link") or ""
        if template := (p.get("public_url") or {}).get(endpoint):
            link = template.format(slug=post.get("slug") or "", id=post["id"])
        elif rewrite and link.startswith(rewrite[0]):
            link = rewrite[1] + link[len(rewrite[0]):]
        content = (post.get("content") or {}).get("rendered") or ""
        title = clean(html.unescape((post.get("title") or {}).get("rendered") or ""))
        published = parse_iso_datetime(post.get("date"))
        cmap = {str(k): v for k, v in (p.get("category_map") or {}).items()}
        category = next((cmap[str(c)] for c in post.get("categories") or [] if str(c) in cmap), None)
        return ItemRef(
            source=ctx.source.code, channel=ctx.channel.name, external_id=f"{endpoint}:{post['id']}",
            url=link, title=title, published_at=published.date() if published else None,
            category=category or ctx.channel.category, version_key=post.get("modified"),
            inline_content=inline_document(title, content), inline_content_type="text/html",
            extra={"wp_type": post.get("type"), "wp_categories": post.get("categories"), "slug": post.get("slug")},
        )
