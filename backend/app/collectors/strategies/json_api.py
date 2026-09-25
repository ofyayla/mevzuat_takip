"""Genel JSON liste API'si stratejisi (ör. SPK Mevzuat Sistemi: mevzuat.spk.gov.tr/api/Search/All).

Sitenin ön yüzü (React) içeriği bir JSON uç noktasından alıyorsa HTML kazımak yerine doğrudan o uç nokta okunur.
Alan eşlemesi tamamen sources.yaml'dadır; şablonlarda ``{alan}`` öğedeki JSON alanıyla doldurulur.

params:
  urls: [JSON liste adresleri]
  items_path: öğe listesinin yolu (nokta ile; boşsa kök dizi)
  id: kimlik şablonu (ör. "{contentSource}-{contentID}")
  url: detay/içerik adresi şablonu (ör. ".../api/{contentSource}/File/{contentID}")
  public_url: kullanıcıya gösterilecek adres şablonu (opsiyonel → extra.public_url)
  title: başlık alanı
  date: tarih alanları (ilk dolu olan kullanılır; ISO veya GG.AA.YYYY)
  category: kategori alanı (opsiyonel; yoksa kanalın category değeri)
  summary: özet alanı (opsiyonel)
  fields: {ad: json_alanı} — extra'ya kopyalanacak alanlar
  version_fields: sürüm anahtarını oluşturan alanlar (liste satırında değişirse içerik yeniden indirilir)
  filter: {json_alanı: [izin verilen değerler]} (opsiyonel)
"""
from __future__ import annotations

import hashlib
import string
from typing import Any

from app.collectors.base import ChannelContext, ChannelResult, ItemRef
from app.collectors.dates import parse_iso_datetime, parse_tr_date
from app.collectors.strategies._html import clean


def _fill(template: str, item: dict) -> str:
    names = [f for _, f, _, _ in string.Formatter().parse(template) if f]
    return template.format(**{n: "" if item.get(n) is None else item.get(n) for n in names})


def _dig(data: Any, path: str | None) -> Any:
    for part in (path or "").split("."):
        if part:
            data = data.get(part) if isinstance(data, dict) else None
    return data


def _date(value: Any):
    if not value or not isinstance(value, str):
        return None
    if dt := parse_iso_datetime(value):
        return dt.date()
    return parse_tr_date(value)


class JsonApiStrategy:
    name = "json_api"

    def list_items(self, ctx: ChannelContext) -> ChannelResult:
        p = ctx.channel.params
        items: list[ItemRef] = []
        keys: set[str] = set()
        seen: set[str] = set()
        pages = 0
        allowed = {k: set(v) for k, v in (p.get("filter") or {}).items()}
        for url in p["urls"]:
            resp = ctx.fetcher.get(url, accept="application/json")
            pages += 1
            data = _dig(resp.json(), p.get("items_path"))
            if not isinstance(data, list):
                continue
            for obj in data:
                if not isinstance(obj, dict):
                    continue
                keys.update(obj.keys())
                if any(obj.get(k) not in v for k, v in allowed.items()):
                    continue
                ref = self._item(ctx, obj)
                if ref and ref.external_id not in seen:
                    seen.add(ref.external_id)
                    items.append(ref)
        signature = hashlib.sha1(",".join(sorted(keys)).encode()).hexdigest()[:16] if keys else ""
        return ChannelResult(items=items, pages_fetched=pages, signature=signature)

    def _item(self, ctx: ChannelContext, obj: dict) -> ItemRef | None:
        p = ctx.channel.params
        title = clean(str(obj.get(p.get("title", "title")) or ""))
        if not title:
            return None
        published = next((d for f in p.get("date", []) if (d := _date(obj.get(f)))), None)
        extra = {name: obj.get(field) for name, field in (p.get("fields") or {}).items()
                 if obj.get(field) not in (None, "", [])}
        if p.get("public_url"):
            extra["public_url"] = _fill(p["public_url"], obj)
        version = "|".join(str(obj.get(f) or "") for f in p.get("version_fields", [])) or None
        category = clean(str(obj.get(p["category"]) or "")) if p.get("category") else None
        summary = clean(str(obj.get(p["summary"]) or "")) if p.get("summary") else None
        return ItemRef(source=ctx.source.code, channel=ctx.channel.name, external_id=_fill(p["id"], obj),
                       url=_fill(p["url"], obj), title=title, published_at=published,
                       category=category or ctx.channel.category, summary=summary or None,
                       version_key=version, extra=extra)
