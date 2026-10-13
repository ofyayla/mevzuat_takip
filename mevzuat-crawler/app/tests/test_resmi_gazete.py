"""Resmî Gazete mükerrer mantığı — sentetik fihrist sayfalarıyla (canlı yapı: tests/fixtures/resmi_gazete)."""
from datetime import datetime, timezone

from app.collectors.runner import list_channel
from tests.conftest import DictFetcher

BASE = "https://www.resmigazete.gov.tr"


def fihrist(*items, section="YÜRÜTME VE İDARE BÖLÜMÜ"):
    rows = "".join(f'<div class="fihrist-item mb-1"><a href="{u}">–– {t}</a></div>' for u, t in items)
    return (f'<html><body><div id="html-content" class="html-content"><div class="card-title html-title">{section}'
            f'</div><div class="html-subtitle">TEBLİĞLER</div>{rows}'
            f'<div class="card-title html-title">İLÂN BÖLÜMÜ</div><div class="fihrist-item mb-1">'
            f'<a href="/ilanlar/eskiilanlar/2026/09/20260925-3.htm">a - İlanlar</a></div></div></body></html>')


def test_mukerrer_followed_until_missing(sources, settings):
    src = sources.get("RESMI_GAZETE")
    home = fihrist((f"{BASE}/eskiler/2026/09/20260925-1.htm", "Bugünün Tebliği"))
    pages = {
        f"{BASE}/fihrist?tarih=2026-09-25": (home, "text/html"),
        f"{BASE}/fihrist?tarih=2026-09-25&mukerrer=1": (
            fihrist((f"{BASE}/eskiler/2026/09/20260925M1-1.pdf", "Mükerrer Karar")), "text/html"),
        # olmayan 2. mükerrer → site ana sayfaya yönlendirir; oradaki maddeler bu sayıya ait değildir
        f"{BASE}/fihrist?tarih=2026-09-25&mukerrer=2": (home, "text/html", 200, f"{BASE}/"),
        f"{BASE}/fihrist?tarih=2026-09-24": (
            fihrist((f"{BASE}/eskiler/2026/09/20260924-4.htm", "Dünün Yönetmeliği")), "text/html"),
        f"{BASE}/fihrist?tarih=2026-09-24&mukerrer=1": (home, "text/html", 200, f"{BASE}/"),
    }
    f = DictFetcher(src, settings, pages)
    res = list_channel(src, src.channel("fihrist"), f, now=datetime(2026, 9, 25, 20, tzinfo=timezone.utc))
    assert [i.external_id for i in res.items] == ["20260925-1", "20260925M1-1", "20260924-4"]
    muk = res.items[1]
    assert muk.extra["mukerrer"] == 1 and muk.extra["issue"] == "20260925M1" and muk.category == "TEBLİĞLER"
    assert f"{BASE}/fihrist?tarih=2026-09-25&mukerrer=3" not in f.requested   # 2. yoksa 3. denenmez


def test_day_boundary_uses_istanbul_time(sources, settings):
    src = sources.get("RESMI_GAZETE")
    f = DictFetcher(src, settings, {})
    ch = src.channel("fihrist").model_copy(update={"params": {"lookback_days": 0, "max_mukerrer": 0}})
    # 22:30 UTC = 01:30 İstanbul → yeni günün gazetesi istenmeli
    try:
        list_channel(src, ch, f, now=datetime(2026, 9, 25, 22, 30, tzinfo=timezone.utc))
    except Exception:  # noqa: BLE001 — sayfa sözlükte yok; yalnızca istenen URL'yi kontrol ediyoruz
        pass
    assert f.requested == [f"{BASE}/fihrist?tarih=2026-09-26"]


def test_failed_secondary_pages_do_not_discard_collected_items(sources, settings):
    """Dünün fihristi ya da ilan dizini alınamazsa bugünün maddeleri korunur; liste eksik olarak işaretlenir."""
    src = sources.get("RESMI_GAZETE")
    home = fihrist((f"{BASE}/eskiler/2026/09/20260925-1.htm", "Bugünün Tebliği"))
    pages = {
        f"{BASE}/fihrist?tarih=2026-09-25": (home, "text/html"),
        f"{BASE}/fihrist?tarih=2026-09-25&mukerrer=1": (home, "text/html", 200, f"{BASE}/"),
        # 24.09 fihristi sözlükte yok → 404
    }
    ch = src.channel("fihrist").model_copy(update={"params": {
        "lookback_days": 1, "max_mukerrer": 1, "ilan_indexes": ["ilanlar"], "ilan_issuers": ["bankacılık"]}})
    res = list_channel(src, ch, DictFetcher(src, settings, pages), now=datetime(2026, 9, 25, 20, tzinfo=timezone.utc))
    assert [i.external_id for i in res.items] == ["20260925-1"]
    assert res.incomplete and any("alınamadı" in w for w in res.warnings)
    assert sum("ilan dizini" in w for w in res.warnings) == 1
