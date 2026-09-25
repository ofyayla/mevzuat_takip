"""Kaynaklarda görülen Türkçe tarih biçimlerinin ayrıştırılması.

Örnekler: "25 Eylül 2026 Cuma", "25 Eylül 2026, Cuma", "17 Eyl 2026 14:00:00", "24.9.2026",
"2026-09-23T12:03:51", "Wed, 23 Sep 2026 09:00:00 GMT".
"""
from __future__ import annotations

import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime

_MONTHS = {
    "ocak": 1, "oca": 1, "şubat": 2, "şub": 2, "mart": 3, "mar": 3, "nisan": 4, "nis": 4,
    "mayıs": 5, "may": 5, "haziran": 6, "haz": 6, "temmuz": 7, "tem": 7, "ağustos": 8, "ağu": 8,
    "eylül": 9, "eyl": 9, "ekim": 10, "eki": 10, "kasım": 11, "kas": 11, "aralık": 12, "ara": 12,
}

_TEXTUAL = re.compile(r"(\d{1,2})\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)\.?\s+(\d{4})")
_NUMERIC = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})")
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

DATE_TEXT_PATTERN = (
    r"\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|\d{1,2}\s+(?:Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık"
    r"|Oca|Şub|Mar|Nis|May|Haz|Tem|Ağu|Eyl|Eki|Kas|Ara)\.?\s+\d{4}"
)


def tr_lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_tr_date(text: str | None) -> date | None:
    """Metnin içindeki ilk tanınan tarihi döndürür; bulunamazsa None."""
    if not text:
        return None
    text = text.strip()
    if m := _ISO.search(text):
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    if m := _TEXTUAL.search(text):
        month = _MONTHS.get(tr_lower(m[2]))
        if month:
            return _safe_date(int(m[3]), month, int(m[1]))
    if m := _NUMERIC.search(text):
        return _safe_date(int(m[3]), int(m[2]), int(m[1]))
    try:  # RFC 822 (RSS)
        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError, IndexError):
        return None


def parse_iso_datetime(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
