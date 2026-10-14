#!/usr/bin/env python3
"""Belirli bir andan sonraki tüm servis loglarını tek dosyada toplar (analiz için paylaşılacak çıktı).

Yalnızca standart kütüphane kullanır; sunucuda olduğu gibi çalışır.

  python3 log_topla.py                                   # bugün 11:05'ten itibaren, ~/mevzuat-logs
  python3 log_topla.py --since "2026-10-14 11:05"        # başka bir an
  python3 log_topla.py --dir /yol/mevzuat-logs -o cikti.txt
  python3 log_topla.py --full                            # başarılı httpx satırlarını da koru (çok büyüyebilir)

Çıktı bölümleri:
  1. ÖZET: dosya başına satır/uyarı/hata sayıları, host başına başarılı/başarısız istek sayıları
  2. TARAMA ÖZETİ: `tarama bitti` ve yaşam döngüsü satırları (başlama/durdurma/talep)
  3. UYARI VE HATALAR: WARNING/ERROR kayıtları, traceback'leriyle birlikte
  4. HAM LOGLAR: her dosyanın süzülmüş hâli. Zaman damgasız dosyalar (ör. uvicorn) için son N satır

Parola, belirteç ve bağlantı adreslerindeki kimlik bilgileri maskelenir.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

FILES = ["crawler.log", "worker.log", "beat.log", "api.log", "start.log", "setup.log"]
TS_PATTERNS = [
    re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"),            # Python logging
    re.compile(r"^\[(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"),          # Celery: [2026-10-14 11:05:12,345: INFO/...]
]
LEVEL = re.compile(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")
HTTPX_OK = re.compile(r'httpx: HTTP Request: (\w+) (\S+) "HTTP/1\.1 (\d{3})')
SECRETS = [
    (re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization)(\s*[=:]\s*)(\S+)"), r"\1\2***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
    (re.compile(r"(\w+://)[^/\s:@]+:[^@\s/]+@"), r"\1***@"),
]
LIFECYCLE = ("tarama bitti", "crawler başladı", "durduruluyor", "tarama talebi", "ölü süreç", "yarıda kalan",
             "kilit", "talep başarısız", "zamanlanmış tarama", "ek onarımı", "devre kesici", "art arda")


def parse_ts(line: str) -> datetime | None:
    for rx in TS_PATTERNS:
        if m := rx.match(line):
            try:
                return datetime.fromisoformat(m.group(1).replace("T", " "))
            except ValueError:
                return None
    return None


def mask(line: str) -> str:
    for rx, repl in SECRETS:
        line = rx.sub(repl, line)
    return line


def entries(path: Path, since: datetime):
    """(zaman, [satırlar]) kayıtları. Zaman damgasız devam satırları (traceback) önceki kayda eklenir.
    Zaman damgası hiç bulunamazsa (None, tüm satırlar) döner."""
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        lines = [l.rstrip("\n") for l in fh]
    out: list[tuple[datetime | None, list[str]]] = []
    seen_ts = False
    for line in lines:
        ts = parse_ts(line)
        if ts is not None:
            seen_ts = True
            out.append((ts, [line]))
        elif out and out[-1][0] is not None:
            out[-1][1].append(line)
        elif not seen_ts:
            out.append((None, [line]))      # başlık/zamansız satır; dosyada hiç damga yoksa sonda ele alınır
    if not seen_ts:
        return None, lines
    return [(ts, ls) for ts, ls in out if ts is not None and ts >= since], lines


def level_of(first_line: str) -> str:
    m = LEVEL.search(first_line[:80])
    return m.group(1) if m else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="~/mevzuat-logs", help="log klasörü (varsayılan ~/mevzuat-logs)")
    ap.add_argument("--since", help='başlangıç, "YYYY-MM-DD HH:MM" (varsayılan: bugün 11:05)')
    ap.add_argument("-o", "--output", default="log_ozet.txt")
    ap.add_argument("--full", action="store_true", help="başarılı (2xx/3xx) httpx satırlarını da ham bölüme koy")
    ap.add_argument("--untimed-tail", type=int, default=300, help="zaman damgasız dosyalardan son N satır")
    args = ap.parse_args()

    since = datetime.fromisoformat(args.since) if args.since else datetime.now().replace(hour=11, minute=5, second=0,
                                                                                         microsecond=0)
    base = Path(args.dir).expanduser()
    if not base.is_dir():
        print(f"Klasör yok: {base}", file=sys.stderr)
        return 1

    summary: list[str] = []
    lifecycle: list[str] = []
    problems: list[str] = []
    raw: list[str] = []
    hosts: dict[str, Counter] = defaultdict(Counter)

    for name in FILES:
        path = base / name
        if not path.exists():
            summary.append(f"{name:<14} YOK")
            continue
        recs, all_lines = entries(path, since)
        if recs is None:   # zaman damgası yok
            tail = all_lines[-args.untimed_tail:]
            summary.append(f"{name:<14} zaman damgası yok; toplam {len(all_lines)} satır, son {len(tail)} satır eklendi")
            raw += [f"\n===== {name} (zaman damgasız, son {len(tail)} satır) =====", *map(mask, tail)]
            continue
        lv = Counter(level_of(ls[0]) for _, ls in recs)
        first = recs[0][0] if recs else None
        last = recs[-1][0] if recs else None
        summary.append(f"{name:<14} kayıt={len(recs):<6} WARNING={lv['WARNING']:<4} ERROR={lv['ERROR'] + lv['CRITICAL']:<4} "
                       f"ilk={first or '-'} son={last or '-'}")
        raw += [f"\n===== {name} ({len(recs)} kayıt) ====="]
        dropped = 0
        for ts, ls in recs:
            head = ls[0]
            level = level_of(head)
            if level in ("WARNING", "ERROR", "CRITICAL"):
                problems += [f"[{name}]", *map(mask, ls), ""]
            if any(k in head for k in LIFECYCLE):
                lifecycle.append(f"[{name}] {mask(head)}")
            if m := HTTPX_OK.search(head):
                host = re.sub(r"^\w+://([^/]+).*$", r"\1", m.group(2))
                hosts[host]["ok" if int(m.group(3)) < 400 else "http_" + m.group(3)] += 1
                if not args.full and int(m.group(3)) < 400:
                    dropped += 1
                    continue
            raw += map(mask, ls)
        if dropped:
            raw.append(f"... ({dropped} başarılı httpx satırı atlandı; --full ile dahil edilir)")

    out = [f"# Log özeti — {base} — {since:%Y-%m-%d %H:%M} sonrası — oluşturma {datetime.now():%Y-%m-%d %H:%M:%S}",
           "", "## 1. ÖZET", *summary, ""]
    if hosts:
        out.append("host başına istekler (httpx): " + "; ".join(
            f"{h}: " + ", ".join(f"{k}={v}" for k, v in c.most_common()) for h, c in sorted(hosts.items())))
        out.append("")
    out += ["## 2. TARAMA ÖZETİ VE YAŞAM DÖNGÜSÜ", *(lifecycle or ["(kayıt yok)"]), "",
            "## 3. UYARI VE HATALAR", *(problems or ["(kayıt yok)"]), "",
            "## 4. HAM LOGLAR", *raw, ""]
    Path(args.output).write_text("\n".join(out), encoding="utf-8")
    size = Path(args.output).stat().st_size
    print(f"Yazıldı: {args.output} ({size / 1024:.0f} KB, {len(out)} satır)")
    if size > 2_000_000:
        print("Dosya büyük: önce bölüm 1-3'ü paylaşın (`sed -n '1,/## 4. HAM LOGLAR/p' log_ozet.txt`).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
