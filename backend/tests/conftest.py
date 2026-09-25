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
                    lock_dir=tmp_path / "locks", sources_file=BACKEND_DIR / "config" / "sources.yaml",
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
