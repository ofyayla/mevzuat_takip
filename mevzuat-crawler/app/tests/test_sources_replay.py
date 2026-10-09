"""Canlı sitelerden kaydedilmiş yanıtlar (tests/fixtures/<kaynak>/) üzerinde kanal ayrıştırma testleri.

Fixture'lar `mevzuat-collect probe <KOD> --record tests/fixtures/<kod>` ile üretildi (25.09.2026). Site yapısı
değişirse bu testler değil, canlı probe/izleme (İK-7) uyarı verir; fixture'lar o gün yeniden kaydedilir.
"""
from datetime import date

import pytest

from app.collectors.http import ReplayFetcher
from app.collectors.runner import fetch_documents, list_channel
from tests.conftest import FIXTURES, RECORDED_AT


def run_channel(sources, settings, code, channel):
    source = sources.get(code)
    fetcher = ReplayFetcher(source, settings, FIXTURES / code.lower())
    return list_channel(source, source.channel(channel), fetcher, now=RECORDED_AT)


@pytest.mark.parametrize("code,channel,min_items", [
    ("RESMI_GAZETE", "fihrist", 5),
    ("BDDK", "duyurular", 100),
    ("BDDK", "mevzuat_duyurulari", 30),
    ("BDDK", "kurulus_duyurulari", 20),
    ("BDDK", "rg_kurul_kararlari", 400),
    ("BDDK", "kurul_kararlari", 400),
    ("BDDK", "duzenleme_taslaklari", 10),
    ("BDDK", "duzenlemeler", 100),
    ("TCMB", "basin_duyurulari", 10),
    ("TCMB", "mevzuat", 100),
    ("SPK", "bultenler", 20),
    ("SPK", "basin_duyurulari", 10),
    ("SPK", "mevzuat", 300),
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
    ilanlar = [i for i in res.items if "/ilanlar/" in i.url]
    # İLÂN BÖLÜMÜ bütünüyle alınmaz: yalnızca Çeşitli İlânlar'daki ilgili kurum ilanları (25.09: TCMB) gelir
    assert [(i.external_id, i.extra["ilan_veren"]) for i in ilanlar] == [
        ("20260925-4-25", "Türkiye Cumhuriyet Merkez Bankasından")]
    assert ilanlar[0].url.endswith("/2026/09/20260925-4-25.pdf") and ilanlar[0].published_at == date(2026, 9, 25)
    assert ilanlar[0].extra["section"] == "İLÂN BÖLÜMÜ"
    assert {i.published_at for i in res.items} == {date(2026, 9, 25), date(2026, 9, 24)}  # bugün + dün
    yesil = [i for i in res.items if "Yeşil Taksonomisi" in i.title]
    assert yesil and yesil[0].category == "YÖNETMELİKLER"


def test_tcmb(sources, settings):
    feed = run_channel(sources, settings, "TCMB", "basin_duyurulari")
    assert feed.items[0].published_at == date(2026, 9, 17)
    assert feed.items[0].external_id == "2026/duy2026-42"
    assert not any("&" in i.title and ";" in i.title for i in feed.items), "HTML varlıkları çözülmeli"
    assert any("’" in i.title for i in feed.items)
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


def test_resmi_gazete_ilan_filter_bddk(sources, settings):
    """17.09.2026 Çeşitli İlânlar: ~25 ilan içinden yalnızca BDDK ilanı seçilmeli (üniversite vb. atılır)."""
    from app.collectors.base import ChannelContext, ItemRef
    from app.collectors.strategies.resmi_gazete import ResmiGazeteStrategy

    source = sources.get("RESMI_GAZETE")
    ch = source.channel("fihrist")
    fetcher = ReplayFetcher(source, settings, FIXTURES / "resmi_gazete")
    url = "https://www.resmigazete.gov.tr/ilanlar/eskiilanlar/2026/09/20260917-4.htm"
    index = ItemRef("RESMI_GAZETE", "fihrist", "20260917-4", url, "c - Çeşitli İlânlar", date(2026, 9, 17),
                    extra={"issue": "20260917", "section": "İLÂN BÖLÜMÜ"})
    import re
    rx = [re.compile(r) for r in ch.params["ilan_issuers"]]
    ctx = ChannelContext(source=source, channel=ch, fetcher=fetcher, now=RECORDED_AT)
    resp = fetcher.get(url)
    items = ResmiGazeteStrategy._ilanlar(ctx, index, resp.text, resp.final_url, rx)
    assert [(i.external_id, i.extra["ilan_veren"]) for i in items] == [
        ("20260917-4-7", "Bankacılık Düzenleme ve Denetleme Kurumundan")]
    assert items[0].title == "Bankacılık Düzenleme ve Denetleme Kurumundan — c - Çeşitli İlânlar"


def test_bddk(sources, settings):
    d = run_channel(sources, settings, "BDDK", "duyurular").items[0]
    assert (d.external_id, d.published_at) == ("3303", date(2026, 9, 18))
    assert d.title.startswith("Bank Mellat Merkezi Tahran")   # tarih öneki atılmış

    k = run_channel(sources, settings, "BDDK", "kurulus_duyurulari").items[0]
    assert k.published_at == date(2026, 9, 18)
    assert k.extra == {"karar_tarihi": "18.09.2026", "karar_sayisi": "11572"}
    assert not k.title.startswith("(")

    rg = run_channel(sources, settings, "BDDK", "rg_kurul_kararlari").items[0]
    assert rg.external_id == "1347" and rg.url.endswith("/Mevzuat/DokumanGetir/1347")
    assert rg.published_at == date(2026, 9, 18) and rg.extra["karar_sayisi"] == "11572"

    kk = run_channel(sources, settings, "BDDK", "kurul_kararlari").items
    assert kk[0].title == "Bankaların Hisse Geri Alımları Hk. Kurul Kararı"
    assert kk[0].extra["karar_sayisi"] == "11571"
    tasfiye = next(i for i in kk if i.external_id == "1344")   # "(30.07.2026- 11537)" — boşluk düzensiz
    assert tasfiye.extra == {"karar_tarihi": "30.07.2026", "karar_sayisi": "11537"}

    t = run_channel(sources, settings, "BDDK", "duzenleme_taslaklari").items
    assert any("Kaldıraç Oranı" in i.title for i in t)


def test_bddk_duzenlemeler_mevzuat_gov_links(sources, settings):
    items = run_channel(sources, settings, "BDDK", "duzenlemeler").items
    by_id = {i.external_id: i for i in items}
    # Eski (Metin1.Aspx?MevzuatKod=1.5.5411&sourceXmlSearch=...) ve yeni biçim aynı kanonik kimliğe iner
    kanun = by_id["mevzuat.gov.tr:1.5.5411"]
    assert kanun.url == "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=5411&MevzuatTur=1&MevzuatTertip=5"
    kredi = by_id["mevzuat.gov.tr:7.5.40520"]
    assert kredi.title == "Bankaların Kredi İşlemlerine İlişkin Yönetmelik"
    assert kredi.extra["group"] == "Yönetmelikler"          # akordeon alt kategorisi, "(39)" atılmış
    assert any(i.extra.get("group") == "Genelgeler" for i in items)
    assert len({i.external_id for i in items}) == len(items)


def test_bddk_documents(sources, settings):
    source = sources.get("BDDK")
    fetcher = ReplayFetcher(source, settings, FIXTURES / "bddk")

    ch = source.channel("kurulus_duyurulari")
    ref = list_channel(source, ch, fetcher, now=RECORDED_AT).items[0]
    docs = fetch_documents(source, ch, ref, fetcher, max_attachments=5)
    assert [d.role for d in docs] == ["main", "attachment"]
    assert docs[1].url.startswith("https://www.bddk.org.tr/Duyuru/EkGetir/3304") and docs[1].content[:4] == b"%PDF"

    ch = source.channel("duzenlemeler")
    ref = next(i for i in list_channel(source, ch, fetcher, now=RECORDED_AT).items
               if i.external_id == "mevzuat.gov.tr:7.5.40520")
    main = fetch_documents(source, ch, ref, fetcher, max_attachments=5)[0]
    assert main.role == "main" and main.url == ref.url       # kullanıcıya kanonik adres
    assert "MevzuatFihristDetayIframe" in main.final_url      # tam metin iframe'den
    assert "Kredi" in main.content.decode("utf-8")


def test_spk_mevzuat_api(sources, settings):
    items = run_channel(sources, settings, "SPK", "mevzuat").items
    ik = items[0]
    assert ik.external_id == "IlkeKarari-384" and ik.url == "https://mevzuat.spk.gov.tr/api/IlkeKarari/File/384"
    assert ik.published_at == date(2026, 9, 22) and ik.category == "İlke Kararı"
    assert ik.extra["public_url"] == "https://mevzuat.spk.gov.tr/IlkeKarari/Dosya/384"
    assert ik.extra["kurul_toplanti_no"] == "60/1711"
    assert {i.category for i in items} >= {"Tebliğ", "Yönetmelik", "Rehber", "İlke Kararı"}
    source = sources.get("SPK")
    fetcher = ReplayFetcher(source, settings, FIXTURES / "spk")
    doc = fetch_documents(source, source.channel("mevzuat"), ik, fetcher, max_attachments=5)[0]
    assert doc.content_type == "application/pdf" and doc.content[:4] == b"%PDF"
