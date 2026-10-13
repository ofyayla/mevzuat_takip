"""Bir kaynağın uçtan uca taranması (İK-1): liste → yeni/değişen tespiti → detay + ekler → ham arşiv → kayıt.

Kurallar:
- Her kanal ayrı ele alınır; bir kanaldaki hata diğerlerini durdurmaz (fetch_run.status = partial).
- Liste satırı (başlık/tarih/sürüm anahtarı) değişmemişse detay yeniden indirilmez.
- Detay indirildiğinde içerik hash'i son sürümle aynıysa yeni sürüm açılmaz; farklıysa sürüm artar.
- Ham içerik asla silinmez; eski sürümler ``is_latest=False`` olarak kalır.
- ``max_details`` sınırı aşılırsa kalan öğeler bir sonraki çalıştırmaya kalır (ilk çalıştırmadaki birikim).
- Bir kanalın ilk taramasında sitede zaten duran içerik ``is_baseline=True`` ile arşivlenir; bunlar
  "yeni düzenleme" sayılmaz ve YZ hattına gönderilmez. Kanal, ertelenen öğe kalmadan bir taramayı tamamlayana
  kadar bu kural geçerlidir (``baseline_complete`` bayrağı fetch_run.channels içinde taşınır). Bu belgeler metin çıkarma ve tekilleştirmeden geçer
  (sonradan yayımlanan kopyalarının eşleşebilmesi için) ama oluşan düzenleme ``BASELINE`` olur ve YZ'ye gitmez.
- Baseline şu durumlarda kapanmaz: ertelenen öğe var, geçici hatayla indirilemeyen öğe var (kalıcı 404/410 gibi
  hatalar engellemez), liste eksik/şüpheli biçimde boş (``expected.min_items_per_run``; ``baseline_empty_runs``
  ardışık koşudan sonra kabul edilir) ya da koşu durdurulmuş. Geçici hata ``baseline_max_runs`` koşudan uzun
  süremez; aksi halde sonradan gelen gerçek düzenlemeler sessizce BASELINE sayılırdı.
- Beklenmeyen hatalar taramayı kesmez: öğe hatası uyarıya, kanal hatası ``rep.error``'a dönüşür ve ``finish_run``
  her koşulda çağrılır (``fetch_runs`` kaydı ``running`` kalmaz).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.collectors.base import ChannelContext, ChannelResult, FetchedDocument, ItemRef
from app.collectors.config import ChannelConfig, SourceConfig
from app.collectors.http import Fetcher, FetchError, TransientFetchError
from app.collectors.resolvers import resolve
from app.collectors.store import CrawlStore, NewDoc, StoredDoc
from app.collectors.strategies import get_strategy
from app.collectors.strategies._html import absolutize, parse_html
from app.settings import Settings

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
    baseline_runs: int = 0          # baseline açıkken tamamlanan koşu sayısı (baseline_max_runs sınırı)
    empty_runs: int = 0             # ardışık "beklenenden az öğe" koşusu (baseline_empty_runs eşiği)
    failed: int = 0                 # geçici hatayla indirilemeyen öğe: baseline'ı kapatmaz, sonraki koşuda yeniden denenir
    gave_up: int = 0                # kalıcı hata (404/410 …): yalnızca uyarı, baseline'ı engellemez
    stopped: bool = False           # servis durdurulduğu için koşu yarıda bırakıldı
    incomplete: bool = False        # strateji listeyi eksik topladı (ör. bir indeks sayfası alınamadı)
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


def _is_permanent(e: FetchError) -> bool:
    """Tekrar denemenin sonucu değiştirmeyeceği hata (404/410, boyut, izin listesi); geçici hata baseline'ı tutar."""
    if isinstance(e, TransientFetchError):
        return False
    return e.status is None or (400 <= e.status < 500 and e.status not in (408, 429))


