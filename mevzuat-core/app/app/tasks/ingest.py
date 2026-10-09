"""DMZ crawler çıktısını aktaran görev (CRAWL_MODE=remote). Beat her dakika tetikler; yeni belge gelen kaynaklar
için işleme hattı (``process_pending``) hemen başlatılır — yerel kurulumdaki ``collect_source`` zinciriyle aynı."""
from __future__ import annotations

from functools import lru_cache

from app.db import make_sessionmaker
from app.ingest import CrawlIngestor
from app.mongo import crawl_db
from app.settings import get_settings
from app.worker import app


@lru_cache
def _session_factory():
    return make_sessionmaker(get_settings().database_url)


@app.task(name="app.tasks.ingest.ingest_crawl")
def ingest_crawl(limit: int | None = None) -> dict:
    s = get_settings()
    rep = CrawlIngestor(s, _session_factory(), crawl_db(s)).run(limit=limit)
    if rep.new_by_source:
        from app.tasks.process import process_pending

        for code in rep.new_by_source:
            process_pending.delay(source=code)
    return rep.__dict__
