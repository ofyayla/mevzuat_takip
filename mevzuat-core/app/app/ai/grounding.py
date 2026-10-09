"""Kaynağa dayandırma (İK-4, plan §6): LLM'in verdiği birebir alıntıların kaynak metinde bulunması ve yürürlük
tarihinin deterministik kontrolü.

Alıntı arama: metin ve alıntı aynı biçimde normalize edilir (Türkçe küçük harf, tırnak/tire birliği, boşluk
sadeleştirme); önce tam eşleşme, olmazsa ``rapidfuzz.partial_ratio_alignment >= eşik``. Normalize metindeki konum,
orijinal metindeki karakter aralığına (``char_start``, ``char_end``) geri çevrilir — portal alıntıyı kaynakta
vurgulayabilsin diye. "…" ile kısaltılmış alıntının her parçası ayrı ayrı aranır.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from rapidfuzz import fuzz

from app.collectors.dates import DATE_TEXT_PATTERN, parse_tr_date, tr_lower

_QUOTE_CHARS = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-", " ": " ",
                              "\f": " ", "â": "a", "î": "i", "û": "u"})
MIN_QUOTE_CHARS = 12
_WORD = re.compile(r"\w+")
_METHOD_RANK = ["exact", "exact_words", "fuzzy", "elided"]
MIN_ELIDED_TOKENS = 5          # atlamalı eşleşme için en az kelime
ELIDED_WINDOW_FACTOR = 2.0     # alıntı kelimeleri kaynakta en fazla bu kat genişlikte bir pencereye yayılabilir


@dataclass
class SourceText:
    raw_document_id: int
    text: str
    _norm: str | None = None
    _map: list[int] | None = None
    _tokens: list[tuple[str, int, int]] | None = None

    def normalized(self) -> tuple[str, list[int]]:
        if self._norm is None:
            self._norm, self._map = normalize_with_map(self.text)
        return self._norm, self._map

    def tokens(self) -> list[tuple[str, int, int]]:
        """(kelime, orijinal başlangıç, orijinal bitiş) — noktalama ve boşluk farklarından bağımsız eşleşme için."""
        if self._tokens is None:
            norm, mp = self.normalized()
            self._tokens = [(m.group(0), mp[m.start()], mp[m.end() - 1] + 1) for m in _WORD.finditer(norm)]
        return self._tokens


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Normalize metin + her normalize karakterin orijinal metindeki konumu."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        c = tr_lower(ch).translate(_QUOTE_CHARS)
        if c == "­":
            continue
        if c.isspace():
            if prev_space:
                continue
            c, prev_space = " ", True
        else:
            prev_space = False
        for cc in c:
            out.append(cc)
            idx.append(i)
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]


def find_quote(quote: str, sources: list[SourceText], threshold: int = 90) -> dict | None:
    """Alıntıyı kaynaklarda arar; bulunursa kanıt sözlüğü (belge, karakter aralığı, skor, yöntem)."""
    parts = [p.strip(" .,;:\"'") for p in re.split(r"\.\.\.|…|\[\.\.\.\]", quote or "")]
    parts = [p for p in parts if p]
    if not parts or sum(len(p) for p in parts) < MIN_QUOTE_CHARS:
        return None
    found = []
    for part in parts:
        hit = _find_part(normalize(part), sources, threshold)
        if hit is None:
            return None
        found.append(hit)
    first, last = found[0], found[-1]
    same_doc = all(f["raw_document_id"] == first["raw_document_id"] for f in found)
    return {"quote": quote, "raw_document_id": first["raw_document_id"], "char_start": first["char_start"],
            "char_end": last["char_end"] if same_doc else first["char_end"],
            "score": min(f["score"] for f in found),
            "method": max((f["method"] for f in found), key=_METHOD_RANK.index)}   # en zayıf parçanın yöntemi


def _find_part(nq: str, sources: list[SourceText], threshold: int) -> dict | None:
    """Sırayla: tam eşleşme → kelime dizisi eşleşmesi (noktalama/boşluk farkı yok sayılır) → bulanık
    (partial_ratio ≥ eşik) → atlamalı eşleşme (tüm kelimeler aynı sırayla, dar pencerede; LLM'in "…" koymadan bent
    atladığı alıntılar). Hiçbirinde kaynakta olmayan kelime kabul edilmez."""
    if len(nq) < 6:
        return None
    qt = _WORD.findall(nq)
    best = None
    for src in sources:
        norm, mp = src.normalized()
        if not norm:
            continue
        pos = norm.find(nq)
        if pos >= 0:
            return _hit(src, mp[pos], mp[pos + len(nq) - 1] + 1, 100.0, "exact")
        if qt and (span := _token_match(qt, src.tokens(), contiguous=True)):
            return _hit(src, *span, 100.0, "exact_words")
        al = fuzz.partial_ratio_alignment(nq, norm, score_cutoff=threshold)
        if al is not None and (best is None or al.score > best["score"]):
            end = max(al.dest_start, al.dest_end - 1)
            best = _hit(src, mp[al.dest_start], mp[min(end, len(mp) - 1)] + 1, round(al.score, 1), "fuzzy")
    if best is not None or len(qt) < MIN_ELIDED_TOKENS:
        return best
    for src in sources:
        if span := _token_match(qt, src.tokens(), contiguous=False):
            return _hit(src, *span, 95.0, "elided")
    return None


def _hit(src: SourceText, start: int, end: int, score: float, method: str) -> dict:
    return {"raw_document_id": src.raw_document_id, "char_start": start, "char_end": end, "score": score,
            "method": method}


def _token_match(qt: list[str], toks: list[tuple[str, int, int]], *, contiguous: bool) -> tuple[int, int] | None:
    n = len(qt)
    window = n if contiguous else int(n * ELIDED_WINDOW_FACTOR) + 3
    words = [t[0] for t in toks]
    for i, w in enumerate(words):
        if w != qt[0]:
            continue
        j, k = 1, i + 1
        while j < n and k < len(words) and k - i < window:
            if words[k] == qt[j]:
                j += 1
            elif contiguous:
                break
            k += 1
        if j == n:
            return toks[i][1], toks[k - 1][2]
    return None


# ------------------------------------------------------------------------------------------------ yürürlük tarihi

_NUM_WORDS = {"bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5, "altı": 6, "yedi": 7, "sekiz": 8, "dokuz": 9,
              "on": 10, "on bir": 11, "on iki": 12, "on beş": 15, "yirmi": 20, "otuz": 30, "kırk": 40, "kırk beş": 45,
              "elli": 50, "altmış": 60, "yetmiş": 70, "seksen": 80, "doksan": 90, "yüz": 100, "yüz yirmi": 120,
              "yüz seksen": 180, "üç yüz altmış beş": 365}
_NUM_ALT = "|".join(sorted((re.escape(k) for k in _NUM_WORDS), key=len, reverse=True))
_RELATIVE = re.compile(rf"yayım\w*(?:\s+tarih\w*)?\s+(?:itibaren\s+)?(?P<n>\d+|{_NUM_ALT})\s+(?P<unit>gün|ay|yıl)\s+sonra")
_ON_PUBLICATION = re.compile(r"yayım\w*\s+tarih\w*(?:nde|nden itibaren)\s+yürürlüğe\s+gir|yayımlandığı\s+tarihte")
_EXPLICIT = re.compile(rf"(\d{{1,2}}[./]\d{{1,2}}[./]\d{{4}}|{DATE_TEXT_PATTERN})")
_YURURLUK_SENTENCE = re.compile(r"[^.\n]{0,300}?yürürlüğe\s+gir(?:er|ecek)[^.\n]{0,80}", re.I)


@dataclass
class EffectiveDateCheck:
    value: date | None
    text: str | None
    basis: str                     # explicit | relative | on_publication | unverified | none
    note: str | None = None


def _num(s: str) -> int | None:
    return int(s) if s.isdigit() else _NUM_WORDS.get(s)


def resolve_effective_date(quote_or_text: str | None, publish_date: date | None,
                           llm_value: date | None = None) -> EffectiveDateCheck:
    """Yürürlük ifadesinden tarihi hesaplar; LLM'in verdiği değer ifadeyle tutarsızsa ifade kazanır."""
    if not quote_or_text:
        return EffectiveDateCheck(None, None, "none")
    low = tr_lower(re.sub(r"\s+", " ", quote_or_text))
    computed, basis = None, "none"
    if m := _RELATIVE.search(low):
        n = _num(m.group("n"))
        if n is not None and publish_date:
            computed = publish_date + {"gün": timedelta(days=n), "ay": relativedelta(months=n),
                                       "yıl": relativedelta(years=n)}[m.group("unit")]
            basis = "relative"
    elif _ON_PUBLICATION.search(low) and publish_date:
        computed, basis = publish_date, "on_publication"
    elif m := _EXPLICIT.search(quote_or_text):
        d = parse_tr_date(m.group(1).replace("/", "."))
        if d:
            computed, basis = d, "explicit"
    note = None
    if llm_value and computed and llm_value != computed:
        note = f"LLM değeri ({llm_value}) ifadeyle tutarsız; ifadeden hesaplanan kullanıldı"
    if computed is None and llm_value:
        return EffectiveDateCheck(None, quote_or_text, "unverified", "LLM tarihi ifadeden doğrulanamadı")
    return EffectiveDateCheck(computed, quote_or_text, basis, note)


def find_effective_sentence(text: str) -> str | None:
    """Metindeki "…yürürlüğe girer" cümlesi (Yürürlük maddesi genellikle sonlardadır → son eşleşme)."""
    hits = [m.group(0).strip() for m in _YURURLUK_SENTENCE.finditer(text or "")]
    hits = [h for h in hits if not re.search(r"yürürlükten kaldır", tr_lower(h))]
    return re.sub(r"^(?:MADDE\s+\d+\s*[-–]\s*)?(?:\(\d+\)\s*)?", "", hits[-1]).strip() if hits else None
