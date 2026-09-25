"""Bağlantı deseni stratejisi — sayfadaki href'i belirli bir desene uyan tüm bağlantıları öğe sayar.

CSS sınıflarına bağımlı olmadığı için yardımcı-sınıf ağırlıklı (Tailwind) veya sık değişen şablonlarda
(TKBB, TCMB mevzuat listeleri) ve henüz canlı doğrulanamamış kaynaklarda (BDDK) daha dayanıklıdır.

params:
  urls: [sayfalar; {year} şablonu desteklenir]
  href_pattern: öğe bağlantısı regex'i (mutlak URL üzerinde aranır)
  exclude_pattern: hariç tutulacak href regex'i (opsiyonel)
  scope: aramanın yapılacağı kapsayıcı (CSS, opsiyonel; varsayılan tüm gövde)
  title_selector: başlığın, bağlantının/üst elemanının içindeki CSS seçicisi (opsiyonel)
  date_selector: tarihin, öğe kapsayıcısı içindeki CSS seçicisi (opsiyonel; date_regex'ten önce denenir)
  date_regex: bağlantı/üst eleman metninde tarih regex'i (opsiyonel)
  title_strip: başlıktan silinecek regex listesi (ör. tarih, "Yeni" etiketi)
  id_pattern: kimlik regex'i; version_pattern: sürüm anahtarı regex'i (ör. TCMB 'CACHEID=...')
  pagination: css_list ile aynı
"""
from __future__ import annotations

import re

from app.collectors.base import ChannelContext, ChannelResult, ItemRef
from app.collectors.dates import DATE_TEXT_PATTERN, parse_tr_date
from app.collectors.strategies._html import absolutize, clean, external_id, link_title, node_signature, parse_html
from app.collectors.strategies._urls import expand_urls


class LinkPatternStrategy:
    name = "link_pattern"

    def list_items(self, ctx: ChannelContext) -> ChannelResult:
        p = ctx.channel.params
        href_rx = re.compile(p["href_pattern"])
        exclude_rx = re.compile(p["exclude_pattern"]) if p.get("exclude_pattern") else None
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
                root = tree.css_first(p["scope"]) if p.get("scope") else tree.body
                if root is None:
                    continue
                containers = []
                found = 0
                for a in root.css("a[href]"):
                    link = absolutize(resp.final_url, a.attributes["href"])
                    if not href_rx.search(link) or (exclude_rx and exclude_rx.search(link)) or link in seen:
                        continue
                    ref, container = self._item(ctx, a, link)
                    if ref is None:
                        continue
                    seen.add(link)
                    containers.append(container)
                    items.append(ref)
                    found += 1
                signatures.append(node_signature(containers))
                if pag and not found:
                    break
        return ChannelResult(items=items, pages_fetched=pages, signature=signatures[0] if signatures else "")

    def _item(self, ctx: ChannelContext, a, link: str):
        p = ctx.channel.params
        title, container = link_title(a)
        if p.get("title_selector"):
            probe = a
            for _ in range(5):
                if probe is None:
                    break
                if (tn := probe.css_first(p["title_selector"])) is not None:
                    title, container = clean(tn.text(separator=" ")), probe
                    break
                probe = probe.parent
        text = clean(container.text(separator=" ")) if container is not None else title
        published = None
        if p.get("date_selector") and container is not None and (dn := container.css_first(p["date_selector"])):
            published = parse_tr_date(dn.text())
        date_rx = p.get("date_regex", f"({DATE_TEXT_PATTERN})")
        if published is None and date_rx:
            # Tarih çoğu zaman aynı satırdaki komşu hücrededir (tablo listeleri): kısa metinli üst elemanlara çıkılır
            probe, probe_text = container, text
            for _ in range(4):
                if m := re.search(date_rx, probe_text):
                    published = parse_tr_date(m.group(1) if m.groups() else m.group(0))
                    break
                probe = probe.parent if probe is not None else None
                if probe is None or probe.tag in ("body", "html"):
                    break
                probe_text = clean(probe.text(separator=" "))
                if len(probe_text) > 600:  # birden fazla öğe içeren kapsayıcıya ulaşıldı
                    break
        for rx in p.get("title_strip", []):
            title = clean(re.sub(rx, "", title))
        if not title:
            return None, container
        version = None
        if p.get("version_pattern") and (m := re.search(p["version_pattern"], link)):
            version = m.group(1) if m.groups() else m.group(0)
        return ItemRef(source=ctx.source.code, channel=ctx.channel.name,
                       external_id=external_id(link, p.get("id_pattern")), url=link, title=title,
                       published_at=published, category=ctx.channel.category, version_key=version), container
