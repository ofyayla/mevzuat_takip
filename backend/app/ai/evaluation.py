"""İlgililik/önem değerlendirme seti çalıştırıcı (plan §10 "LLM değerlendirme", ``make eval``).

Set biçimi (JSON Lines), her satır bir örnek:
  {"id": "...", "title": "...", "issuer": "BDDK", "issuer_code": "BDDK", "sources": ["BDDK"],
   "publish_date": "2026-09-19", "reg_type_hint": "Kurul Kararı", "text": "...",
   "expected_relevant": true, "expected_severity": "Yüksek", "note": "..."}

Ölçütler: recall (kaçırma = ilgili olup elenen, en kritik), precision (gereksiz bildirim), F1, eşik taraması
(skorlar saklanır; farklı eşiklerde recall/precision yeniden hesaplanır) ve önem derecesi tam/±1 isabeti.
DB'ye yazmaz; LLM çağrılarını önbelleklemez.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.ai import relevance, severity
from app.ai.common import SEVERITIES, RegulationContext, knowledge
from app.ai.llm_client import LLM, LLMError
from app.db import Regulation
from app.settings import Settings


@dataclass
class EvalRow:
    id: str
    title: str
    expected: bool
    predicted: bool | None
    score: float | None
    uncertain: bool | None
    expected_severity: str | None
    severity: str | None
    rationale: str = ""
    error: str | None = None
    components: dict | None = None


@dataclass
class EvalReport:
    rows: list[EvalRow] = field(default_factory=list)
    threshold: float = 0.3

    def metrics(self, threshold: float | None = None) -> dict:
        tp = fp = fn = tn = 0
        for r in self.rows:
            if r.predicted is None:
                continue
            pred = r.predicted if threshold is None else _decide(r, threshold)
            tp += pred and r.expected
            fp += pred and not r.expected
            fn += (not pred) and r.expected
            tn += (not pred) and not r.expected
        recall = tp / (tp + fn) if tp + fn else 1.0
        precision = tp / (tp + fp) if tp + fp else 1.0
        f1 = 2 * recall * precision / (recall + precision) if recall + precision else 0.0
        return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "recall": round(recall, 3),
                "precision": round(precision, 3), "f1": round(f1, 3)}

    def severity_accuracy(self) -> dict:
        rank = {s: i for i, s in enumerate(SEVERITIES)}
        pairs = [(r.expected_severity, r.severity) for r in self.rows
                 if r.expected_severity and r.severity and r.expected]
        if not pairs:
            return {"n": 0}
        exact = sum(a == b for a, b in pairs)
        near = sum(abs(rank[a] - rank[b]) <= 1 for a, b in pairs)
        return {"n": len(pairs), "exact": round(exact / len(pairs), 3), "within_1": round(near / len(pairs), 3)}

    def render(self) -> str:
        m = self.metrics()
        lines = [f"Örnek: {len(self.rows)}  hata: {sum(r.error is not None for r in self.rows)}",
                 f"Eşik {self.threshold}: recall={m['recall']} precision={m['precision']} f1={m['f1']} "
                 f"(tp={m['tp']} fp={m['fp']} fn={m['fn']} tn={m['tn']})",
                 "Eşik taraması: " + "  ".join(
                     f"{t:.2f}→R{self.metrics(t)['recall']}/P{self.metrics(t)['precision']}"
                     for t in (0.2, 0.3, 0.4, 0.5, 0.6)),
                 f"Önem: {self.severity_accuracy()}"]
        misses = [r for r in self.rows if r.expected and r.predicted is False]
        if misses:
            lines.append("KAÇIRILAN (ilgili ama elendi):")
            lines += [f"  - {r.id} {r.title[:90]} (skor {r.score}) — {r.rationale[:120]}" for r in misses]
        noise = [r for r in self.rows if not r.expected and r.predicted]
        if noise:
            lines.append("GEREKSİZ BİLDİRİM:")
            lines += [f"  - {r.id} {r.title[:80]} (skor {r.score}, oylar={_c(r, 'votes')}, "
                      f"tereddüt={_c(r, 'uncertain_votes')}, örnek skorlar={_c(r, 'sample_scores')})" for r in noise]
        sev_miss = [r for r in self.rows if r.expected_severity and r.severity and r.severity != r.expected_severity]
        if sev_miss:
            lines.append("ÖNEM FARKI:")
            lines += [f"  - {r.id} {r.title[:80]}: beklenen {r.expected_severity}, çıkan {r.severity}" for r in sev_miss]
        return "\n".join(lines)


def _decide(r: EvalRow, t: float) -> bool:
    """relevance.assess ile aynı karar kuralı, farklı eşikte (tarama için)."""
    c = r.components or {}
    return r.score >= t or bool(c.get("model_uncertain")) or bool(c.get("split_votes")) or \
        (not c.get("has_text", True) and r.score >= t / 2)


def _c(row: EvalRow, key: str):
    return (row.components or {}).get(key)


def load_set(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text("utf-8").splitlines() if line.strip()
            and not line.lstrip().startswith("//")]


def run_eval(path: str | Path, llm: LLM, settings: Settings) -> EvalReport:
    kn = knowledge(settings)
    report = EvalReport(threshold=settings.relevance_threshold)
    for item in load_set(path):
        reg = Regulation(id=None, title=item["title"], issuer=item.get("issuer_code"), issuer_name=item.get("issuer"),
                         reg_type=item.get("reg_type_hint"), primary_source_code=(item.get("sources") or ["?"])[0],
                         canonical_key="eval")
        text = item.get("text") or item["title"]
        ctx = RegulationContext(reg, item["title"], item.get("issuer"), item.get("sources", []),
                                item.get("publish_date"), item.get("reg_type_hint"),
                                text[:settings.relevance_max_input_chars], len(text) >= 200, item.get("alt_titles", []))
        row = EvalRow(item.get("id", item["title"][:30]), item["title"], bool(item["expected_relevant"]), None, None,
                      None, item.get("expected_severity"), None)
        try:
            rel = relevance.assess(llm, ctx, kn, settings, force=True)
            row.predicted, row.score, row.uncertain, row.rationale = rel.is_relevant, rel.score, rel.uncertain, \
                rel.rationale
            row.components = rel.components
            if rel.is_relevant and item.get("expected_severity"):
                row.severity = severity.assess(llm, ctx, kn, reg_type=rel.reg_type, topics=rel.topics,
                                               relevance_rationale=rel.rationale, force=True).severity
        except LLMError as e:
            row.error = str(e)
        report.rows.append(row)
    return report
