"""İK-3 ilgililik tespiti ve göreli güven skoru (plan §6 İK-3).

Model eğitimi yapılmadığı için kalibre edilmiş olasılık yoktur; skor üç bileşenden hesaplanır:
  - modelin kendi skoru (örneklerin ortalaması)                       ağırlık 0.6
  - self-consistency: n=3 örneklemede "ilgili" oyu oranı               ağırlık 0.3
  - kural sinyalleri: kaynak/kurum önceliği + banka anahtar kelimeleri ağırlık 0.1
Ağırlıklar ve eşikler ``app_setting`` ile çalışma zamanında ayarlanır.

Yüksek duyarlılık: skor eşiğin altında olsa bile model tereddüt bildirdiyse (``uncertain``), oylar bölündüyse ya da
metin çıkarılamadıysa kayıt portala düşer ve güven "Düşük" gösterilir. Eşik altı kayıtlar silinmez (IRRELEVANT).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.ai.common import REG_TYPES, Knowledge, RegulationContext, messages
from app.ai.llm_client import LLM
from app.collectors.dates import tr_lower
from app.settings import BACKEND_DIR, Settings

PROMPT = "v1"
FEWSHOT_FILE = BACKEND_DIR / "config" / "fewshot_relevance.yaml"


class RelevanceOut(BaseModel):
    is_relevant: bool
    relevance_score: float = Field(ge=0.0, le=1.0)
    reg_type: str
    matched_topics: list[str]
    matched_criteria: list[str]
    rationale: str
    uncertain: bool


@dataclass
class RelevanceDecision:
    is_relevant: bool
    score: float
    band: str
    uncertain: bool
    reg_type: str | None
    topics: list[str]
    criteria: list[str]
    rationale: str
    components: dict = field(default_factory=dict)
    call_id: int | None = None


def rule_signal(ctx: RegulationContext, taxonomy: dict) -> tuple[float, dict]:
    rules = taxonomy.get("relevance_rules", {})
    src_prio = max([rules.get("source_priority", {}).get(s, 0.3) for s in ctx.sources] or [0.3])
    issuer = ctx.regulation.issuer
    if issuer and issuer in rules.get("issuer_priority", {}):
        src_prio = max(src_prio, rules["issuer_priority"][issuer])
    elif "RESMI_GAZETE" in ctx.sources and len(ctx.sources) == 1:
        src_prio = 0.2 if issuer is None else src_prio   # RG'de takip dışı kurum (üniversite vb.)
    low = tr_lower(ctx.all_titles + "\n" + ctx.text[:6000])
    hits = sorted({k for k in rules.get("bank_keywords", []) if tr_lower(k) in low})
    kw = min(1.0, len(hits) / 2)
    return round(0.5 * src_prio + 0.5 * kw, 4), {"source_priority": src_prio, "keyword_hits": hits}


def band_for(score: float, settings: Settings) -> str:
    if score >= settings.confidence_high:
        return "Yüksek"
    return "Orta" if score >= settings.confidence_medium else "Düşük"


def load_fewshot(path: Path = FEWSHOT_FILE, limit: int = 6) -> list[dict]:
    if not path.exists():
        return []
    return (yaml.safe_load(path.read_text("utf-8")) or {}).get("examples", [])[:limit]


def relevance_messages(ctx: RegulationContext, kn: Knowledge, examples: list[dict]) -> list[dict]:
    tx = kn.taxonomy
    return messages("relevance", PROMPT,
                    {"institution": tx["institution"], "guide": kn.guide, "topics": tx.get("topics", []),
                     "clearly_irrelevant": tx.get("clearly_irrelevant", []), "reg_types": REG_TYPES},
                    {"examples": examples, "title": ctx.title, "alt_titles": ctx.alt_titles or [], "issuer": ctx.issuer, "sources": ctx.sources,
                     "publish_date": ctx.publish_date, "reg_type_hint": ctx.reg_type_hint, "has_text": ctx.has_text,
                     "text": ctx.text})


def assess(llm: LLM, ctx: RegulationContext, kn: Knowledge, settings: Settings, *, session=None,
           force: bool = False) -> RelevanceDecision:
    examples = load_fewshot()
    msgs = relevance_messages(ctx, kn, examples)
    n = max(1, int(settings.relevance_samples))
    res = llm.complete_json("relevance", f"relevance.{PROMPT}+{kn.version}", msgs, RelevanceOut,
                            temperature=settings.relevance_sample_temperature if n > 1 else 0.1, n=n,
                            session=session, regulation_id=ctx.regulation.id, force=force)
    outs = res.outputs
    model_score = sum(min(1.0, max(0.0, o.relevance_score)) for o in outs) / len(outs)
    vote_ratio = sum(o.is_relevant for o in outs) / len(outs)
    rules, rule_detail = rule_signal(ctx, kn.taxonomy)
    score = round(settings.relevance_weight_model * model_score + settings.relevance_weight_votes * vote_ratio
                  + settings.relevance_weight_rules * rules, 4)
    split_votes = 0 < vote_ratio < 1
    uncertain_votes = [o.uncertain for o in outs]
    # Plan tek çağrı için "uncertain=true → portal" der; n örneklemede (sıcaklık 0.7) tek örneğin tereddüdü gürültüdür
    # (canlı ölçüm: oylar ve skorlar oybirliğiyle ilgisizken tek örnek tereddüt bildirdi). Çoğunluk aranır; oyların
    # bölünmesi ayrıca split_votes ile portala düşürür.
    model_uncertain = sum(uncertain_votes) * 2 >= len(uncertain_votes) and any(uncertain_votes)
    uncertain = model_uncertain or split_votes or not ctx.has_text
    # Yüksek duyarlılık: model tereddüt bildirdiyse veya oylar bölündüyse skor ne olursa olsun portala düşer.
    # Yalnızca metnin çıkarılamamış olması (OCR bekleyen taranmış belge) tek başına yeterli sayılmaz — aksi halde RG'nin
    # taranmış PDF'lerinin tamamı düşerdi; bu durumda eşiğin yarısı uygulanır (başlığa göre değerlendirme).
    relevant = score >= settings.relevance_threshold or model_uncertain or split_votes or \
        (not ctx.has_text and score >= settings.relevance_threshold / 2)
    band = "Düşük" if uncertain else band_for(score, settings)
    best = max(outs, key=lambda o: (o.is_relevant == (vote_ratio >= 0.5), o.relevance_score))
    types = [o.reg_type for o in outs if o.reg_type in REG_TYPES]
    reg_type = max(set(types), key=types.count) if types else None
    topics = sorted({t for o in outs if o.is_relevant for t in o.matched_topics})
    return RelevanceDecision(
        is_relevant=relevant, score=score, band=band, uncertain=uncertain, reg_type=reg_type, topics=topics,
        criteria=best.matched_criteria, rationale=best.rationale, call_id=res.call_id,
        components={"model_score": round(model_score, 4), "vote_ratio": round(vote_ratio, 4), "rules": rules,
                    "rule_detail": rule_detail, "votes": [o.is_relevant for o in outs], "samples": len(outs),
                    "uncertain_votes": uncertain_votes, "sample_scores": [round(o.relevance_score, 3) for o in outs],
                    "model_uncertain": model_uncertain, "split_votes": split_votes, "has_text": ctx.has_text,
                    "threshold": settings.relevance_threshold, "cached": res.cached})


_ws = re.compile(r"\s+")
