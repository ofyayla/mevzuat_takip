"""Kanal URL şablonlarının genişletilmesi: {year} → içinde bulunulan yıl (Ocak ayında önceki yıl da eklenir)."""
from __future__ import annotations

from datetime import datetime


def expand_urls(urls: list[str], now: datetime, *, january_overlap_days: int = 15) -> list[str]:
    out: list[str] = []
    for u in urls:
        if "{year}" in u:
            years = [now.year]
            if now.month == 1 and now.day <= january_overlap_days:
                years.append(now.year - 1)
            out.extend(u.format(year=y) for y in years)
        else:
            out.append(u)
    return out
