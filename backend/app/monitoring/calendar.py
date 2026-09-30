"""Türkiye iş günü takvimi: hafta sonu, sabit resmî tatiller ve config/holidays.yaml'daki dini bayramlar.

``business_hours_between`` iki an arasındaki süreyi yalnızca iş günlerini sayarak saat cinsinden verir; SPK/BDDK gibi
hafta sonu yayın yapmayan kaynakların sessizliği bayram ve hafta sonunda boşuna alarm üretmesin diye.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

FIXED_HOLIDAYS = {(1, 1), (4, 23), (5, 1), (5, 19), (7, 15), (8, 30), (10, 29)}
HOLIDAYS_FILE = Path(__file__).resolve().parents[2] / "config" / "holidays.yaml"


@lru_cache(maxsize=2)
def _religious(path: str) -> frozenset[date]:
    data = yaml.safe_load(Path(path).read_text("utf-8")) if Path(path).exists() else {}
    days = set()
    for items in (data.get("religious_holidays") or {}).values():
        for h in items:
            d = h["from"] if isinstance(h["from"], date) else date.fromisoformat(h["from"])
            end = h["to"] if isinstance(h["to"], date) else date.fromisoformat(h["to"])
            while d <= end:
                days.add(d)
                d += timedelta(days=1)
    return frozenset(days)


def is_business_day(d: date, holidays_file: Path = HOLIDAYS_FILE) -> bool:
    return d.weekday() < 5 and (d.month, d.day) not in FIXED_HOLIDAYS and d not in _religious(str(holidays_file))


def business_hours_between(start: datetime, end: datetime, tz: str = "Europe/Istanbul",
                           holidays_file: Path = HOLIDAYS_FILE) -> float:
    """İş günlerine düşen saatler (tam gün 24 saat sayılır; mesai saati değil, takvim günü esaslı)."""
    if end <= start:
        return 0.0
    zone = ZoneInfo(tz)
    s, e = start.astimezone(zone), end.astimezone(zone)
    total = 0.0
    day = s.date()
    while day <= e.date():
        day_start = datetime.combine(day, time.min, tzinfo=zone)
        day_end = day_start + timedelta(days=1)
        lo, hi = max(s, day_start), min(e, day_end)
        if hi > lo and is_business_day(day, holidays_file):
            total += (hi - lo).total_seconds() / 3600
        day += timedelta(days=1)
    return total
