"""BDDK kanallarının desen testleri — SENTETİK HTML ile.

BDDK geliştirme ortamından erişilemedi (yurt dışı IP engeli). Bu test yalnızca sources.yaml'daki URL
desenlerinin (web aramasıyla doğrulanan Duyuru/Detay, Duyuru/EkGetir, Mevzuat/Detay biçimleri) doğru
çalıştığını gösterir. Gerçek sayfa yapısı kurum ağında şu komutla doğrulanmalı ve bu dosya gerçek
fixture'larla değiştirilmelidir:

    mevzuat-collect probe BDDK --record tests/fixtures/bddk --with-details
"""
from datetime import datetime, timezone

from app.collectors.runner import fetch_documents, list_channel
from tests.conftest import DictFetcher

B = "https://www.bddk.org.tr"
LIST_40 = """<html><body><nav><a href="/Duyuru/Detay/1">menüdeki bağlantı</a></nav>
<div class="liste"><table>
 <tr><td>25.09.2026</td><td><a href="/Duyuru/Detay/2231">Kredi Kartı Taksit Sayılarına İlişkin Kurul Kararı</a></td></tr>
 <tr><td>19.09.2026</td><td><a href="/Duyuru/Detay/2229">Bilgi Sistemleri Yönetmeliği Taslağı Hakkında Duyuru</a></td></tr>
 <tr><td></td><td><a href="/Duyuru/Liste/41">Diğer duyurular</a></td></tr>
</table></div></body></html>"""
DETAIL = """<html><body><h1>Kredi Kartı Taksit Sayılarına İlişkin Kurul Kararı</h1>
<a href="/Duyuru/EkGetir/2231?ekId=901">Kurul Kararı (PDF)</a><a href="https://twitter.com/bddk">paylaş</a></body></html>"""


def test_bddk_duyuru_pattern(sources, settings):
    src = sources.get("BDDK")
    ch = src.channel("mevzuat_duyurulari")
    f = DictFetcher(src, settings, {
        f"{B}/Duyuru/Liste/40": (LIST_40, "text/html"),
        f"{B}/Duyuru/Detay/2231": (DETAIL, "text/html"),
        f"{B}/Duyuru/EkGetir/2231?ekId=901": (b"%PDF-1.7", "application/pdf"),
    })
    res = list_channel(src, ch, f, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
    assert [i.external_id for i in res.items] == ["2231", "2229"]   # nav bağlantısı ve liste sayfaları hariç
    assert res.items[0].published_at.isoformat() == "2026-09-25"
    docs = fetch_documents(src, ch, res.items[0], f, max_attachments=5)
    assert [d.role for d in docs] == ["main", "attachment"] and docs[1].content_type == "application/pdf"