class SourceCollector:
    """``store``: kayıt katmanı (``SqlCrawlStore`` veya DMZ'de ``MongoCrawlStore``)."""

    def __init__(self, source: SourceConfig, settings: Settings, store: CrawlStore, fetcher: Fetcher | None = None,
                 should_stop: Callable[[], bool] | None = None):
        self.source = source
        self.settings = settings
        self.store = store
        self.fetcher = fetcher or Fetcher(source, settings)
        self.should_stop = should_stop or (lambda: False)   # servis kapanırken tarama temiz biçimde bırakılır

    def run(self, *, channels: list[str] | None = None, max_details: int = 200,
            now: datetime | None = None) -> RunReport:
        now = now or datetime.now(timezone.utc)
        self.store.upsert_source(self.source)
        since = self.store.last_success(self.source.code)
        selected = [c for c in self.source.channels if c.enabled and (not channels or c.name in channels)]
        prev_channels = self.store.previous_channel_stats(self.source.code, [c.name for c in selected])
        run_id = self.store.start_run(self.source.code, now)

        reports: list[ChannelReport] = []
        completed = False
        try:
            self._run_channels(run_id, selected, max_details, now, since, prev_channels, reports)
            completed = True
        finally:   # istisna yükselse bile kayıt "running" kalmasın
            failed = [r for r in reports if r.error]
            degraded = any(r.stopped or r.incomplete or r.failed for r in reports)
            if not completed or (reports and len(failed) == len(reports)):
                status = "failed"
            else:
                status = "partial" if failed or degraded else "success"
            errors = [f"{r.name}: {r.error}" for r in failed]
            if not completed:
                errors.append("tarama beklenmeyen bir hatayla kesildi")
            elif any(r.stopped for r in reports):
                errors.append("servis durdurulduğu için tarama yarıda bırakıldı")
            self.store.finish_run(
                run_id, finished_at=datetime.now(timezone.utc), status=status,
                items_listed=sum(r.items for r in reports), items_new=sum(r.new for r in reports),
                items_changed=sum(r.changed for r in reports), requests=self.fetcher.request_count,
                channels={r.name: {k: v for k, v in r.__dict__.items() if k != "name"} for r in reports},
                errors=errors, structure_alert=any(r.empty_listing and not r.error for r in reports))
        return RunReport(self.source.code, run_id, status, reports, self.fetcher.request_count)

    def _run_channels(self, run_id, selected: list[ChannelConfig], max_details: int, now: datetime, since, prev_channels: dict,
                      reports: list[ChannelReport]) -> None:
        budget = max_details
        for channel in selected:
            rep = ChannelReport(channel.name)
            reports.append(rep)
            prev = prev_channels.get(channel.name) or {}
            rep.baseline = not prev.get("baseline_complete", False)
            rep.baseline_complete = prev.get("baseline_complete", False)
            rep.baseline_runs, rep.empty_runs = prev.get("baseline_runs", 0), prev.get("empty_runs", 0)
            if self.should_stop():
                rep.stopped = True
                break
            try:
                result = list_channel(self.source, channel, self.fetcher, now=now, since=since)
            except Exception as e:  # noqa: BLE001 — kanal hatası çalıştırmayı durdurmamalı
                log.exception("%s/%s liste hatası", self.source.code, channel.name)
                rep.error = f"{type(e).__name__}: {e}"
                continue
            rep.items, rep.pages, rep.signature = len(result.items), result.pages_fetched, result.signature
            rep.warnings.extend(result.warnings)
            rep.incomplete = result.incomplete
            rep.empty_listing = rep.items < self.source.expected.min_items_per_run
            rep.structure_changed = bool(prev.get("signature") and rep.signature
                                         and prev["signature"] != rep.signature)
            try:
                budget = self._store_items(run_id, channel, result.items, rep, budget, rep.baseline, now)
            except Exception as e:  # noqa: BLE001 — yazma/Mongo hatası diğer kanalları durdurmasın
                log.exception("%s/%s kayıt hatası", self.source.code, channel.name)
                rep.error = f"{type(e).__name__}: {e}"
                continue
            if rep.baseline and not rep.baseline_complete:
                self._update_baseline(rep)
            if rep.stopped:
                break

    def _update_baseline(self, rep: ChannelReport) -> None:
        """Mevcut içeriğin tamamı arşive alındığında kanal normal moda geçer (ayrıntı: modül docstring'i)."""
        rep.baseline_runs += 1
        rep.empty_runs = rep.empty_runs + 1 if rep.empty_listing else 0
        listing_ok = not rep.empty_listing or rep.empty_runs >= self.settings.baseline_empty_runs
        rep.baseline_complete = (rep.deferred == 0 and rep.failed == 0 and listing_ok
                                 and not rep.incomplete and not rep.stopped)
        if not rep.baseline_complete and rep.baseline_runs >= self.settings.baseline_max_runs and not rep.stopped:
            rep.baseline_complete = True
            rep.warnings.append(f"baseline {rep.baseline_runs} koşudur kapanamadı; zorla kapatıldı "
                                f"(ertelenen={rep.deferred}, hatalı={rep.failed}, eksik liste={rep.empty_listing})")

    # ------------------------------------------------------------------ yardımcılar

    def _store_items(self, run_id, channel: ChannelConfig, items: list[ItemRef], rep: ChannelReport, budget: int,
                     baseline: bool = False, now: datetime | None = None) -> int:
        existing = self.store.latest_documents(self.source.code, [i.external_id for i in items])
        refresh_budget = channel.refresh_max_per_run if channel.refresh_after_days else 0
        now = now or datetime.now(timezone.utc)
        for ref in items:
            if self.should_stop():
                rep.stopped = True
                break
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
                self._item_failed(rep, ref, e, permanent=_is_permanent(e))
                continue
            except Exception as e:  # noqa: BLE001 — tek öğenin ayrıştırma/ağ hatası kalan öğeleri durdurmasın
                log.exception("%s: %s işlenemedi", self.source.code, ref.url)
                self._item_failed(rep, ref, e, permanent=False)
                continue
            main = docs[0]
            key, sha = self.store.put_content(main.content)
            if prev is not None and prev.content_sha256 == sha:
                self.store.touch_unchanged(prev, listing_hash=listing_hash, version_key=ref.version_key,
                                           title=ref.title, checked_at=now)
                rep.unchanged += 1
                continue
            if refreshing:
                log.info("%s: içerik yerinde güncellenmiş: %s", self.source.code, ref.url)
            version = 1
            if prev is not None:
                version = prev.version + 1
                rep.changed += 1
            else:
                rep.new += 1
            is_baseline = baseline and prev is None
            parent = self._new_doc(channel, ref, main, key, sha, version, listing_hash, is_baseline)
            attachments, seen_att = [], set()
            for att in docs[1:]:
                akey, asha = self.store.put_content(att.content)
                if asha in seen_att:   # aynı bayt (farklı URL ya da aynı hata sayfası) tek ek: kimlik çakışması olmasın
                    continue
                seen_att.add(asha)
                att_ref = ItemRef(ref.source, ref.channel, f"{ref.external_id}#{asha[:12]}", att.url,
                                  att.title or ref.title, ref.published_at, ref.category)
                # eski içeriğin ekleri de eski içeriktir
                attachments.append(self._new_doc(channel, att_ref, att, akey, asha, version, None, is_baseline))
            self.store.add_version(prev, run_id, parent, attachments)
        return budget

    @staticmethod
    def _item_failed(rep: ChannelReport, ref: ItemRef, e: Exception, *, permanent: bool) -> None:
        detail = str(e) if isinstance(e, FetchError) else f"{type(e).__name__}: {e}"
        rep.warnings.append(f"{ref.url}: {detail}")
        if permanent:
            rep.gave_up += 1
        else:
            rep.failed += 1

    @staticmethod
    def _refresh_due(prev: StoredDoc, channel: ChannelConfig, now: datetime) -> bool:
        last = prev.fetched_at
        if checked := (prev.extra or {}).get("checked_at"):
            last = datetime.fromisoformat(checked)
        if last.tzinfo is None:  # SQLite tz bilgisini saklamaz
            last = last.replace(tzinfo=timezone.utc)
        return now - last >= timedelta(days=channel.refresh_after_days or 0)

    def _new_doc(self, channel, ref: ItemRef, doc: FetchedDocument, key, sha, version, listing_hash,
                 is_baseline: bool) -> NewDoc:
        return NewDoc(
            source_code=self.source.code, channel=channel.name, external_id=ref.external_id, version=version,
            role=doc.role, url=doc.url, final_url=doc.final_url, title=ref.title, published_at=ref.published_at,
            category=ref.category, listing_hash=listing_hash, version_key=ref.version_key,
            content_type=doc.content_type, content_sha256=sha, size_bytes=len(doc.content), storage_key=key,
            fetched_at=doc.fetched_at, is_baseline=is_baseline,
            extra={**ref.extra, **({"summary": ref.summary} if ref.summary else {})},
        )
