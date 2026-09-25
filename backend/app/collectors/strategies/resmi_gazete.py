"""Resmî Gazete fihrist stratejisi (asıl + mükerrer sayılar).

Canlı sitede doğrulanan yapı (25.09.2026):
- ``/fihrist?tarih=YYYY-MM-DD`` asıl sayının, ``&mukerrer=N`` N. mükerrerin fihristini döndürür.
  Olmayan bir mükerrer istenirse site ana sayfaya ("/") yönlendirir ve o günün gazetesini gösterir.
- Fihrist ``#html-content`` içinde sıralı div'lerdir: ``.html-title`` (bölüm: YÜRÜTME VE İDARE BÖLÜMÜ,
  İLÂN BÖLÜMÜ …), ``.html-subtitle`` (alt başlık: YÖNETMELİKLER, TEBLİĞLER …) ve ``.fihrist-item a`` (madde).
- Madde bağlantıları ``/eskiler/YYYY/MM/YYYYMMDD[-N|MK-N].(htm|pdf)`` biçimindedir; .htm sayfaları windows-1254.
- Site, tarayıcı olmayan istemcileri TLS parmak izinden engellediği için kaynak ``client: impersonate`` kullanır.

params:
  lookback_days: bugünle birlikte geriye kaç gün taranacak (varsayılan 1 → bugün + dün; geç yayımlanan
                 mükerrerler için)
  max_mukerrer: denenecek en yüksek mükerrer numarası (varsayılan 4)
  exclude_sections: bu bölümlerdeki maddeler alınmaz (varsayılan ["İLÂN BÖLÜMÜ"])
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from app.collectors.base import ChannelContext, ChannelResult, ItemRef
from app.collectors.dates import tr_lower
from app.collectors.strategies._html import absolutize, clean, node_signature, parse_html

_ITEM_ID = re.compile(r"/(?:eskiler|ilanlar/eskiilanlar)/\d{4}/\d{2}/(\d{8}(?:M\d+)?(?:-[\w-]+)?)\.\w+$")


def _norm_section(text: str) -> str:
    return tr_lower(clean(text)).replace("â", "a")


def issue_key(day: date, mukerrer: int) -> str:
    return day.strftime("%Y%m%d") + (f"M{mukerrer}" if mukerrer else "")


class ResmiGazeteStrategy:
    name = "resmi_gazete"

    def list_items(self, ctx: ChannelContext) -> ChannelResult:
        p = ctx.channel.params
        tz = ZoneInfo(ctx.fetcher.settings.timezone)
        today = ctx.now.astimezone(tz).date()
        excluded = {_norm_section(s) for s in p.get("exclude_sections", ["İLÂN BÖLÜMÜ"])}
        items: list[ItemRef] = []
        signatures: list[str] = []
        pages = 0
        warnings: list[str] = []
        for offset in range(p.get("lookback_days", 1) + 1):
            day = today - timedelta(days=offset)
            for muk in range(0, p.get("max_mukerrer", 4) + 1):
                params = {"tarih": day.isoformat()}
                if muk:
                    params["mukerrer"] = muk
                resp = ctx.fetcher.get(ctx.source.base_url.rstrip("/") + "/fihrist", params=params)
                pages += 1
                found, sig = self._parse(ctx, resp.text, resp.final_url, day, muk, excluded, items)
                if sig:
                    signatures.append(sig)
                if found == 0:
                    if muk == 0 and offset == 0:
                        warnings.append(f"{day}: asıl sayı fihristi boş (henüz yayımlanmamış olabilir)")
                    break  # mükerrer yoksa sonrakileri denemeye gerek yok
        return ChannelResult(items=items, pages_fetched=pages, signature=signatures[0] if signatures else "",
                             warnings=warnings)

    def _parse(self, ctx, html_text, page_url, day, muk, excluded, out) -> tuple[int, str]:
        tree = parse_html(html_text, strip_chrome=False)
        container = tree.css_first("#html-content")
        if container is None:
            return 0, ""
        key = issue_key(day, muk)
        # Başka bir güne ait fihrist (ör. olmayan mükerrer → ana sayfa) karışmasın diye URL'deki anahtar kontrol edilir
        key_rx = re.compile(rf"/{key}(?:-[\w-]+)?\.\w+$")
        section = subtitle = None
        found = 0
        item_nodes = []
        for node in container.iter():
            classes = (node.attributes.get("class") or "").split()
            if "html-title" in classes:
                section, subtitle = clean(node.text()), None
            elif "html-subtitle" in classes:
                subtitle = clean(node.text())
            elif "fihrist-item" in classes:
                a = node.css_first("a[href]")
                if a is None:
                    continue
                url = absolutize(page_url, a.attributes["href"])
                if not key_rx.search(url):
                    continue
                found += 1
                if section and _norm_section(section) in excluded:
                    continue
                m = _ITEM_ID.search(url)
                item_nodes.append(node)
                out.append(ItemRef(
                    source=ctx.source.code, channel=ctx.channel.name,
                    external_id=m.group(1) if m else url, url=url, title=clean(a.text()),
                    published_at=day, category=subtitle or section,
                    extra={"issue": key, "mukerrer": muk, "section": section, "subtitle": subtitle},
                ))
        return found, node_signature(item_nodes)
