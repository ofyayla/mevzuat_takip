"""HTML stratejilerinin ortak yardımcıları."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser, Node

from app.collectors.dates import tr_lower

_WS = re.compile(r"\s+")
_MAX_CHROME_HEADER_CHARS = 2500

# "İncele", "Git" gibi anlamsız bağlantı metinleri — başlık üst elemandan alınır
GENERIC_LINK_TEXTS = {"incele", "git", "devamını oku", "devamını gör", "görüntüle", "görüntüle >", "indir",
                      "detay", "tıklayınız", "tümü", "tümünü gör", ">", "»"}


def clean(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip(" –-–—\t\n")


def parse_html(text: str, *, strip_chrome: bool = True) -> HTMLParser:
    """HTML'i ayrıştırır; ``strip_chrome`` ile menü/altbilgi gibi tekrar eden çerçeveyi atar.

    Bazı siteler (Rekabet Kurumu) sayfa içeriğinin tamamını ``<header>`` içine koyduğu için header yalnızca
    kısa bir menü bloğuysa atılır.
    """
    tree = HTMLParser(text)
    for n in tree.css("script,style,noscript,svg"):
        n.decompose()
    if strip_chrome:
        for n in tree.css("nav,footer"):
            n.decompose()
        for n in tree.css("header"):
            if len(n.text(strip=True)) < _MAX_CHROME_HEADER_CHARS:
                n.decompose()
    return tree


def absolutize(base: str, href: str) -> str:
    url = urljoin(base, href.strip())
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))  # fragment atılır


def node_signature(nodes: list[Node]) -> str:
    """Öğe kapsayıcılarının etiket/sınıf yolu özeti. Site şablonu değişirse imza değişir (İK-7)."""
    paths = set()
    for n in nodes[:50]:
        parts, cur = [], n
        for _ in range(3):
            if cur is None or cur.tag in ("html", "body", "-undef"):
                break
            cls = ".".join(sorted((cur.attributes.get("class") or "").split()))
            parts.append(f"{cur.tag}.{cls}" if cls else cur.tag)
            cur = cur.parent
        paths.add(">".join(reversed(parts)))
    return hashlib.sha1("|".join(sorted(paths)).encode()).hexdigest()[:16] if paths else ""


def link_title(a: Node, *, max_depth: int = 6, min_len: int = 12) -> tuple[str, Node]:
    """Bağlantı metni anlamlıysa onu, değilse anlamlı metin içeren en yakın üst elemanın metnini döndürür."""
    text = clean(a.text(separator=" "))
    if text and tr_lower(text) not in GENERIC_LINK_TEXTS and len(text) >= 4:
        return text, a
    title = a.attributes.get("title")
    if title and len(clean(title)) >= 4:
        return clean(title), a
    cur = a
    for _ in range(max_depth):
        cur = cur.parent
        if cur is None:
            break
        candidate = clean(cur.text(separator=" "))
        for g in GENERIC_LINK_TEXTS:
            candidate = _strip_generic(candidate, g)
        candidate = clean(candidate)
        if len(candidate) >= min_len:
            return candidate, cur
    return text, a


def external_id(url: str, pattern: str | None) -> str:
    if pattern and (m := re.search(pattern, url)):
        return next((g for g in m.groups() if g), m.group(0))
    parts = urlsplit(url)
    return f"{parts.path}{'?' + parts.query if parts.query else ''}"


def _strip_generic(text: str, generic: str) -> str:
    """Metindeki anlamsız bağlantı sözcüğünü (Türkçe büyük/küçük harf duyarsız) çıkarır."""
    words = text.split(" ")
    gw = generic.split(" ")
    n = len(gw)
    out, i = [], 0
    while i < len(words):
        if [tr_lower(w) for w in words[i:i + n]] == gw:
            i += n
            continue
        out.append(words[i])
        i += 1
    return " ".join(out)
