"""mevzuat.gov.tr bağlantı biçimlerinin kanonik kimliğe/adrese çevrilmesi."""
import pytest

from app.collectors.resolvers import resolve


@pytest.mark.parametrize("url", [
    "http://www.mevzuat.gov.tr/Metin.Aspx?MevzuatKod=7.5.11180&MevzuatIliski=0&sourceXmlSearch=banka kartları",
    "http://www.mevzuat.gov.tr/Metin1.Aspx?MevzuatKod=7.5.11180&MevzuatIliski=0&Tur=7&Tertip=5&No=11180",
    "https://mevzuat.gov.tr/mevzuat?MevzuatNo=11180&MevzuatTur=7&MevzuatTertip=5",
    "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=11180&MevzuatTur=7&MevzuatTertip=5 ",
    "https://www.mevzuat.gov.tr/MevzuatMetin/7.5.11180.pdf",
])
def test_mevzuat_gov_variants_resolve_to_same_document(url):
    res = resolve(url)
    assert res.canonical_id == "mevzuat.gov.tr:7.5.11180"
    assert res.public_url == "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=11180&MevzuatTur=7&MevzuatTertip=5"
    assert res.content_url.endswith("MevzuatFihristDetayIframe?MevzuatTur=7&MevzuatNo=11180&MevzuatTertip=5")
    assert res.meta == {"mevzuat_tur": 7, "mevzuat_tertip": 5, "mevzuat_no": "11180"}


@pytest.mark.parametrize("url", [
    "https://www.bddk.org.tr/Mevzuat/DokumanGetir/1347",
    "https://www.mevzuat.gov.tr/",
    "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=abc&MevzuatTur=7&MevzuatTertip=5",
    "https://mevzuat.gov.tr.example.com/mevzuat?MevzuatNo=1&MevzuatTur=7&MevzuatTertip=5",
])
def test_other_links_not_resolved(url):
    assert resolve(url) is None
