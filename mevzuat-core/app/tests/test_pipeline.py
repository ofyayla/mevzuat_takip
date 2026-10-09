"""Süreç haritası: /api/v1/pipeline sayıları, düğüm kayıtları, Mermaid çıktısı ve /surec sayfası."""
from __future__ import annotations

import re
from pathlib import Path

from app.services.pipeline import EDGES, NODES
from tests.test_api import _ready, client  # noqa: F401
from tests.test_processing import env  # noqa: F401

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def test_pipeline_counts_and_node_items(settings, env, client):  # noqa: F811
    reg_id = _ready(settings, env)
    c, _ = client
    body = c.get("/api/v1/pipeline").json()
    counts = {n["id"]: n["count"] for n in body["nodes"]}
    assert counts["reg:READY"] == 1 and counts["review:Bekliyor"] == 1 and counts["raw:LINKED"] >= 1
    assert counts["sources"] > 0 and body["unknownStatuses"] == []
    assert {e["source"] for e in body["edges"]} | {e["target"] for e in body["edges"]} <= set(counts)

    items = c.get("/api/v1/pipeline/nodes/review:Bekliyor").json()["items"]
    assert [i["id"] for i in items] == [reg_id] and items[0]["visible"] is True
    raw = c.get("/api/v1/pipeline/nodes/raw:LINKED").json()["items"]
    assert raw and all(i["regulationId"] for i in raw)
    assert c.get("/api/v1/pipeline/nodes/fetch").status_code == 200
    assert c.get("/api/v1/pipeline/nodes/bilinmeyen").json()["error"]["code"] == "not_found"
    assert c.get("/api/v1/pipeline", params={"days": 0}).json()["error"]["code"] == "validation_error"

    text = c.get("/api/v1/pipeline", params={"format": "mermaid", "days": 30}).text
    assert text.startswith("flowchart LR") and "reg_READY" in text and "review_Bekliyor" in text


def test_pipeline_page_is_served(settings, env, client):  # noqa: F811
    c, _ = client
    r = c.get("/surec")
    assert r.status_code == 200 and "/pipeline" in r.text
    assert "./surec" in c.get("/").text


def test_pipeline_graph_covers_all_statuses():
    """Kodda atanan her processing_status grafikte bir düğüm olmalı (yeni durum eklenince harita da güncellensin)."""
    ids = {n.id for n in NODES}
    assigned = set()
    for path in APP_DIR.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        assigned |= set(re.findall(r'processing_status\s*(?:=|==)\s*"([A-Z_]+)"', src))
        assigned |= set(re.findall(r'processing_status="([A-Z_]+)"', src))
        assigned |= set(re.findall(r'"(RELEVANT|IRRELEVANT)" if', src))
    missing = {s for s in assigned if f"raw:{s}" not in ids and f"reg:{s}" not in ids}
    assert not missing, f"grafikte olmayan durumlar: {missing}"
    assert all(e.source in ids and e.target in ids for e in EDGES)
