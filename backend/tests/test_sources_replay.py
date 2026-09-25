"""Canlı sitelerden kaydedilmiş yanıtlar (tests/fixtures/<kaynak>/) üzerinde kanal ayrıştırma testleri.

Fixture'lar `mevzuat-collect probe <KOD> --record tests/fixtures/<kod>` ile üretildi (25.09.2026). Site yapısı
değişirse bu testler değil, canlı probe/izleme (İK-7) uyarı verir; fixture'lar o gün yeniden kaydedilir.
"""
from datetime import date

import pytest

from app.collectors.http import ReplayFetcher
from app.collectors.runner import list_channel
from tests.conftest import FIXTURES, RECORDED_AT


def run_channel(sources, settings, code, channel):
    source = sources.get(code)
    fetcher = ReplayFetcher(source, settings, FIXTURES / code.lower())
    return list_channel(source, source.channel(channel), fetcher, now=RECORDED_AT)


@pytest.mark.parametrize("code,channel,min_items", [
    ("RESMI_GAZETE", "fihrist", 5),
    ("TCMB", "basin_duyurulari", 10),
    ("TCMB", "mevzuat", 100),
    ("SPK", "bultenler", 20),
    ("SPK", "basin_duyurulari", 10),
    ("KVKK", "duyurular", 8),
    ("KVKK", "kurul_kararlari", 5),
    ("KVKK", "mevzuat", 10),
    ("MASAK", "icerik", 1),
    ("TICARET", "duyurular", 20),
    ("TICARET", "tuketici_mevzuati", 3),
    ("REKABET", "duyurular", 5),
    ("REKABET", "kurul_kararlari", 5),
    ("TKBB", "duyurular", 3),
    ("TKBB", "birlik_duzenlemeleri", 20),
])
def test_channel_parses_recorded_page(sources, settings, code, channel, min_items):
    res = run_channel(sources, settings, code, channel)
    assert len(res.items) >= min_items
    assert res.signature
    ids = [i.external_id for i in res.items]
    assert len(ids) == len(set(ids)), "kimlikler tekil olmalı"
    for it in res.items:
        assert it.title and len(it.title) >= 2, it   # MASAK'ta "sib" gibi kısa başlıklı gerçek sayfalar var
        assert it.title.lower() not in {"incele", "i̇ncele", "git", "i̇ndi̇r", "devamını gör"}, it
        assert it.url.startswith("https://"), it.url


def test_resmi_gazete_fihrist(sources, settings):
    res = run_channel(sources, settings, "RESMI_GAZETE", "fihrist")
    first = res.items[0]
    assert first.external_id == "20260925-1"
    assert first.published_at == date(2026, 9, 25)
    assert first.extra["section"] == "YÜRÜTME VE İDARE BÖLÜMÜ"
    assert not first.title.startswith("–")
    assert all("/ilanlar/" not in i.url for i in res.items), "İLÂN BÖLÜMÜ hariç tutulmalı"
    assert {i.published_at for i in res.items} == {date(2026, 9, 25), date(2026, 9, 24)}  # bugün + dün
    yesil = [i for i in res.items if "Yeşil Taksonomisi" in i.title]
    assert yesil and yesil[0].category == "YÖNETMELİKLER"


def test_tcmb(sources, settings):
    feed = run_channel(sources, settings, "TCMB", "basin_duyurulari")
    assert feed.items[0].published_at == date(2026, 9, 17)
    assert feed.items[0].external_id == "2026/duy2026-42"
    mev = run_channel(sources, settings, "TCMB", "mevzuat")
    with_cache_id = [i for i in mev.items if i.version_key]
    assert len(with_cache_id) >= 0.95 * len(mev.items), "CACHEID sürüm anahtarı olarak alınmalı"
    assert any("Kredi Kartı" in i.title for i in mev.items)


def test_spk(sources, settings):
    b = run_channel(sources, settings, "SPK", "bultenler").items[0]
    assert b.title == "SPK Bülteni 2026/65" and b.published_at == date(2026, 9, 25)
    d = run_channel(sources, settings, "SPK", "basin_duyurulari").items
    sure = next(i for i in d if "Süre Düzenlemesi" in i.title)
    assert sure.title == "Tasfiye Konusu Fonlara İlişkin Süre Düzenlemesi"
    assert "3 aylık süre" in sure.summary


def test_kvkk(sources, settings):
    d = run_channel(sources, settings, "KVKK", "duyurular").items
    assert d[0].published_at == date(2026, 9, 25) and d[0].external_id == "9009"
    k = run_channel(sources, settings, "KVKK", "kurul_kararlari").items[0]
    assert k.extra["karar_sayisi"] == "2026/1491" and "resmigazete" in k.url


def test_masak_wordpress(sources, settings):
    items = run_channel(sources, settings, "MASAK", "icerik").items
    post = next(i for i in items if i.external_id == "posts:6517")
    assert post.url.startswith("https://masak.hmb.gov.tr/2026/09/23/")  # api-masak → herkese açık adres
    assert post.version_key and post.inline_content and b"FATF" in post.inline_content


def test_rekabet(sources, settings):
    d = run_channel(sources, settings, "REKABET", "duyurular").items[0]
    assert d.published_at == date(2026, 9, 25)  # başlıktaki "6 Ekim 2026" değil, yayım hücresi
    k = run_channel(sources, settings, "REKABET", "kurul_kararlari").items[0]
    assert k.published_at == date(2026, 9, 24)
    assert k.extra["karar_sayisi"] == "26-08/256-91" and k.extra["karar_tarihi"] == "5.3.2026"


def test_tkbb_titles_from_container(sources, settings):
    items = run_channel(sources, settings, "TKBB", "birlik_duzenlemeleri").items
    assert any(i.title.startswith("MÜLGA - Danışma Kurulunun") for i in items)
