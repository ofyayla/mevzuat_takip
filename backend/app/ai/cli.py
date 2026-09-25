"""İK-3 komut satırı aracı.

  mevzuat-ai llm-check                         # LLM bağlantı ve şemalı çıktı testi
  mevzuat-ai classify [--id N ...] [--limit N] [--force]   # NEW düzenlemeleri sınıflandır
  mevzuat-ai show REG_ID                        # ilgililik/önem kararı ve bileşenleri
  mevzuat-ai calls [--limit N]                  # son LLM çağrıları (gecikme, token, durum)
  mevzuat-ai setting [KEY VALUE]                # çalışma zamanı eşikleri (app_setting)
  mevzuat-ai eval DOSYA.jsonl [--samples N]     # değerlendirme seti: recall / precision / önem isabeti
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from pydantic import BaseModel
from sqlalchemy import func, select

from app.ai.classify import classify_pending
from app.ai.common import RUNTIME_KEYS, effective_settings, set_runtime_setting
from app.ai.llm_client import LLMError, get_llm
from app.db import LlmCall, Regulation, make_sessionmaker
from app.settings import get_settings


def _llm_or_exit(s):
    llm = get_llm(s)
    if llm is None:
        print("LLM_PROVIDER=none — backend/.env içinde LLM_PROVIDER ve LLM_API_KEY (vLLM için LLM_BASE_URL) ayarlayın")
        sys.exit(1)
    return llm


class _Ping(BaseModel):
    ok: bool
    answer: str


def cmd_llm_check(args) -> int:
    s = get_settings()
    llm = _llm_or_exit(s)
    msgs = [{"role": "system", "content": "Yalnızca JSON döndür."},
            {"role": "user", "content": "Türkiye'nin başkenti neresidir? ok=true ve answer alanında yanıtla."}]
    try:
        res = llm.complete_json("ping", "ping.v1", msgs, _Ping, force=True)
    except LLMError as e:
        print(f"HATA {e}")
        return 1
    print(f"OK  {llm.provider}/{llm.model}: {res.outputs[0].model_dump()} ({res.latency_ms} ms, "
          f"{res.prompt_tokens}+{res.completion_tokens} token, thinking={'var' if res.reasoning else 'yok'})")
    return 0


def cmd_classify(args) -> int:
    s = get_settings()
    llm = _llm_or_exit(s)
    rep = classify_pending(make_sessionmaker(s.database_url), llm, s, ids=args.id, limit=args.limit,
                           force=args.force, include_baseline=args.include_baseline)
    print(f"işlenen={rep.processed} ilgili={rep.relevant} ilgisiz={rep.irrelevant} hata={rep.failed} "
          f"önem={rep.by_severity}")
    for e in rep.errors[:20]:
        print(f"  HATA {e}")
    return 1 if rep.failed else 0


def cmd_show(args) -> int:
    s = get_settings()
    with make_sessionmaker(s.database_url)() as ses:
        reg = ses.get(Regulation, args.reg_id)
        if reg is None:
            return 1
        print(f"#{reg.id} [{reg.processing_status}] {reg.title}")
        print(f"  ilgili={reg.is_relevant} skor={reg.relevance_score} güven={reg.confidence_band} "
              f"önem={reg.severity} tür={reg.ai_reg_type or reg.reg_type}")
        print(f"  önem gerekçesi: {reg.severity_rationale}")
        print(json.dumps(reg.classification, ensure_ascii=False, indent=2))
    return 0


def cmd_calls(args) -> int:
    s = get_settings()
    with make_sessionmaker(s.database_url)() as ses:
        for c in ses.scalars(select(LlmCall).order_by(LlmCall.id.desc()).limit(args.limit)):
            print(f"{c.id:>6} {c.created_at:%Y-%m-%d %H:%M} {c.task:<15} {c.status:<8} {c.latency_ms or 0:>6} ms "
                  f"{c.prompt_tokens or 0:>6}+{c.completion_tokens or 0:<5} reg={c.regulation_id} {c.error or ''}")
        rows = ses.execute(select(LlmCall.task, func.count(), func.avg(LlmCall.latency_ms),
                                  func.sum(LlmCall.prompt_tokens), func.sum(LlmCall.completion_tokens))
                           .group_by(LlmCall.task))
        print("\ngörev bazında: " + "; ".join(f"{t}: {n} çağrı, ort {int(a or 0)} ms, {p or 0}+{c or 0} token"
                                              for t, n, a, p, c in rows))
    return 0


def cmd_setting(args) -> int:
    s = get_settings()
    with make_sessionmaker(s.database_url)() as ses:
        if args.key:
            set_runtime_setting(ses, args.key, args.value, by="cli")
            ses.commit()
        eff = effective_settings(ses, s)
        for k in sorted(RUNTIME_KEYS):
            print(f"{k:<26} {getattr(eff, k)}")
    return 0


def cmd_eval(args) -> int:
    from app.ai.evaluation import run_eval

    s = get_settings()
    if args.samples:
        s = s.model_copy(update={"relevance_samples": args.samples})
    report = run_eval(args.file, _llm_or_exit(s), s)
    print(report.render())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mevzuat-ai", description="İlgililik ve önem sınıflandırması (İK-3)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("llm-check").set_defaults(fn=cmd_llm_check)
    p = sub.add_parser("classify")
    p.add_argument("--id", type=int, action="append")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--force", action="store_true", help="önbelleği ve durumu yok say, yeniden sınıflandır")
    p.add_argument("--include-baseline", action="store_true",
                   help="ilk taramadaki eski (BASELINE) kayıtları da sınıflandır (geriye dönük)")
    p.set_defaults(fn=cmd_classify)
    p = sub.add_parser("show")
    p.add_argument("reg_id", type=int)
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("calls")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_calls)
    p = sub.add_parser("setting")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(fn=cmd_setting)
    p = sub.add_parser("eval")
    p.add_argument("file")
    p.add_argument("--samples", type=int)
    p.set_defaults(fn=cmd_eval)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
