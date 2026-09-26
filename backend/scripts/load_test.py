"""İK-8 yük testi: 1 yıllık veri hacmi üretip portal API'sinin ve izlemenin yanıt sürelerini ölçer.

Hacim (plan §7.4 tahmininden): RG günde ~60 madde (7 tarama/gün), kurumlar iş günü başına toplam ~20 öğe; öğelerin
~%15'i portalda görünür (özet + 2 birim önerisi + 3 denetim olayı). Varsayılan: SQLite (üretimde PostgreSQL daha hızlı
ve eşzamanlıdır; bu ölçüm üst sınır fikri verir).

  python scripts/load_test.py [--db /tmp/yuk.db | postgresql+psycopg://…] [--days 365] [--requests 30]
"""
from __future__ import annotations

import argparse
import random
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import insert

from app.db import (
    AuditEvent,
    FetchRun,
    RawDocument,
    Regulation,
    RegulationSourceLink,
    RegulationSummary,
    Source,
    Unit,
    UnitSuggestion,
    make_sessionmaker,
)
from app.settings import BACKEND_DIR, Settings

SOURCES = ["RESMI_GAZETE", "BDDK", "SPK", "TCMB", "KVKK", "MASAK", "TICARET", "REKABET", "TKBB"]
SEV = ["Kritik", "Yüksek", "Orta", "Düşük"]
WORDS = ("kredi kart sermaye likidite yönetmelik tebliğ karar ödeme elektronik para katılım faizsiz kişisel veri "
         "rapor muhasebe kambiyo döviz zorunlu karşılık sukuk kira sertifika tüketici ücret").split()


def _url(db: str) -> str:
    return db if "://" in db else f"sqlite:///{db}"


def generate(db: str, days: int, seed: int = 7) -> dict:
    rnd = random.Random(seed)
    settings = Settings(_env_file=None, database_url=_url(db), db_auto_create="sqlite" in _url(db))
    sf = make_sessionmaker(settings.database_url)
    end = datetime(2026, 9, 25, 18, tzinfo=timezone.utc)
    start = end - timedelta(days=days)
    counts = dict(runs=0, raw=0, regs=0, visible=0)
    with sf() as s:
        s.execute(insert(Source), [{"code": c, "name": c, "enabled": True, "config": {}} for c in SOURCES])
        units = [f"UNIT_{i:02d}" for i in range(44)]
        s.execute(insert(Unit), [{"code": u, "name": f"Birim {u}", "group_name": "G", "responsibilities": ["x"],
                                  "keywords": [], "regulatory_areas": [], "active": True} for u in units])
        run_id = raw_id = reg_id = 0
        runs, raws, regs, links, summ, sugg, audit = [], [], [], [], [], [], []
        for d in range(days):
            day = start + timedelta(days=d)
            bday = day.weekday() < 5
            for code in SOURCES:
                n_runs = 7 if code == "RESMI_GAZETE" else (3 if bday else 1)
                n_items = rnd.randint(40, 80) if code == "RESMI_GAZETE" else (rnd.randint(0, 5) if bday else 0)
                for r in range(n_runs):
                    run_id += 1
                    t = day + timedelta(hours=2 + r * 3)
                    runs.append({"id": run_id, "source_code": code, "started_at": t, "finished_at": t,
                                 "status": "success", "items_listed": 30, "items_new": n_items if r == 0 else 0,
                                 "items_changed": 0, "requests": 10, "channels": {"k": {"items": 30}}, "errors": [],
                                 "structure_alert": False})
                for i in range(n_items):
                    raw_id += 1
                    reg_id += 1
                    title = " ".join(rnd.choices(WORDS, k=8)).capitalize()
                    t = day + timedelta(hours=2)
                    raws.append({"id": raw_id, "source_code": code, "channel": "k", "external_id": f"{code}-{raw_id}",
                                 "version": 1, "is_latest": True, "role": "main", "url": f"https://{code}/{raw_id}",
                                 "title": title, "published_at": day.date(), "content_type": "text/html",
                                 "content_sha256": "0" * 64, "size_bytes": 1000, "storage_key": "k",
                                 "fetch_run_id": run_id - n_runs + 1, "fetched_at": t,
                                 "processing_status": "LINKED", "is_baseline": False, "extra": {},
                                 "text": title * 20, "regulation_id": reg_id})
                    visible = rnd.random() < 0.15
                    sev = rnd.choice(SEV) if visible else None
                    regs.append({"id": reg_id, "title": title, "issuer": code, "issuer_name": code, "reg_type": "Tebliğ",
                                 "is_amendment": False, "publish_date": day.date(), "primary_source_code": code,
                                 "canonical_key": f"k{reg_id}",
                                 "processing_status": "READY" if visible else "IRRELEVANT",
                                 "review_status": rnd.choice(["Bekliyor", "Onaylandı", "Reddedildi"]) if visible
                                 else "Bekliyor",
                                 "needs_dedupe_review": False, "detected_at": t, "created_at": t, "updated_at": t,
                                 "extra": {}, "is_relevant": visible, "relevance_score": rnd.random(),
                                 "confidence_band": "Orta", "severity": sev, "classification": {},
                                 "classified_at": t, "row_version": 1,
                                 "effective_date": (day + timedelta(days=rnd.randint(0, 60))).date() if visible else None})
                    links.append({"regulation_id": reg_id, "raw_document_id": raw_id, "match_method": "new",
                                  "created_at": t})
                    if visible:
                        counts["visible"] += 1
                        summ.append({"regulation_id": reg_id, "version": 1, "is_current": True,
                                     "short_content": title + ". " + title, "short_content_evidence": [],
                                     "relevant_topics": [{"text": w, "evidence": []} for w in title.split()[:3]],
                                     "grounding_score": 1.0, "unverified_claims": [], "source_links": [],
                                     "method": "single", "prompt_version": "p", "llm_call_ids": [], "created_at": t})
                        for k, u in enumerate(rnd.sample(units, 2), 1):
                            sugg.append({"regulation_id": reg_id, "unit_code": u, "rank": k, "score": 0.8,
                                         "reason": "r", "origin": "ai", "is_active": True, "is_final": False,
                                         "created_at": t})
                        for ev in ("detected", "viewed", "approved"):
                            audit.append({"regulation_id": reg_id, "event_type": ev, "actor_type": "system",
                                          "actor_name": "M", "payload": {}, "created_at": t})
        # yabancı anahtar sırası: düzenleme → ham belge (regulation_id) → bağlantı/özet/öneri/olay
        for model, rows in ((FetchRun, runs), (Regulation, regs), (RawDocument, raws),
                            (RegulationSourceLink, links), (RegulationSummary, summ), (UnitSuggestion, sugg),
                            (AuditEvent, audit)):
            for i in range(0, len(rows), 5000):
                s.execute(insert(model), rows[i:i + 5000])
        if s.bind.dialect.name == "postgresql":   # açık id ile eklenen tablolarda diziyi ilerlet
            from sqlalchemy import text

            for table in ("fetch_run", "raw_document", "regulation"):
                s.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}))"))
        s.commit()
        counts.update(runs=len(runs), raw=len(raws), regs=len(regs))
    return counts


