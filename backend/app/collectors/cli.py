"""Toplama katmanı komut satırı aracı.

  mevzuat-collect sources                         # tanımlı kaynaklar/kanallar ve doğrulama durumu
  mevzuat-collect check-access [KOD ...]          # ağ/firewall erişim testi (İK-1 bağlantı testi)
  mevzuat-collect probe KOD [--channel K] [--record DIR] [--limit N]
                                                  # yalnızca listeyi çeker, DB'ye yazmaz; --record yanıtları
                                                  # fixture olarak kaydeder (kurum ağında doğrulama için)
  mevzuat-collect run KOD|all [--channel K] [--max-details N]
                                                  # tam tarama: detay + ekler + arşiv + DB
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from app.collectors.config import SourceConfig, load_sources
from app.collectors.http import FetchError, Fetcher, Recorder
from app.collectors.locks import LockBusy, source_lock
from app.collectors.runner import SourceCollector, list_channel
from app.db import make_sessionmaker
from app.settings import get_settings
from app.storage import FileSystemStorage


def _sources(args) -> list[SourceConfig]:
    cfg = load_sources(get_settings().sources_file)
    codes = getattr(args, "codes", None) or []
    if not codes or codes == ["all"]:
        return cfg.enabled()
    return [cfg.get(c) for c in codes]


def cmd_sources(args) -> int:
    for s in _sources(args):
        status = f"doğrulandı {s.verified}" if s.verified else "DOĞRULANMADI"
        print(f"{s.code:<14} {s.name}  [{s.client}] ({status})")
        for ch in s.channels:
            print(f"    - {ch.name:<22} {ch.strategy:<14} detay={'evet' if ch.fetch_detail else 'hayır'}")
    return 0


def cmd_check_access(args) -> int:
    settings = get_settings()
    failures = 0
    for s in _sources(args):
        fetcher = Fetcher(s, settings)
        t0 = time.monotonic()
        try:
            r = fetcher.get(s.base_url, allow_status=tuple(range(400, 600)))
            ok = r.status < 400
            detail = f"HTTP {r.status}, {len(r.content)} bayt"
        except FetchError as e:
            ok, detail = False, str(e)
        finally:
            fetcher.close()
        failures += not ok
        print(f"{'OK  ' if ok else 'HATA'} {s.code:<14} {s.base_url:<34} [{s.client}] "
              f"{time.monotonic() - t0:5.1f}s  {detail}")
    return 1 if failures else 0


def cmd_probe(args) -> int:
    settings = get_settings()
    source = load_sources(settings.sources_file).get(args.code)
    recorder = Recorder(Path(args.record)) if args.record else None
    fetcher = Fetcher(source, settings, recorder=recorder)
    rc = 0
    try:
        for ch in source.channels:
            if args.channel and ch.name not in args.channel:
                continue
            print(f"\n== {source.code}/{ch.name} ({ch.strategy})")
            try:
                res = list_channel(source, ch, fetcher)
            except Exception as e:  # noqa: BLE001
                print(f"   HATA: {type(e).__name__}: {e}")
                rc = 1
                continue
            flag = "  ⚠ BOŞ LİSTE — yapı değişmiş olabilir" if len(res.items) < source.expected.min_items_per_run else ""
            print(f"   {len(res.items)} öğe, {res.pages_fetched} sayfa, imza={res.signature}{flag}")
            for w in res.warnings:
                print(f"   uyarı: {w}")
            for it in res.items[: args.limit]:
                print(f"   - [{it.published_at or '    ?     '}] {it.title[:95]}")
                print(f"       id={it.external_id}  {it.url[:110]}")
            if recorder and args.with_details:
                from app.collectors.runner import fetch_documents
                for it in res.items[: args.limit]:
                    try:
                        fetch_documents(source, ch, it, fetcher, settings.collect_max_attachments)
                    except FetchError as e:
                        print(f"   detay hatası: {e}")
            if flag:
                rc = 1
    finally:
        fetcher.close()
    if recorder:
        print(f"\nYanıtlar kaydedildi: {args.record} ({len(recorder.manifest)} dosya)")
    return rc


def cmd_run(args) -> int:
    settings = get_settings()
    session_factory = make_sessionmaker(settings.database_url)
    storage = FileSystemStorage(settings.raw_storage_dir)
    rc = 0
    for s in _sources(args):
        try:
            with source_lock(s.code, redis_url=settings.redis_url, lock_dir=settings.lock_dir):
                collector = SourceCollector(s, settings, session_factory, storage)
                try:
                    rep = collector.run(channels=args.channel, max_details=args.max_details)
                finally:
                    collector.fetcher.close()
        except LockBusy:
            print(f"{s.code}: başka bir tarama sürüyor, atlandı")
            continue
        print(f"{s.code:<14} {rep.status:<8} yeni={rep.new} değişen={rep.changed} istek={rep.requests}")
        for c in rep.channels:
            extra = " (ilk tarama: mevcut içerik BASELINE)" if c.baseline else ""
            extra += " ⚠ boş liste" if c.empty_listing else ""
            extra += " ⚠ yapı imzası değişti" if c.structure_changed else ""
            print(f"    {c.name:<22} öğe={c.items:<4} yeni={c.new:<4} değişen={c.changed:<3} "
                  f"aynı={c.unchanged:<4} ertelenen={c.deferred}{extra}" + (f"  HATA: {c.error}" if c.error else ""))
        rc |= rep.status != "success"
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mevzuat-collect", description="Mevzuat kaynak toplayıcı")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("sources", cmd_sources), ("check-access", cmd_check_access)):
        p = sub.add_parser(name)
        p.add_argument("codes", nargs="*")
        p.set_defaults(fn=fn)
    p = sub.add_parser("probe")
    p.add_argument("code")
    p.add_argument("--channel", action="append")
    p.add_argument("--record", help="yanıtların kaydedileceği klasör (fixture)")
    p.add_argument("--with-details", action="store_true", help="--record ile birlikte ilk öğelerin detaylarını da kaydet")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("run")
    p.add_argument("codes", nargs="+")
    p.add_argument("--channel", action="append")
    p.add_argument("--max-details", type=int, default=200)
    p.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
