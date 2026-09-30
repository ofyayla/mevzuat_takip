"""DMZ crawler komut satırı aracı.

  mevzuat-crawler serve                           # servis: zamanlama + LAN tarama talepleri (konteyner komutu)
  mevzuat-crawler check                           # MongoDB bağlantısı, indeksler, aktarılmayı bekleyen kayıtlar
  mevzuat-crawler run KOD|all [--channel K] [--max-details N]   # elle tarama (Mongo'ya yazar)
  mevzuat-crawler backfill KOD --from YYYY-MM-DD --to YYYY-MM-DD
  mevzuat-crawler healthcheck                     # konteyner sağlık kontrolü (sinyal dosyası taze mi)

Kaynak erişim testi ve ara sertifika için ``mevzuat-collect check-access`` / ``ca-fetch`` aynı imajda kullanılır.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone

from app.settings import get_settings


def _service():
    from app.crawler.service import CrawlerService
    from app.mongo import crawl_db

    s = get_settings()
    return CrawlerService(s, crawl_db(s))


def cmd_serve(args) -> int:
    _service().serve()
    return 0


def cmd_check(args) -> int:
    from app.mongo import DOCS, REQUESTS, RUNS, crawl_db, ensure_indexes, last_heartbeat

    s = get_settings()
    db = crawl_db(s)
    db.client.admin.command("ping")
    ensure_indexes(db)
    print(f"OK   MongoDB {s.mongo_db} erişilebilir, indeksler hazır")
    print(f"     aktarılmayı bekleyen: belge={db[DOCS].count_documents({'synced': False})} "
          f"tarama={db[RUNS].count_documents({'synced': False})} "
          f"bekleyen talep={db[REQUESTS].count_documents({'status': 'pending'})}")
    hb = last_heartbeat(db)
    print(f"     son crawler sinyali: {hb['_id']} {hb['at']:%Y-%m-%d %H:%M:%S}" if hb else "     crawler sinyali yok")
    return 0


def cmd_run(args) -> int:
    from app.crawler.service import SourceBusy

    svc = _service()
    codes = list(svc.sources) if args.codes == ["all"] else args.codes
    rc = 0
    for code in codes:
        try:
            out = svc.run_source(code, channels=args.channel, max_details=args.max_details)
        except SourceBusy:
            print(f"{code}: başka bir tarama sürüyor, atlandı")
            continue
        print(json.dumps(out, ensure_ascii=False))
        rc |= out["status"] != "success"
    return rc


def cmd_backfill(args) -> int:
    out = _service().run_backfill(args.code, date.fromisoformat(args.date_from), date.fromisoformat(args.date_to))
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


def cmd_healthcheck(args) -> int:
    s = get_settings()
    try:
        at = datetime.fromisoformat(s.crawler_heartbeat_file.read_text().strip())
    except (OSError, ValueError):
        return 1
    return 0 if (datetime.now(timezone.utc) - at).total_seconds() < max(4 * s.crawler_poll_s, 120) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mevzuat-crawler", description="Mevzuat DMZ crawler servisi")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("serve", cmd_serve), ("check", cmd_check), ("healthcheck", cmd_healthcheck)):
        sub.add_parser(name).set_defaults(fn=fn)
    p = sub.add_parser("run")
    p.add_argument("codes", nargs="+")
    p.add_argument("--channel", action="append")
    p.add_argument("--max-details", type=int, default=200)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("backfill")
    p.add_argument("code")
    p.add_argument("--from", dest="date_from", required=True)
    p.add_argument("--to", dest="date_to", required=True)
    p.set_defaults(fn=cmd_backfill)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