def measure(db: str, n: int) -> list[tuple[str, float, float]]:
    from fastapi.testclient import TestClient

    from app.api.main import create_app

    settings = Settings(_env_file=None, database_url=_url(db), db_auto_create=False, cors_origins="",
                        sources_file=BACKEND_DIR / "config" / "sources.yaml", portal_dir=BACKEND_DIR.parent)
    c = TestClient(create_app(settings))
    cases = {
        "liste (varsayılan)": "/api/v1/regulations",
        "liste + kaynak + önem": "/api/v1/regulations?source=BDDK&severity=Kritik",
        "liste + arama": "/api/v1/regulations?q=sukuk",
        "liste + birim": "/api/v1/regulations?unit=UNIT_07",
        "liste + metrik (yaklaşan)": "/api/v1/regulations?metric=upcoming",
        "liste 5. sayfa (50)": "/api/v1/regulations?page=5&page_size=50",
        "istatistik": "/api/v1/regulations/stats",
        "detay": "/api/v1/regulations/{id}",
        "kaynak sağlığı": "/api/v1/sources/health",
        "paralel çalışma raporu (90 gün)": "/api/v1/evaluation/report?from=2026-06-27&to=2026-09-25",
    }
    first_id = c.get("/api/v1/regulations").json()["items"][0]["id"]
    out = []
    for name, url in cases.items():
        url = url.replace("{id}", str(first_id))
        times = []
        for _ in range(n if "rapor" not in name else max(3, n // 10)):
            t0 = time.perf_counter()
            r = c.get(url)
            times.append((time.perf_counter() - t0) * 1000)
            assert r.status_code == 200, (url, r.status_code, r.text[:200])
        times.sort()
        out.append((name, statistics.median(times), times[int(0.95 * (len(times) - 1))]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/tmp/mevzuat_yuk.db", help="SQLite dosyası veya veritabanı URL'si (boş şema)")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--requests", type=int, default=30)
    args = ap.parse_args()
    if "://" not in args.db:
        Path(args.db).unlink(missing_ok=True)
    t0 = time.perf_counter()
    counts = generate(args.db, args.days)
    print(f"Veri üretildi ({time.perf_counter() - t0:.0f} sn): {counts}")
    print(f"{'uç nokta':<34} {'medyan ms':>10} {'p95 ms':>10}")
    for name, med, p95 in measure(args.db, args.requests):
        print(f"{name:<34} {med:>10.1f} {p95:>10.1f}")


if __name__ == "__main__":
    main()
