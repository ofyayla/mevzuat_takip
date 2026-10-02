"""DMZ crawler ve aktarım hattının sağlığı (CRAWL_MODE=remote).

Kaynak kontrolleri (checks.py) PostgreSQL'e aktarılmış taramalara bakar; crawler tamamen durursa bunlar ancak kaynağın
sessizlik toleransı dolunca (saatler) alarm verir. Bu kontroller sorunu dakikalar içinde yakalar:

| Kontrol        | Mantık                                                                        | Durum   |
|----------------|-------------------------------------------------------------------------------|---------|
| crawler_down   | MongoDB'ye erişilemiyor veya son crawler sinyali > CRAWLER_STALE_MINUTES      | down    |
| ingest_lag     | 15 dakikadan eski, aktarılmamış belge var (ingest görevi çalışmıyor/hatalı)   | delayed |
| crawl_request  | 30 dakikadır bekleyen veya son 24 saatte başarısız olmuş tarama talebi        | warning |
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.monitoring.checks import Finding
from app.settings import Settings


def crawler_findings(settings: Settings, now: datetime, db=None) -> list[Finding]:
    if settings.crawl_mode != "remote":
        return []
    from app.mongo import DOCS, REQUESTS, crawl_db, last_heartbeat

    try:
        db = db if db is not None else crawl_db(settings)
        hb = last_heartbeat(db)
        lagging = db[DOCS].count_documents({"synced": False, "updated_at": {"$lt": now - timedelta(minutes=15)}})
        stuck = db[REQUESTS].count_documents({"status": "pending", "created_at": {"$lt": now - timedelta(minutes=30)}})
        failed = list(db[REQUESTS].find({"status": "failed", "finished_at": {"$gte": now - timedelta(hours=24)}},
                                        {"source_code": 1, "kind": 1, "error": 1}, limit=5))
    except Exception as e:  # noqa: BLE001 — Mongo erişilemezse bu da bir bulgudur
        return [Finding("crawler_down", "crawler_down", "down", f"MongoDB'ye erişilemiyor: {type(e).__name__}: {e}")]
    out: list[Finding] = []
    if hb is None or now - hb["at"] > timedelta(minutes=settings.crawler_stale_minutes):
        last = f"son sinyal {hb['at']:%d.%m %H:%M}" if hb else "hiç sinyal yok"
        out.append(Finding("crawler_down", "crawler_down", "down", f"DMZ crawler çalışmıyor ({last})",
                           details={"worker": hb["_id"] if hb else None}))
    if lagging:
        out.append(Finding("ingest_lag", "ingest_lag", "delayed",
                           f"{lagging} belge 15 dakikadan uzun süredir aktarılmayı bekliyor (ingest görevi)"))
    if stuck:
        out.append(Finding("crawl_request_stuck", "crawl_request", "warning",
                           f"{stuck} tarama talebi 30 dakikadır crawler tarafından alınmadı"))
    for r in failed:
        out.append(Finding(f"crawl_request_failed:{r['_id']}", "crawl_request", "warning",
                           f"{r['source_code']} {r['kind']} talebi başarısız: {r.get('error')}",
                           source_code=r["source_code"]))
    return out
