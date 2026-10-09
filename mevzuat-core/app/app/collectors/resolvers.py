"""Dış belge siteleri için bağlantı çözümleyiciler.

Kaynak listeleri çoğu zaman düzenlemenin kendisini kendi sitelerinde değil, merkezi bir belge sitesinde gösterir
(BDDK ve Ticaret Bakanlığı listeleri → mevzuat.gov.tr). Aynı belgeye farklı biçimlerde bağlantı verilir (eski
``Metin.Aspx?MevzuatKod=7.5.11180&sourceXmlSearch=...``, yeni ``/mevzuat?MevzuatNo=11180&MevzuatTur=7&MevzuatTertip=5``,
``http``/``https``, ``www`` var/yok). Çözümleyici:

- ``canonical_id``: bağlantı biçiminden bağımsız kararlı kimlik (tekrar eden öğe/sürüm oluşmasın)
- ``public_url``: kullanıcıya gösterilecek kanonik adres
- ``content_url``: tam metnin indirileceği adres (mevzuat.gov.tr sayfası metni bir iframe içinde yükler)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

MEVZUAT_GOV_HOSTS = ("mevzuat.gov.tr", "www.mevzuat.gov.tr")


@dataclass(frozen=True)
class ResolvedLink:
    canonical_id: str
    public_url: str
    content_url: str
    meta: dict


def _mevzuat_gov(url: str) -> ResolvedLink | None:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in MEVZUAT_GOV_HOSTS:
        return None
    q = {k.lower(): v[0].strip() for k, v in parse_qs(parts.query).items() if v}
    tur = tertip = no = None
    if "mevzuatkod" in q and (m := re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", q["mevzuatkod"])):
        tur, tertip, no = m.groups()                       # eski biçim: MevzuatKod=Tür.Tertip.No
    elif "mevzuatno" in q:
        no, tur, tertip = q.get("mevzuatno"), q.get("mevzuattur"), q.get("mevzuattertip")
    elif m := re.search(r"/MevzuatMetin/(\d+)\.(\d+)\.(\d+)\.\w+$", parts.path, re.I):
        tur, tertip, no = m.groups()
    if not (tur and tertip and no and tur.isdigit() and tertip.isdigit() and no.isdigit()):
        return None
    base = "https://www.mevzuat.gov.tr"
    return ResolvedLink(
        canonical_id=f"mevzuat.gov.tr:{tur}.{tertip}.{no}",
        public_url=f"{base}/mevzuat?MevzuatNo={no}&MevzuatTur={tur}&MevzuatTertip={tertip}",
        content_url=f"{base}/anasayfa/MevzuatFihristDetayIframe?MevzuatTur={tur}&MevzuatNo={no}&MevzuatTertip={tertip}",
        meta={"mevzuat_tur": int(tur), "mevzuat_tertip": int(tertip), "mevzuat_no": no},
    )


_RESOLVERS = (_mevzuat_gov,)


def resolve(url: str) -> ResolvedLink | None:
    for fn in _RESOLVERS:
        if (res := fn(url)) is not None:
            return res
    return None
