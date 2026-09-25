from datetime import date

import pytest

from app.collectors.dates import parse_tr_date


@pytest.mark.parametrize("text,expected", [
    ("25 Eylül 2026 Cuma", date(2026, 9, 25)),
    ("25 Eylül 2026, Cuma", date(2026, 9, 25)),
    ("17 Eyl 2026 14:00:00", date(2026, 9, 17)),
    ("Yayımlanma : 09 Eylül 2026 Çarşamba", date(2026, 9, 9)),
    ("24.9.2026", date(2026, 9, 24)),
    ("Yayımlanma Tarihi : 05.03.2026", date(2026, 3, 5)),
    ("2026-09-23T12:03:51", date(2026, 9, 23)),
    ("Wed, 23 Sep 2026 09:00:00 GMT", date(2026, 9, 23)),
    ("1 ŞUBAT 2026", date(2026, 2, 1)),
    ("31 İlkbahar 2026", None),
    ("31.02.2026", None),
    ("", None),
    (None, None),
])
def test_parse_tr_date(text, expected):
    assert parse_tr_date(text) == expected
