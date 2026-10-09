from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.collectors.config import SourceConfig, load_sources
from app.collectors.http import Fetcher, FetchError, Response
from app.settings import BACKEND_DIR, Settings

FIXTURES = Path(__file__).parent / "fixtures"
# Fixture'ların kaydedildiği an (canlı keşif, 25.09.2026 akşamı TR saati)
RECORDED_AT = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'test.db'}", raw_storage_dir=tmp_path / "raw",
                    lock_dir=tmp_path / "locks", crawler_heartbeat_file=tmp_path / "crawler.alive",
                    sources_file=BACKEND_DIR / "config" / "sources.yaml",
                    collect_request_delay_s=0)


@pytest.fixture(scope="session")
def sources():
    return load_sources(BACKEND_DIR / "config" / "sources.yaml")


class DictFetcher(Fetcher):
    """URL → (içerik, content-type[, status, final_url]) sözlüğünden yanıt veren sahte istemci."""

    def __init__(self, source: SourceConfig, settings: Settings, pages: dict):
        super().__init__(source, settings, sleep=lambda _: None)
        self.pages = pages
        self.requested: list[str] = []

    def _get_with_retry(self, url, headers):
        self.requested.append(url)
        if url not in self.pages:
            raise FetchError(url, "yok", 404)
        entry = self.pages[url]
        content, ctype = entry[0], entry[1]
        status = entry[2] if len(entry) > 2 else 200
        final = entry[3] if len(entry) > 3 else url
        self.request_count += 1
        body = content.encode("utf-8") if isinstance(content, str) else content
        return Response(url, final, status, {"content-type": ctype}, body)


# ------------------------------------------------------------------------------------------------ MongoDB (DMZ ↔ LAN)
# Gerçek MongoDB gerekir (GridFS, find_one_and_update, kısmi indeksler). ``MONGO_TEST_URL`` (varsayılan
# mongodb://localhost:27018) erişilemezse bu fixture'ları kullanan testler atlanır:
#   docker run -d --name mevzuat-mongo-test -p 27018:27017 mongo:7
MONGO_URL = __import__("os").environ.get("MONGO_TEST_URL", "mongodb://localhost:27018")


@pytest.fixture
def mdb():
    import uuid

    pymongo = pytest.importorskip("pymongo")
    from app.mongo import ensure_indexes

    client = pymongo.MongoClient(MONGO_URL, tz_aware=True, serverSelectionTimeoutMS=1500)
    try:
        client.admin.command("ping")
    except pymongo.errors.PyMongoError:
        pytest.skip(f"MongoDB erişilemiyor: {MONGO_URL}")
    name = f"mevzuat_test_{uuid.uuid4().hex[:8]}"
    db = client[name]
    ensure_indexes(db)
    yield db
    client.drop_database(name)
    client.close()


@pytest.fixture
def remote(settings, mdb):
    from app.db import make_sessionmaker

    s = settings.model_copy(update={"crawl_mode": "remote", "raw_storage": "gridfs", "mongo_url": MONGO_URL,
                                    "mongo_db": mdb.name})
    return s, make_sessionmaker(s.database_url)

