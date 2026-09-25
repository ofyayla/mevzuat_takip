"""Bir kaynağın uçtan uca taranması (İK-1): liste → yeni/değişen tespiti → detay + ekler → ham arşiv → kayıt.

Kurallar:
- Her kanal ayrı ele alınır; bir kanaldaki hata diğerlerini durdurmaz (fetch_run.status = partial).
- Liste satırı (başlık/tarih/sürüm anahtarı) değişmemişse detay yeniden indirilmez.
- Detay indirildiğinde içerik hash'i son sürümle aynıysa yeni sürüm açılmaz; farklıysa sürüm artar.
- Ham içerik asla silinmez; eski sürümler ``is_latest=False`` olarak kalır.
- ``max_details`` sınırı aşılırsa kalan öğeler bir sonraki çalıştırmaya kalır (ilk çalıştırmadaki birikim).
- Bir kanalın ilk taramasında sitede zaten duran içerik ``processing_status=BASELINE`` ile arşivlenir; bunlar
  "yeni düzenleme" sayılmaz ve YZ hattına gönderilmez. Kanal, ertelenen öğe kalmadan bir taramayı tamamlayana
  kadar bu kural geçerlidir (``baseline_complete`` bayrağı fetch_run.channels içinde taşınır). Geriye dönük işleme gerekiyorsa BASELINE kayıtlar ayrıca kuyruğa alınabilir.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.collectors.base import ChannelContext, ChannelResult, FetchedDocument, ItemRef
from app.collectors.config import ChannelConfig, SourceConfig
from app.collectors.http import Fetcher, FetchError
from app.collectors.resolvers import resolve
from app.collectors.strategies import get_strategy
from app.collectors.strategies._html import absolutize, parse_html
from app.db import FetchRun, RawDocument, Source, latest_documents
from app.settings import Settings
from app.storage import FileSystemStorage

log = logging.getLogger(__name__)


@dataclass
class ChannelReport:
    name: str
    items: int = 0
    pages: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    deferred: int = 0
    refreshed: int = 0
    signature: str = ""
    structure_changed: bool = False
    empty_listing: bool = False
    baseline: bool = False
    baseline_complete: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class RunReport:
    source: str
    run_id: int | None
    status: str
    channels: list[ChannelReport]
    requests: int

    @property
    def new(self) -> int:
        return sum(c.new for c in self.channels)

    @property
    def changed(self) -> int:
        return sum(c.changed for c in self.channels)


def list_channel(source: SourceConfig, channel: ChannelConfig, fetcher: Fetcher, *,
                 now: datetime | None = None, since: datetime | None = None) -> ChannelResult:
    ctx = ChannelContext(source=source, channel=channel, fetcher=fetcher, now=now or datetime.now(timezone.utc),
                         since=since)
    result = get_strategy(channel.strategy).list_items(ctx)
    result.items = _normalize_external(result.items)
    return result


def _normalize_external(items: list[ItemRef]) -> list[ItemRef]:
    """Bilinen dış belge sitelerine giden bağlantılara kanonik kimlik/adres verir; aynı belgeyi tekilleştirir."""
    out: list[ItemRef] = []
    seen: set[str] = set()
    for ref in items:
        if (res := resolve(ref.url)) is not None:
            ref.external_id, ref.url = res.canonical_id, res.public_url
            ref.extra = {**res.meta, **ref.extra}
            if ref.external_id in seen:
                continue
            seen.add(ref.external_id)
        out.append(ref)
    return out


def fetch_documents(source: SourceConfig, channel: ChannelConfig, ref: ItemRef, fetcher: Fetcher,
                    max_attachments: int) -> list[FetchedDocument]:
    now = datetime.now(timezone.utc)
    if not channel.fetch_detail or (ref.inline_content is None and not source.host_allowed(ref.url)):
        # Dış siteye giden bağlantı (ör. KVKK → Resmî Gazete PDF'i): yalnızca liste satırı saklanır
        snapshot = {"title": ref.title, "url": ref.url, "published_at": str(ref.published_at or ""),
                    "category": ref.category, "summary": ref.summary, "extra": ref.extra}
        return [FetchedDocument(ref.url, ref.url, json.dumps(snapshot, ensure_ascii=False).encode(),
                                "application/json", now, role="listing", title=ref.title)]
    if ref.inline_content is not None:
        main = FetchedDocument(ref.url, ref.url, ref.inline_content, ref.inline_content_type or "text/html", now,
                               title=ref.title)
    else:
        res = resolve(ref.url)
        resp = fetcher.get(res.content_url if res else ref.url)
        main = FetchedDocument(ref.url, resp.final_url, resp.content, resp.content_type, now, title=ref.title)
    docs = [main]
    if channel.attachment_pattern and main.content_type == "text/html":
        rx = re.compile(channel.attachment_pattern)
        html_text = main.content.decode("utf-8", errors="replace")
        seen: set[str] = set()
        for a in parse_html(html_text).css("a[href]"):
            url = absolutize(main.final_url, a.attributes["href"])
            if not rx.search(url) or url in seen or url == main.final_url:
                continue
            seen.add(url)
            if len(seen) > max_attachments:
                break
            if not source.host_allowed(url):
                log.info("%s: ek atlandı (izinli olmayan host): %s", source.code, url)
                continue
            try:
                r = fetcher.get(url)
                docs.append(FetchedDocument(url, r.final_url, r.content, r.content_type, now, role="attachment",
                                            title=a.text(strip=True) or None))
            except FetchError as e:
                log.warning("%s: ek indirilemedi: %s", source.code, e)
    return docs


class SourceCollector:
    def __init__(self, source: SourceConfig, settings: Settings, session_factory: sessionmaker[Session],
                 storage: FileSystemStorage, fetcher: Fetcher | None = None):
        self.source = source
        self.settings = settings
        self.session_factory = session_factory
        self.storage = storage
        self.fetcher = fetcher or Fetcher(source, settings)

    def run(self, *, channels: list[str] | None = None, max_details: int = 200,
            now: datetime | None = None) -> RunReport:
        now = now or datetime.now(timezone.utc)
        with self.session_factory() as session:
            self._upsert_source(session)
            since = self._last_success(session)
            prev_channels = self._previous_channel_stats(session)
            run = FetchRun(source_code=self.source.code, started_at=now)
            session.add(run)
            session.commit()

            reports: list[ChannelReport] = []
            budget = max_details
            for channel in self.source.channels:
                if not channel.enabled or (channels and channel.name not in channels):
                    continue
                rep = ChannelReport(channel.name)
                reports.append(rep)
                prev = prev_channels.get(channel.name) or {}
                rep.baseline = not prev.get("baseline_complete", False)
                rep.baseline_complete = prev.get("baseline_complete", False)
                try:
                    result = list_channel(self.source, channel, self.fetcher, now=now, since=since)
                except Exception as e:  # noqa: BLE001 — kanal hatası çalıştırmayı durdurmamalı
                    log.exception("%s/%s liste hatası", self.source.code, channel.name)
                    rep.error = f"{type(e).__name__}: {e}"
                    continue
                rep.items, rep.pages, rep.signature = len(result.items), result.pages_fetched, result.signature
                rep.warnings.extend(result.warnings)
                rep.empty_listing = rep.items < self.source.expected.min_items_per_run
                rep.structure_changed = bool(prev.get("signature") and rep.signature
                                             and prev["signature"] != rep.signature)
                budget = self._store_items(session, run, channel, result.items, rep, budget, rep.baseline, now)
                # Mevcut içeriğin tamamı arşive alındığında (ertelenen kalmadıysa) kanal normal moda geçer
                rep.baseline_complete = rep.baseline_complete or rep.deferred == 0

            run.finished_at = datetime.now(timezone.utc)
            run.items_listed = sum(r.items for r in reports)
            run.items_new = sum(r.new for r in reports)
            run.items_changed = sum(r.changed for r in reports)
            run.requests = self.fetcher.request_count
            run.channels = {r.name: {k: v for k, v in r.__dict__.items() if k != "name"} for r in reports}
            run.errors = [f"{r.name}: {r.error}" for r in reports if r.error]
            run.structure_alert = any(r.empty_listing and not r.error for r in reports)
            failed = [r for r in reports if r.error]
            run.status = "failed" if reports and len(failed) == len(reports) else "partial" if failed else "success"
            session.commit()
            return RunReport(self.source.code, run.id, run.status, reports, self.fetcher.request_count)

    # ------------------------------------------------------------------ yardımcılar

    def _store_items(self, session: Session, run: FetchRun, channel: ChannelConfig, items: list[ItemRef],
                     rep: ChannelReport, budget: int, baseline: bool = False,
                     now: datetime | None = None) -> int:
        existing = latest_documents(session, self.source.code, [i.external_id for i in items])
        refresh_budget = channel.refresh_max_per_run if channel.refresh_after_days else 0
        now = now or datetime.now(timezone.utc)
        for ref in items:
            prev = existing.get(ref.external_id)
            listing_hash = ref.listing_hash()
            refreshing = False
            if prev is not None and prev.listing_hash == listing_hash:
                if refresh_budget <= 0 or budget <= 0 or not self._refresh_due(prev, channel, now):
                    rep.unchanged += 1
                    continue
                refresh_budget -= 1
                refreshing = True
                rep.refreshed += 1
            if budget <= 0:
                rep.deferred += 1
                continue
            budget -= 1
            try:
                docs = fetch_documents(self.source, channel, ref, self.fetcher, self.settings.collect_max_attachments)
            except FetchError as e:
                rep.warnings.append(f"{ref.url}: {e}")
                continue
            main = docs[0]
            key, sha = self.storage.put(main.content)
            if prev is not None and prev.content_sha256 == sha:
                prev.listing_hash, prev.version_key, prev.title = listing_hash, ref.version_key, ref.title
                prev.extra = {**(prev.extra or {}), "checked_at": now.isoformat()}  # JSON alanı yeniden atanmalı
                rep.unchanged += 1
                session.commit()
                continue
            if refreshing:
                log.info("%s: içerik yerinde güncellenmiş: %s", self.source.code, ref.url)
            version = 1
            if prev is not None:
                prev.is_latest = False
                version = prev.version + 1
                rep.changed += 1
            else:
                rep.new += 1
            parent = self._add_doc(session, run, channel, ref, main, key, sha, version, listing_hash)
            if baseline and prev is None:
                parent.processing_status = "BASELINE"
            session.flush()
            for att in docs[1:]:
                akey, asha = self.storage.put(att.content)
                att_ref = ItemRef(ref.source, ref.channel, f"{ref.external_id}#{asha[:12]}", att.url,
                                  att.title or ref.title, ref.published_at, ref.category)
                row = self._add_doc(session, run, channel, att_ref, att, akey, asha, version, None,
                                    parent_id=parent.id)
                row.processing_status = parent.processing_status   # BASELINE ana belgenin ekleri de BASELINE
            session.commit()
        return budget

    @staticmethod
    def _refresh_due(prev: RawDocument, channel: ChannelConfig, now: datetime) -> bool:
        last = prev.fetched_at
        if checked := (prev.extra or {}).get("checked_at"):
            last = datetime.fromisoformat(checked)
        if last.tzinfo is None:  # SQLite tz bilgisini saklamaz
            last = last.replace(tzinfo=timezone.utc)
        return now - last >= timedelta(days=channel.refresh_after_days or 0)

    def _add_doc(self, session, run, channel, ref, doc: FetchedDocument, key, sha, version, listing_hash,
                 parent_id=None) -> RawDocument:
        row = RawDocument(
            source_code=self.source.code, channel=channel.name, external_id=ref.external_id, version=version,
            is_latest=True, parent_id=parent_id, role=doc.role, url=doc.url, final_url=doc.final_url,
            title=ref.title, published_at=ref.published_at, category=ref.category, listing_hash=listing_hash,
            version_key=ref.version_key, content_type=doc.content_type, content_sha256=sha,
            size_bytes=len(doc.content), storage_key=key, fetch_run_id=run.id, fetched_at=doc.fetched_at,
            extra={**ref.extra, **({"summary": ref.summary} if ref.summary else {})},
        )
        session.add(row)
        return row

    def _upsert_source(self, session: Session) -> None:
        row = session.get(Source, self.source.code)
        cfg = self.source.model_dump(mode="json")
        if row is None:
            session.add(Source(code=self.source.code, name=self.source.name, enabled=self.source.enabled, config=cfg))
        else:
            row.name, row.enabled, row.config = self.source.name, self.source.enabled, cfg
        session.commit()

    def _last_success(self, session: Session) -> datetime | None:
        started = session.scalar(select(FetchRun.started_at).where(
            FetchRun.source_code == self.source.code, FetchRun.status == "success"
        ).order_by(FetchRun.started_at.desc()).limit(1))
        if started is not None and started.tzinfo is None:  # SQLite tz bilgisini saklamaz
            started = started.replace(tzinfo=timezone.utc)
        return started

    def _previous_channel_stats(self, session: Session) -> dict:
        """Her kanal için en son kaydedilen istatistik (yalnızca bazı kanalların tarandığı çalıştırmalar olabilir)."""
        merged: dict = {}
        rows = session.scalars(select(FetchRun.channels).where(
            FetchRun.source_code == self.source.code, FetchRun.status.in_(("success", "partial"))
        ).order_by(FetchRun.started_at.desc()).limit(50))
        for channels in rows:
            for name, stats in (channels or {}).items():
                merged.setdefault(name, stats)
        return merged
