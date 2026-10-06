"""Süreç haritası: işleme hattının durum makinesi ve her durumdaki canlı kayıt sayıları (portal ``/surec``).

Hattın asıl durumu veritabanındadır (``raw_document.processing_status``, ``regulation.processing_status``,
``regulation.review_status``); bu modül o alanları gruplayıp sayar ve geçişleri (hangi görev/kuyruk, hangi modül)
grafik olarak verir. Grafik tanımı burada elle tutulur; yeni bir durum eklenirse ``NODES``/``EDGES`` güncellenir —
``test_pipeline_graph_covers_all_statuses`` kodda atanan durumların grafikte olduğunu denetler.

``mermaid()`` aynı grafiği Mermaid ``flowchart`` metni olarak üretir (belgelere/README'ye yapıştırmak için).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Alert, FetchRun, RawDocument, Regulation
from app.services.regulations import VISIBLE_STATUSES, _iso, visible
from app.settings import Settings


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    layer: str          # toplama | belge | duzenleme | yz | portal
    kind: str           # flow | ok | wait | fail | terminal
    description: str
    module: str = ""


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str
    queue: str = ""     # Celery kuyruğu (varsa)


LAYERS = [
    {"id": "toplama", "label": "Toplama (İK-1)"},
    {"id": "belge", "label": "Ham belge (İK-2)"},
    {"id": "duzenleme", "label": "Düzenleme kaydı (İK-2)"},
    {"id": "yz", "label": "YZ analizi (İK-3..5)"},
    {"id": "portal", "label": "Portal incelemesi (İK-6)"},
]

# Ham belge ve düzenleme durumlarının düğüm kimlikleri ``raw:<DURUM>`` / ``reg:<DURUM>``, inceleme ``review:<DURUM>``
NODES: list[Node] = [
    Node("sources", "Kaynaklar", "toplama", "flow",
         "sources.yaml'daki etkin resmî kaynaklar (Resmî Gazete, BDDK, SPK, TCMB …).", "config/sources.yaml"),
    Node("fetch", "Tarama", "toplama", "flow",
         "Kaynak başına tarama çalışması (fetch_run): liste, detay, ekler, arşiv.", "app/collectors/runner.py"),
    Node("raw:FETCHED", "İndirildi", "belge", "wait",
         "Arşive yazıldı, metin çıkarma bekliyor.", "app/collectors/store.py"),
    Node("raw:EXTRACT_FAILED", "Çıkarma hatası", "belge", "fail",
         "Metin çıkarılamadı; PROCESS_MAX_ATTEMPTS'e kadar yeniden denenir.", "app/processing/pipeline.py"),
    Node("raw:EXTRACTED", "Metin çıkarıldı", "belge", "wait",
         "HTML/PDF/DOCX/OCR metni hazır, tekilleştirme bekliyor.", "app/processing/extract.py"),
    Node("raw:LINKED", "Bağlandı", "belge", "ok",
         "Bir düzenleme kaydına bağlandı (yeni veya mevcut).", "app/processing/dedupe.py"),
    Node("reg:BASELINE", "İlk tarama", "duzenleme", "terminal",
         "Kanalın ilk taramasında sitede zaten duran içerik; YZ hattına girmez.", "app/processing/dedupe.py"),
    Node("reg:NEW", "Yeni", "duzenleme", "wait",
         "Tekil düzenleme kaydı oluştu, ilgililik sınıflandırması bekliyor.", "app/processing/dedupe.py"),
    Node("reg:MERGED", "Birleştirildi", "duzenleme", "terminal",
         "Başka bir kayıtla aynı düzenleme olduğu anlaşıldı ve ona katıldı.", "app/processing/dedupe.py"),
    Node("reg:IRRELEVANT", "İlgisiz", "yz", "terminal",
         "YZ bankayla ilgisiz buldu; portalda görünmez (paralel çalışma raporunda izlenir).", "app/ai/relevance.py"),
    Node("reg:RELEVANT", "İlgili", "yz", "wait",
         "İlgili bulundu, önem derecesi atandı; özet bekleniyor.", "app/ai/classify.py"),
    Node("reg:SUMMARIZED", "Özetlendi", "yz", "wait",
         "Kaynak alıntılarıyla doğrulanmış özet hazır; birim önerisi bekleniyor.", "app/ai/summary.py"),
    Node("reg:READY", "Hazır", "yz", "ok",
         "Özet ve birim önerileri tamam; uzman incelemesine hazır.", "app/ai/unit_matching.py"),
    Node("reg:AI_FAILED", "YZ hatası", "yz", "fail",
         "LLM adımı başarısız; Beat turlarında yeniden denenir.", "app/ai/common.py"),
    Node("review:Bekliyor", "Bekliyor", "portal", "wait",
         "Portalda görünen, uzman kararı bekleyen kayıtlar.", "app/services/regulations.py"),
    Node("review:Onaylandı", "Onaylandı", "portal", "ok",
         "Uzman onayladı; birimlere iletilir (outbox).", "app/services/regulations.py"),
    Node("review:Reddedildi", "Reddedildi", "portal", "terminal",
         "Uzman reddetti; few-shot örneği olarak geri beslenir.", "app/services/regulations.py"),
]

EDGES: list[Edge] = [
    Edge("sources", "fetch", "cron / şimdi tara", "collect"),
    Edge("fetch", "raw:FETCHED", "yeni / değişmiş içerik"),
    Edge("raw:FETCHED", "raw:EXTRACTED", "metin çıkarma, OCR", "process"),
    Edge("raw:FETCHED", "raw:EXTRACT_FAILED", "hata", "process"),
    Edge("raw:EXTRACT_FAILED", "raw:EXTRACTED", "yeniden deneme", "process"),
    Edge("raw:EXTRACTED", "raw:LINKED", "tekilleştirme", "process"),
    Edge("raw:LINKED", "reg:NEW", "yeni düzenleme"),
    Edge("raw:LINKED", "reg:BASELINE", "ilk tarama"),
    Edge("reg:NEW", "reg:MERGED", "birleştirme"),
    Edge("reg:BASELINE", "reg:NEW", "yeniden işleme"),
    Edge("reg:NEW", "reg:RELEVANT", "ilgililik + önem", "ai"),
    Edge("reg:NEW", "reg:IRRELEVANT", "ilgisiz", "ai"),
    Edge("reg:NEW", "reg:AI_FAILED", "hata", "ai"),
    Edge("reg:RELEVANT", "reg:SUMMARIZED", "özet + yürürlük", "ai"),
    Edge("reg:SUMMARIZED", "reg:READY", "birim önerisi", "ai"),
    Edge("reg:RELEVANT", "reg:AI_FAILED", "hata", "ai"),
    Edge("reg:SUMMARIZED", "reg:AI_FAILED", "hata", "ai"),
    Edge("reg:AI_FAILED", "reg:RELEVANT", "yeniden deneme", "ai"),
    Edge("reg:READY", "review:Bekliyor", "portala düşer"),
    Edge("review:Bekliyor", "review:Onaylandı", "onay"),
    Edge("review:Bekliyor", "review:Reddedildi", "red"),
]

_NODE_IDS = {n.id for n in NODES}


def _since(days: int | None) -> datetime | None:
    return datetime.now(timezone.utc) - timedelta(days=days) if days else None


def _raw_base(since: datetime | None):
    conds = [RawDocument.is_latest.is_(True), RawDocument.role != "attachment"]
    if since:
        conds.append(RawDocument.fetched_at >= since)
    return conds


def _reg_base(since: datetime | None):
    return [Regulation.detected_at >= since] if since else []


def overview(session: Session, settings: Settings, days: int | None = None) -> dict:
    since = _since(days)
    counts: dict[str, int] = {}

    run_q = select(FetchRun.status, func.count()).group_by(FetchRun.status)
    if since:
        run_q = run_q.where(FetchRun.started_at >= since)
    runs = dict(session.execute(run_q).all())
    counts["fetch"] = sum(runs.values())

    for status, n in session.execute(select(RawDocument.processing_status, func.count())
                                     .where(*_raw_base(since)).group_by(RawDocument.processing_status)):
        counts[f"raw:{status}"] = n
    for status, n in session.execute(select(Regulation.processing_status, func.count())
                                     .where(*_reg_base(since)).group_by(Regulation.processing_status)):
        counts[f"reg:{status}"] = n
    for status, n in session.execute(select(Regulation.review_status, func.count())
                                     .where(visible(), *_reg_base(since)).group_by(Regulation.review_status)):
        counts[f"review:{status}"] = n

    from app.collectors.config import load_sources

    counts["sources"] = len(load_sources(settings.sources_file).enabled())
    alerts = dict(session.execute(select(Alert.severity, func.count()).where(Alert.resolved_at.is_(None))
                                  .group_by(Alert.severity)).all())
    unknown = sorted(k for k in counts if k not in _NODE_IDS)   # grafikte olmayan bir durum (yeni eklenmiş olabilir)
    return {
        "generatedAt": _iso(datetime.now(timezone.utc)),
        "days": days,
        "layers": LAYERS,
        "nodes": [{"id": n.id, "label": n.label, "layer": n.layer, "kind": n.kind, "description": n.description,
                   "module": n.module, "count": counts.get(n.id, 0)} for n in NODES],
        "edges": [{"source": e.source, "target": e.target, "label": e.label, "queue": e.queue} for e in EDGES],
        "fetchRuns": runs,
        "openAlerts": alerts,
        "unknownStatuses": [{"id": k, "count": counts[k]} for k in unknown],
        "visibleStatuses": list(VISIBLE_STATUSES),
    }


def node_items(session: Session, node_id: str, days: int | None = None, limit: int = 20) -> dict:
    """Bir düğümdeki en yeni kayıtlar (düğüm paneli)."""
    since = _since(days)
    kind, _, status = node_id.partition(":")
    if kind == "raw":
        rows = session.scalars(select(RawDocument).where(RawDocument.processing_status == status, *_raw_base(since))
                               .order_by(RawDocument.fetched_at.desc()).limit(limit))
        items = [{"id": r.id, "title": r.title or r.url, "source": r.source_code, "url": r.url,
                  "at": _iso(r.fetched_at), "regulationId": r.regulation_id, "error": r.extract_error} for r in rows]
    elif kind in ("reg", "review"):
        conds = [Regulation.processing_status == status] if kind == "reg" else [
            visible(), Regulation.review_status == status]
        rows = session.scalars(select(Regulation).where(*conds, *_reg_base(since))
                               .order_by(Regulation.detected_at.desc()).limit(limit))
        items = [{"id": r.id, "title": r.title, "source": r.issuer or r.primary_source_code, "at": _iso(r.detected_at),
                  "severity": r.severity, "regulationId": r.id,
                  "visible": bool(r.is_relevant) and r.processing_status in VISIBLE_STATUSES,
                  "error": (r.extra or {}).get("ai_error")} for r in rows]
    elif node_id == "fetch":
        q = select(FetchRun).order_by(FetchRun.started_at.desc()).limit(limit)
        if since:
            q = q.where(FetchRun.started_at >= since)
        items = [{"id": r.id, "title": f"{r.source_code} · {r.status}", "source": r.source_code,
                  "at": _iso(r.started_at), "detail": f"{r.items_listed} listelendi · {r.items_new} yeni · "
                                                      f"{r.items_changed} değişti",
                  "error": "; ".join(str(e) for e in (r.errors or [])[:2]) or None} for r in session.scalars(q)]
    elif node_id == "sources":
        items = []
    else:
        from app.services.regulations import ApiError

        raise ApiError(404, "not_found", f"bilinmeyen düğüm: {node_id}")
    return {"node": node_id, "items": items}


def mermaid(data: dict | None = None) -> str:
    """Mermaid ``flowchart LR`` metni; ``data`` (``overview`` çıktısı) verilirse düğümlerde sayılar da yazar."""
    counts = {n["id"]: n["count"] for n in data["nodes"]} if data else {}

    def nid(x: str) -> str:
        return x.replace(":", "_").replace("ı", "i").replace("İ", "I")

    lines = ["flowchart LR"]
    for layer in LAYERS:
        lines.append(f'  subgraph {layer["id"]}["{layer["label"]}"]')
        for n in (n for n in NODES if n.layer == layer["id"]):
            label = f'{n.label}<br/>{counts[n.id]}' if n.id in counts else n.label
            lines.append(f'    {nid(n.id)}["{label}"]:::{n.kind}')
        lines.append("  end")
    for e in EDGES:
        label = f"{e.label} · {e.queue}" if e.queue else e.label
        arrow = "-.->" if "hata" in e.label or "yeniden" in e.label else "-->"
        lines.append(f'  {nid(e.source)} {arrow}|"{label}"| {nid(e.target)}')
    lines += ["  classDef ok fill:#e3f4ea,stroke:#2f8a57", "  classDef wait fill:#eef4ff,stroke:#3b6fc4",
              "  classDef fail fill:#fde8e6,stroke:#c4382b", "  classDef terminal fill:#f2f2f2,stroke:#9a9a9a",
              "  classDef flow fill:#e6f3f1,stroke:#1f7a6d"]
    return "\n".join(lines) + "\n"
