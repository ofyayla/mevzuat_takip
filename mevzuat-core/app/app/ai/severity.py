"""İK-3 önem derecesi: LLM + Başkanlık kural katmanı (plan §6 İK-3).

LLM taksonomideki derece tanımlarına göre önerir; ardından kurallar uygulanır:
  1. Taban (en az "Yüksek"): kritik kalıplar (idari para cezası, sermaye yeterliliği, zorunlu karşılık …) veya
     yayım tarihinde yürürlüğe girip bankaları doğrudan muhatap alan düzenleme.
  2. Tavan (en fazla): türe göre (Duyuru/Basın Duyurusu/Bülten/İlan → "Orta"). Başkanlık kuralı LLM'den ve tabandan
     önceliklidir.
Uygulanan kurallar ``classification.severity_rules`` alanına yazılır (portalda gerekçe olarak gösterilebilir).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from app.ai.common import SEVERITIES, Knowledge, RegulationContext, messages
from app.ai.llm_client import LLM
from app.collectors.dates import tr_lower

PROMPT = "v1"
_RANK = {s: i for i, s in enumerate(reversed(SEVERITIES))}   # Düşük=0 … Kritik=3


class SeverityOut(BaseModel):
    severity: Literal["Kritik", "Yüksek", "Orta", "Düşük"]
    rationale: str
    criteria: list[str]


@dataclass
class SeverityDecision:
    severity: str
    rationale: str
    llm_severity: str
    applied_rules: list[str] = field(default_factory=list)
    call_id: int | None = None


def _flex(pattern: str) -> str:
    """Kalıptaki boşluklar PDF metnindeki satır sonlarına da uysun."""
    return re.sub(r"(?:\\ | )+", r"\\s+", pattern)


def apply_rules(llm_severity: str, reg_type: str | None, text: str, rules: dict,
                titles: str = "") -> tuple[str, list[str]]:
    sev, applied = llm_severity, []
    low = tr_lower(text)
    crit = [p for p in rules.get("critical_patterns", []) if re.search(_flex(tr_lower(p)), low)]
    immediate = any(re.search(_flex(re.escape(tr_lower(p))), low) for p in rules.get("immediate_effect_patterns", []))
    addressed = any(re.search(rf"\b{_flex(re.escape(tr_lower(p)))}\b", low)
                    for p in rules.get("bank_addressee_patterns", []))
    if (crit or (immediate and addressed)) and _RANK[sev] < _RANK["Yüksek"]:
        sev = "Yüksek"
        applied.append("taban:kritik_kalip:" + ",".join(crit) if crit else "taban:hemen_yururluk+banka_muhatap")
    caps = []
    if cap := rules.get("max_by_type", {}).get(reg_type or ""):
        caps.append((cap, f"tavan:{reg_type}→{cap}"))
    low_titles = tr_lower(titles)
    for pattern, cap in rules.get("max_by_title", {}).items():
        if re.search(tr_lower(pattern), low_titles):
            caps.append((cap, f"tavan:başlık '{pattern}'→{cap}"))
    for cap, label in sorted(caps, key=lambda c: _RANK[c[0]]):
        if _RANK[sev] > _RANK[cap]:
            sev = cap
            applied.append(label)
    return sev, applied


def assess(llm: LLM, ctx: RegulationContext, kn: Knowledge, *, reg_type: str | None, topics: list[str],
           relevance_rationale: str, session=None, force: bool = False) -> SeverityDecision:
    tx = kn.taxonomy
    msgs = messages("severity", PROMPT, {"institution": tx["institution"], "severity": tx.get("severity", {})},
                    {"title": ctx.title, "alt_titles": ctx.alt_titles or [], "issuer": ctx.issuer, "reg_type": reg_type, "topics": topics,
                     "relevance_rationale": relevance_rationale, "text": ctx.text})
    res = llm.complete_json("severity", f"severity.{PROMPT}+{kn.version}", msgs, SeverityOut, temperature=0.1,
                            session=session, regulation_id=ctx.regulation.id, force=force)
    out = res.outputs[0]
    sev, applied = apply_rules(out.severity, reg_type, ctx.all_titles + "\n" + ctx.text, tx.get("severity_rules", {}),
                               titles=ctx.all_titles)
    return SeverityDecision(sev, out.rationale, out.severity, applied, res.call_id)
