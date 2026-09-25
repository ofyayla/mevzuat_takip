"""Türkçe metin normalizasyonu ve başlıktan yapısal bilgi çıkarımı (İK-2).

- ``normalize_text``: çıkarılmış metnin temizliği (yumuşak tire, satır sonu tireleri, boşluklar)
- ``title_key``: eşleştirme için başlık anahtarı (Türkçe küçük harf, noktalama/önek temizliği)
- ``parse_title``: düzenleme türü, değişiklik kalıbı, karar/tebliğ numarası, karar tarihi
- ``detect_issuer``: metnin başındaki "… Kurumundan:" satırından veya başlıktan yayımlayan kurum

Bu çıkarımlar kural tabanlıdır ve tekilleştirmede anahtar üretmek için kullanılır; düzenleme türünün kesin
sınıflandırması İK-3'te YZ ile yapılır.
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field

from app.collectors.dates import parse_tr_date, tr_lower

# ------------------------------------------------------------------------------------------------ metin


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f\ufffd]")


def text_quality(text: str) -> float:
    """Anlamlı karakter oranı (harf, rakam, noktalama, boşluk). ToUnicode eşlemesi olmayan fontlu PDF'lerde
    metin katmanı kontrol karakterlerinden oluşur (ör. RG'nin taranmış PDF üst bilgisi)."""
    if not text:
        return 0.0
    good = sum(1 for c in text if c.isalnum() or c.isspace() or c in ".,;:!?()[]{}'\"’“”-–—/%&+*=<>§|")
    return good / len(text)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = _CONTROL.sub("", text)
    text = text.replace("­", "").replace(" ", " ").replace("​", "").replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    # Satır sonunda bölünmüş kelimeler: "yükümlülük-\nlerin" → "yükümlülüklerin" (küçük harfle devam ediyorsa)
    text = re.sub(r"(\w)-\n[ \t]*([a-zçğıöşü])", r"\1\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_TITLE_PREFIXES = [
    r"^[\s\-–—−•·]+",                                               # RG fihristindeki "––" imleri
    r"^\(\s*\d{1,2}[./]\d{1,2}[./]\d{4}\s*-\s*\d+\s*\)\s*",       # BDDK "(18.09.2026 - 11572)"
    r"^\d{1,2}[./]\d{1,2}[./]\d{4}\s*",                             # "15.09.2026 …"
    r"^mülga\s*-\s*",
]


def clean_title(title: str) -> str:
    title = html.unescape(title or "")
    title = re.sub(r"\s+", " ", unicodedata.normalize("NFC", title)).strip().strip('"“”')
    for rx in _TITLE_PREFIXES:
        title = re.sub(rx, "", title, flags=re.I)
    return title.strip()


def title_key(title: str) -> str:
    """Eşleştirme anahtarı: küçük harf, şapkasız, noktalama yok, tek boşluk."""
    t = tr_lower(clean_title(title))
    t = t.translate(str.maketrans("âîû", "aiu"))
    t = re.sub(r"[’'`´]", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ------------------------------------------------------------------------------------------------ başlık

# Sıra önemli: daha özel kalıplar önce. Eşleşme başlığın sonuna yakın ana isimden yapılır ("…Yönetmelik").
_TYPES: list[tuple[str, str]] = [
    ("Cumhurbaşkanlığı Kararnamesi", r"cumhurbaşkanlığı kararnamesi"),
    ("Cumhurbaşkanı Kararı", r"cumhurbaşkanı kararı|karar sayısı\s*:\s*\d+\s*$"),
    ("Kanun", r"\bkanun(?:u)?$|\bkanun hükmünde|kanunda değişiklik|sayılı kanun$"),
    ("Yönetmelik", r"yönetmeli[kğ]"),
    ("Tebliğ", r"tebliğ"),
    ("Genelge", r"genelge"),
    ("Yönerge", r"yönerge"),
    ("Rehber", r"rehber|kılavuz"),
    ("Usul ve Esaslar", r"usul ve esaslar"),
    ("İlke Kararı", r"ilke kararı"),
    ("Kurul Kararı", r"kurul(?:u|unun|'un|’un)?\b.*karar|karar(?:ı)?$|sayılı kararı"),
    ("Düzenleme Taslağı", r"taslağı?"),
    ("Bülten", r"bülten"),
    ("Basın Duyurusu", r"basın (?:duyurusu|açıklaması)"),
    ("Duyuru", r"duyuru"),
    ("İlan", r"ilân|ilan"),
]

_AMEND = re.compile(r"değişiklik yapılmasına (?:dair|ilişkin)|değiştirilmesine (?:dair|ilişkin)", re.I)
_AMENDED_BASE = re.compile(r"^(?P<base>.+?)\s+(?:ile\s+.+?\s+)?değişiklik yapılmasına", re.I)
_LOCATIVE = re.compile(r"(?:n?[dt][ae])$")   # "Yönetmelikte" → "Yönetmelik", "Yönetmeliğinde" → "Yönetmeliği"

_DECISION = [
    # "… 11/03/2021 Tarihli ve 2021/238 Sayılı Kararı", "12.01.2023 Tarihli ve 2/51 S.K."
    re.compile(r"(?P<date>\d{1,2}[./]\d{1,2}[./]\d{4})\s*tarih(?:li)?\s*ve\s*(?P<no>[\w./\-ÖİŞÇĞÜ]+?)\s*"
               r"(?:sayılı|s\.\s?k\.)", re.I),
    # BDDK: "(18.09.2026 - 11572)"
    re.compile(r"\(\s*(?P<date>\d{1,2}[./]\d{1,2}[./]\d{4})\s*-\s*(?P<no>\d+)\s*\)"),
    re.compile(r"karar\s*(?:sayısı|no)\s*:?\s*(?P<no>[\d/\-]+)", re.I),
    re.compile(r"(?P<no>\d+)\s*sayılı\s+(?:kurul\s+)?kararı?", re.I),
]
_SEQ = [
    re.compile(r"sıra\s*no\s*:?\s*(?P<no>[\w/.\-]+)\)?", re.I),
    re.compile(r"\((?P<no>[IVX]+-[\d.]+[a-zçğış]?)\)"),              # SPK "(II-27.3)", "(III-45.2.a)"
    re.compile(r"(?P<no>\d{4}/\d+)\s*sayılı\s*(?:tebliğ|genelge|yönetmelik)", re.I),  # TCMB "2020/4 Sayılı Tebliğ"
]


@dataclass
class TitleFacts:
    title: str
    key: str
    reg_type: str | None = None
    is_amendment: bool = False
    amended_title: str | None = None
    decision_no: str | None = None
    decision_date: str | None = None     # ISO
    seq_no: str | None = None
    extra: dict = field(default_factory=dict)


def guess_type(title: str, category: str | None = None) -> str | None:
    for text in (title, category or ""):
        low = tr_lower(text)
        if _AMEND.search(low):
            # "X Yönetmeliğinde Değişiklik Yapılmasına Dair Yönetmelik" → son ismin türü
            low = low[_AMEND.search(low).end():]
        for name, rx in _TYPES:
            if re.search(rx, low):
                return name
    return None


def parse_title(title: str, category: str | None = None) -> TitleFacts:
    t = clean_title(title)
    raw = html.unescape(title or "")
    facts = TitleFacts(title=t, key=title_key(t), reg_type=guess_type(t, category))
    if _AMEND.search(t):
        facts.is_amendment = True
        if m := _AMENDED_BASE.search(t):
            base = m.group("base").strip()
            words = base.split(" ")
            words[-1] = _LOCATIVE.sub("", words[-1]) or words[-1]
            facts.amended_title = " ".join(words)
    for rx in _DECISION:
        if m := rx.search(raw):
            facts.decision_no = m.group("no").strip(" .")
            if "date" in m.groupdict() and m.group("date"):
                d = parse_tr_date(m.group("date").replace("/", "."))
                facts.decision_date = d.isoformat() if d else None
            break
    for rx in _SEQ:
        if m := rx.search(raw):
            facts.seq_no = m.group("no").strip(" .)")
            break
    return facts


# ------------------------------------------------------------------------------------------------ kurum

# kod → (görünen ad, metin/başlıkta tanıma regex'i — tr_lower uygulanmış metinde)
ISSUERS: dict[str, tuple[str, str]] = {
    "BDDK": ("Bankacılık Düzenleme ve Denetleme Kurumu", r"bankacılık düzenleme ve denetleme kur"),
    "SPK": ("Sermaye Piyasası Kurulu", r"sermaye piyasası kurul"),
    "TCMB": ("Türkiye Cumhuriyet Merkez Bankası", r"(?:türkiye cumhuriyet )?merkez bankası"),
    "KVKK": ("Kişisel Verileri Koruma Kurumu", r"kişisel verileri koruma kur"),
    "MASAK": ("Mali Suçları Araştırma Kurulu", r"mali suçları araştırma kurul"),
    "TICARET": ("Ticaret Bakanlığı", r"^ticaret bakanlığı|\bticaret bakanlığından"),
    "REKABET": ("Rekabet Kurumu", r"rekabet kur"),
    "TKBB": ("Türkiye Katılım Bankaları Birliği", r"katılım bankaları birliği"),
    "TBB": ("Türkiye Bankalar Birliği", r"türkiye bankalar birliği"),
    "HMB": ("Hazine ve Maliye Bakanlığı", r"hazine ve maliye bakanlığı"),
    "SEDDK": ("Sigortacılık ve Özel Emeklilik Düzenleme ve Denetleme Kurumu",
              r"sigortacılık ve özel emeklilik düzenleme"),
    "TMSF": ("Tasarruf Mevduatı Sigorta Fonu", r"tasarruf mevduatı sigorta fonu"),
    "CB": ("Cumhurbaşkanlığı", r"^cumhurbaşkanı kararı|cumhurbaşkanlığı kararnamesi"),
    "TBMM": ("Türkiye Büyük Millet Meclisi", r"türkiye büyük millet meclisi"),
}

# RG'deki yürütme metinleri "… Kurumundan:" satırıyla başlar
_ISSUER_LINE = re.compile(r"^[|*\s]*(?P<name>[^\n:|*]{4,160}?(?:dan|den|tan|ten))\s*\**\s*:", re.M)
_ABLATIVE = re.compile(r"(?:n?dan|n?den|tan|ten)$")   # "Kurumundan" → "Kurumu", "Bakanlığından" → "Bakanlığı"


def match_issuer(text: str) -> str | None:
    low = tr_lower(text or "")
    for code, (_, rx) in ISSUERS.items():
        if re.search(rx, low):
            return code
    return None


def detect_issuer(text: str, *, head_chars: int = 1500) -> tuple[str | None, str | None]:
    """Metnin başındaki "X'den:" satırından kurum (kod, ad). Tanınmayan kurumda kod None, ad dolu döner."""
    head = (text or "")[:head_chars]
    for m in _ISSUER_LINE.finditer(head):
        name = re.sub(r"\s+", " ", m.group("name")).strip()
        if re.search(r"\d", name) or len(name.split()) > 14:
            continue
        return match_issuer(name), _ABLATIVE.sub("", name)
    return None, None


_RG_ISSUE = re.compile(r"Sayı\s*:\s*(\d{5})")


# Kurum yayınlarındaki atıf: "(19.09.2026 tarih ve 33375 sayılı Resmî Gazete’de yayımlanmıştır.)". Yalnızca bu
# kalıp alınır; metin içindeki "…tarihli ve 31419 sayılı Resmî Gazete’de yayımlanan X Yönetmeliği" atıfları
# değiştirilen eski düzenlemeyi gösterir.
_RG_PUBLISHED = re.compile(r"(\d{1,2}[./]\d{1,2}[./]\d{4})\s*tarih(?:li)?\s*ve\s*(\d{5})\s*sayılı\s*Resm[iî]\s*"
                           r"Gazete[’']?de\s*yayımlanmıştır", re.I)


def detect_rg_issue(text: str) -> str | None:
    """Resmî Gazete sayı numarası (belge üst bilgisindeki "Sayı : 33380")."""
    m = _RG_ISSUE.search((text or "")[:2000])
    return m.group(1) if m else None


def detect_rg_reference(text: str) -> tuple[str | None, str | None]:
    """Kurum belgesinin yayımlandığı RG (tarih ISO, sayı)."""
    m = _RG_PUBLISHED.search((text or "")[:3000])
    if not m:
        return None, None
    d = parse_tr_date(m.group(1).replace("/", "."))
    return (d.isoformat() if d else None), m.group(2)
