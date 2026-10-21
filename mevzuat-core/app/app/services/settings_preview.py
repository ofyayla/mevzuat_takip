"""YZ ayarı değişikliğinin etki önizlemesi (Yönetim → YZ ayarları): LLM çağrısı yapmaz.

Son ``WINDOW_DAYS`` gün içinde sınıflandırılan kayıtların, sınıflandırma anında saklanan bileşenlerinden
(``classification.relevance``: model skoru, oy oranı, kural sinyali, belirsizlik bayrakları) yeni ağırlık/eşiklerle ilgililik
kararı yeniden hesaplanır ve şu anki ayarlarla yeniden hesaplanan karar ile karşılaştırılır. Mükerrer eşikleri için bekleyen
birleştirme adaylarının skorları kullanılır.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.common import RUNTIME_KEYS, check_consistency, effective_settings, parse_runtime_value
from app.db import LlmCall, Regulation
from app.services.regulations import ApiError
from app.settings import Settings

WINDOW_DAYS = 30
_NEEDED = ("model_score", "vote_ratio", "rules", "model_uncertain", "split_votes", "has_text")


def parse_values(values: dict[str, str]) -> dict:
    """Alan bazlı doğrulama: ``{anahtar: ileti}`` ile 400 (PUT /admin/settings ile aynı biçim)."""
    fields, parsed = {}, {}
    for key, value in values.items():
        if key not in RUNTIME_KEYS:
            fields[key] = f"değiştirilebilir ayarlar: {', '.join(sorted(RUNTIME_KEYS))}"
            continue
        try:
            parsed[key] = parse_runtime_value(key, value)
        except ValueError as e:
            fields[key] = str(e)
    if fields:
        raise ApiError(400, "invalid_setting", "; ".join(fields.values()), {"fields": fields})
    return parsed


def _relevant(comp: dict, s: Settings) -> bool:
    score = round(s.relevance_weight_model * comp["model_score"] + s.relevance_weight_votes * comp["vote_ratio"]
                  + s.relevance_weight_rules * comp["rules"], 4)
    return bool(score >= s.relevance_threshold or comp["model_uncertain"] or comp["split_votes"]
                or (not comp["has_text"] and score >= s.relevance_threshold / 2))


def preview(session: Session, settings: Settings, values: dict[str, str]) -> dict:
    parsed = parse_values(values)
    cur = effective_settings(session, settings)
    new = cur.model_copy(update=parsed)
    try:
        check_consistency(new)
    except ValueError as e:
        raise ApiError(400, "invalid_setting", str(e), {"fields": {}}) from e

    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    to_rel = to_irr = same = skipped = 0
    for (cls,) in session.execute(select(Regulation.classification).where(Regulation.classified_at >= since)):
        comp = ((cls or {}).get("relevance") or {})
        if any(k not in comp for k in _NEEDED):
            skipped += 1
            continue
        before, after = _relevant(comp, cur), _relevant(comp, new)
        if before == after:
            same += 1
        elif after:
            to_rel += 1
        else:
            to_irr += 1

    review = [(reg.extra or {}).get("dedupe_candidates") or [] for reg in session.scalars(
        select(Regulation).where(Regulation.needs_dedupe_review.is_(True)))]
    scores = [max((c.get("score") or 0) for c in cands) for cands in review if cands]
    calls = session.scalar(select(func.count()).select_from(LlmCall).where(
        LlmCall.task == "relevance", LlmCall.created_at >= since)) or 0
    factor = new.relevance_samples / cur.relevance_samples if cur.relevance_samples else 1
    return {
        "windowDays": WINDOW_DAYS, "evaluated": to_rel + to_irr + same, "skipped": skipped,
        "changed": sorted(k for k, v in parsed.items() if getattr(cur, k) != v),
        "relevance": {"toRelevant": to_rel, "toIrrelevant": to_irr, "unchanged": same},
        "dedupe": {"pendingReview": len(scores),
                   "wouldAutoMerge": sum(1 for x in scores if x >= new.dedupe_auto_threshold),
                   "wouldLeaveReview": sum(1 for x in scores if x < new.dedupe_review_threshold)},
        "llm": {"relevanceSamples": {"current": cur.relevance_samples, "new": new.relevance_samples},
                "relevanceCallsPerDay": {"current": round(calls / WINDOW_DAYS, 1),
                                         "projected": round(calls / WINDOW_DAYS * factor, 1)}},
    }
