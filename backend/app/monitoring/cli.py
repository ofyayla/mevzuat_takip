"""İK-7 komut satırı aracı.

  mevzuat-monitor check [--dry-run]                     # kontrolleri çalıştır, alarmları senkronla, özeti yaz
  mevzuat-monitor alerts [--all]                        # açık (veya tüm) alarmlar
  mevzuat-monitor backfill KOD --from YYYY-MM-DD --to YYYY-MM-DD   # geçmiş dönemi yeniden topla (RG: gün gün)
  mevzuat-monitor reprocess STAGE --from … --to … [--source KOD]   # extract | classify | summarize | match
  mevzuat-monitor audit-verify [--id REG_ID]            # denetim izi hash zinciri bütünlük kontrolü
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from sqlalchemy import select

from app.db import Alert, make_sessionmaker
from app.monitoring.reprocess import STAGES, backfill, reprocess
from app.monitoring.service import evaluate, run_monitor
from app.settings import get_settings

ICON = {"ok": "  ", "delayed": "! ", "down": "X "}


def cmd_check(args) -> int:
    s = get_settings()
    sf = make_sessionmaker(s.database_url)
    with sf() as ses:
        res = evaluate(ses, s) if args.dry_run else run_monitor(ses, s)
        if not args.dry_run:
            ses.commit()
    for st in res.statuses:
        tol = f"tolerans {st.tolerance_hours:.0f} sa ({st.tolerance_basis})" if st.tolerance_hours else ""
        print(f"{ICON[st.status]}{st.code:<14} {st.status:<8} {tol:<42} {st.message or ''}")
    others = [f for f in res.findings if f.alert_type in ("cross_check_miss", "ai_failure")]
    for f in others:
        print(f"  uyarı [{f.alert_type}] {f.message}")
    if res.sync:
        print(f"\nalarm: açılan={len(res.sync.opened)} kapanan={len(res.sync.resolved)} süren={res.sync.still_open}")
    return 1 if any(st.status == "down" for st in res.statuses) else 0


def cmd_alerts(args) -> int:
    s = get_settings()
    with make_sessionmaker(s.database_url)() as ses:
        q = select(Alert).order_by(Alert.opened_at.desc())
        if not args.all:
            q = q.where(Alert.resolved_at.is_(None))
        for a in ses.scalars(q.limit(200)):
            state = f"kapandı {a.resolved_at:%d.%m %H:%M}" if a.resolved_at else "AÇIK"
            print(f"{a.opened_at:%d.%m %H:%M} {state:<16} {a.severity:<8} {a.alert_type:<17} "
                  f"{a.source_code or '-':<13} {a.message[:120]}")
    return 0


def cmd_backfill(args) -> int:
    s = get_settings()
    rep = backfill(make_sessionmaker(s.database_url), s, args.code, date.fromisoformat(args.date_from),
                   date.fromisoformat(args.date_to))
    for r in rep.runs:
        print(r)
    print(f"{rep.days} gün, {rep.new} yeni öğe")
    return 0


def cmd_reprocess(args) -> int:
    from app.ai.llm_client import get_llm

    s = get_settings()
    out = reprocess(make_sessionmaker(s.database_url), s, args.stage, date.fromisoformat(args.date_from),
                    date.fromisoformat(args.date_to), source=args.source, llm=get_llm(s))
    print(out)
    return 0


def cmd_audit_verify(args) -> int:
    from app.services.audit import verify_chain

    s = get_settings()
    with make_sessionmaker(s.database_url)() as ses:
        problems = verify_chain(ses, args.id)
    for p in problems:
        print(f"SORUN olay {p['id']} (düzenleme {p['regulation_id']}): {p['problem']}")
    print("Denetim izi bütünlüğü tamam" if not problems else f"{len(problems)} sorunlu olay")
    return 1 if any(p["problem"] != "hash yok (zincir öncesi)" for p in problems) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mevzuat-monitor", description="Kaynak izleme ve geriye dönük işleme (İK-7)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("--dry-run", action="store_true", help="alarmları yazma, yalnızca göster")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("alerts")
    p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_alerts)
    p = sub.add_parser("backfill")
    p.add_argument("code")
    p.add_argument("--from", dest="date_from", required=True)
    p.add_argument("--to", dest="date_to", required=True)
    p.set_defaults(fn=cmd_backfill)
    p = sub.add_parser("reprocess")
    p.add_argument("stage", choices=STAGES)
    p.add_argument("--from", dest="date_from", required=True)
    p.add_argument("--to", dest="date_to", required=True)
    p.add_argument("--source")
    p.set_defaults(fn=cmd_reprocess)
    p = sub.add_parser("audit-verify")
    p.add_argument("--id", type=int)
    p.set_defaults(fn=cmd_audit_verify)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
