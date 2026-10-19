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
- ``history_from`` (Yönetim → Baseline sınırı, ``backfill``): doluysa baseline kararı kanalın ilk taramasına değil
  öğenin yayım tarihine bağlanır (tarihi sınırdan önceyse BASELINE, sınır ve sonrası yeni; tarihsiz öğe kanalın
  durumuna uyar) ve sayfalayabilen stratejiler listeyi sınır tarihine kadar geriye doğru okur.
- Beklenmeyen hatalar taramayı kesmez: öğe hatası uyarıya, kanal hatası ``rep.error``'a dönüşür ve ``finish_run``
  her koşulda çağrılır (``fetch_runs`` kaydı ``running`` kalmaz).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
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
    id_conflicts: int = 0           # external_id başka kanalın FARKLI belgesiyle çakıştı (id_pattern ayırt etmiyor): atlandı
    att_pending: int = 0            # indirilemeyen, sonraki koşularda yeniden denenecek ek (extra.missing_attachments)
    att_recovered: int = 0          # bu koşuda geç de olsa alınan ek
    att_gave_up: int = 0            # kalıcı hata ya da deneme sınırı nedeniyle bırakılan ek (onarım komutuyla açılır)
    att_over_limit: int = 0         # COLLECT_MAX_ATTACHMENTS aşıldığı için alınmayan ek (hata değil, bilgi; uyarıya yazılır)
    stopped: bool = False           # servis durdurulduğu için koşu yarıda bırakıldı
    incomplete: bool = False        # strateji listeyi eksik topladı (ör. bir indeks sayfası alınamadı)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class RepairReport:
    """``SourceCollector.repair_attachments`` sonucu."""

    source: str
    dry_run: bool = False
    docs_checked: int = 0
    docs_with_gaps: int = 0
    gaps: int = 0           # eksik ek sayısı (dry-run'da denenmez, yalnızca sayılır)
    recovered: int = 0      # bu çalıştırmada alınan ek
    pending: int = 0        # hâlâ alınamayan, sonraki koşularda yeniden denenecek
    gave_up: int = 0        # kalıcı hata / deneme sınırı / ek sınırı; ``reopen`` ile yeniden açılır
    blocked: int = 0        # devre kesici açık olduğu için denenmedi
    requests: int = 0


@dataclass
class RunReport:
    source: str
    run_id: int | None
    status: str
    channels: list[ChannelReport]
    requests: int
    errors: list[str] = field(default_factory=list)   # fetch_run.errors ile aynı: partial/failed nedenleri

    @property
    def new(self) -> int:
        return sum(c.new for c in self.channels)

    @property
    def changed(self) -> int:
        return sum(c.changed for c in self.channels)


def run_issues(reports: list[ChannelReport]) -> list[str]:
    """Koşunun ``partial``/``failed`` olmasına yol açan nedenler; ``fetch_run.errors`` ve log özetine bunlar girer."""
    out: list[str] = []
    for r in reports:
        if r.error:
            out.append(f"{r.name}: {r.error}")
        if r.failed:
            out.append(f"{r.name}: {r.failed} öğe geçici hatayla indirilemedi (sonraki koşuda yeniden denenir)")
        if r.gave_up:
            out.append(f"{r.name}: {r.gave_up} öğe kalıcı hatayla atlandı")
        if r.incomplete:
            out.append(f"{r.name}: liste eksik toplandı")
        if r.id_conflicts:
            out.append(f"{r.name}: {r.id_conflicts} öğenin kimliği başka kanalın belgesiyle çakışıyor, atlandı "
                       f"(kanallara id_prefix verin)")
        if r.att_pending:
            out.append(f"{r.name}: {r.att_pending} ek indirilemedi (yeniden denenecek)")
        if r.att_gave_up:
            out.append(f"{r.name}: {r.att_gave_up} ek kalıcı olarak alınamadı")
    return out


def list_channel(source: SourceConfig, channel: ChannelConfig, fetcher: Fetcher, *,
                 now: datetime | None = None, since: datetime | None = None,
                 known_ids: Callable[[list[str]], set[str]] | None = None,
                 depth_until: date | None = None) -> ChannelResult:
    ctx = ChannelContext(source=source, channel=channel, fetcher=fetcher, now=now or datetime.now(timezone.utc),
                         since=since, known_ids=known_ids, depth_until=depth_until)
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


OVER_LIMIT = "ek sınırı"       # missing_attachments.last_error öneki: sınır aşımı (yeniden denenmez, hata sayılmaz)
MAX_MISSING_ENTRIES = 50   # bir öğenin extra.missing_attachments listesi bundan uzun tutulmaz


def _attachment_backoff(attempts: int) -> timedelta:
    """1. başarısızlıktan sonra 1 sa, sonra 2, 4, 8, 16 sa; en çok 24 sa (firewall düzeltmesi günler sürebilir)."""
    return timedelta(hours=min(2 ** max(attempts - 1, 0), 24))


def _missing_entry(url: str, title: str | None, e: Exception, now: datetime, *, attempts: int = 1,
                   first_failed_at: str | None = None, max_attempts: int = 10, reason: str | None = None) -> dict:
    """İndirilemeyen ek kaydı. ``permanent``: yeniden denenmez (kalıcı hata, deneme sınırı, ek sınırı)."""
    permanent = reason is not None or (isinstance(e, FetchError) and _is_permanent(e)) or attempts >= max_attempts
    return {"url": url, "title": title, "attempts": attempts,
            "first_failed_at": first_failed_at or now.isoformat(),
            "next_try_at": None if permanent else (now + _attachment_backoff(attempts)).isoformat(),
            "last_error": (reason or str(e))[:300], "permanent": permanent}


def attachment_links(html_text: str, base_url: str, pattern: str) -> list[tuple[str, str | None]]:
    """Sayfadaki ek bağlantıları (mutlak adres, bağlantı metni); desene uyan, tekrarsız, sayfanın kendisi hariç."""
    rx = re.compile(pattern)
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for a in parse_html(html_text).css("a[href]"):
        url = absolutize(base_url, a.attributes["href"])
        if not rx.search(url) or url in seen or url == base_url:
            continue
        seen.add(url)
        out.append((url, a.text(strip=True) or None))
    return out


def fetch_documents(source: SourceConfig, channel: ChannelConfig, ref: ItemRef, fetcher: Fetcher,
                    max_attachments: int, missing: list[dict] | None = None,
                    max_attempts: int = 10) -> list[FetchedDocument]:
    """``missing`` verilirse indirilemeyen her ek buna kayıt olarak eklenir (yoksa yalnızca uyarı loglanır)."""
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
        html_text = main.content.decode("utf-8", errors="replace")
        for n, (url, title) in enumerate(attachment_links(html_text, main.final_url, channel.attachment_pattern), 1):
            if n > max_attachments:   # sessizce düşmesin: görünür ama yeniden denenmeyen kayıt
                if missing is not None and len(missing) < MAX_MISSING_ENTRIES:
                    missing.append(_missing_entry(url, title, FetchError(url, "ek sınırı aşıldı"), now,
                                                  reason=f"{OVER_LIMIT} ({max_attachments}) aşıldı"))
                continue
            if not source.host_allowed(url):
                log.info("%s: ek atlandı (izinli olmayan host): %s", source.code, url)
                continue
            try:
                r = fetcher.get(url)
                docs.append(FetchedDocument(url, r.final_url, r.content, r.content_type, now, role="attachment",
                                            title=title))
            except FetchError as e:
                log.warning("%s: ek indirilemedi: %s", source.code, e)
                if missing is not None and len(missing) < MAX_MISSING_ENTRIES:
                    missing.append(_missing_entry(url, title, e, now, max_attempts=max_attempts))
    return docs


def _baseline_item(channel_baseline: bool, published_at: date | None, history_from: date | None) -> bool:
    """Yeni öğe BASELINE mı? Sınır tarihi yoksa kanalın durumuna; varsa yayım tarihine bakılır (tarihsiz öğe kanala)."""
    if history_from is None or published_at is None:
        return channel_baseline
    return published_at < history_from


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
            now: datetime | None = None, history_from: date | None = None) -> RunReport:
        now = now or datetime.now(timezone.utc)
        self.store.upsert_source(self.source)
        since = self.store.last_success(self.source.code)
        selected = [c for c in self.source.channels if c.enabled and (not channels or c.name in channels)]
        prev_channels = self.store.previous_channel_stats(self.source.code, [c.name for c in selected])
        run_id = self.store.start_run(self.source.code, now)

        reports: list[ChannelReport] = []
        completed = False
        try:
            self._run_channels(run_id, selected, max_details, now, since, prev_channels, reports, history_from)
            completed = True
        finally:   # istisna yükselse bile kayıt "running" kalmasın
            failed = [r for r in reports if r.error]
            degraded = any(r.stopped or r.incomplete or r.failed or r.att_pending or r.id_conflicts for r in reports)
            if not completed or (reports and len(failed) == len(reports)):
                status = "failed"
            else:
                status = "partial" if failed or degraded else "success"
            errors = run_issues(reports)
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
        return RunReport(self.source.code, run_id, status, reports, self.fetcher.request_count, errors)

    def _run_channels(self, run_id, selected: list[ChannelConfig], max_details: int, now: datetime, since, prev_channels: dict,
                      reports: list[ChannelReport], history_from: date | None = None) -> None:
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
                # Telafi sayfalaması yalnız baseline kapandıktan sonra: ilk taramada tüm geçmişi çekmeyiz
                known = None if rep.baseline else (
                    lambda ids: set(self.store.latest_documents(self.source.code, ids)))
                result = list_channel(self.source, channel, self.fetcher, now=now, since=since, known_ids=known,
                                      depth_until=history_from)
            except Exception as e:  # noqa: BLE001 — kanal hatası çalıştırmayı durdurmamalı
                if isinstance(e, FetchError):   # ağ/HTTP hatası beklenen durumdur: traceback yerine tek satır
                    log.warning("%s/%s liste hatası: %s", self.source.code, channel.name, e)
                else:
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
                budget = self._store_items(run_id, channel, result.items, rep, budget, rep.baseline, now, history_from)
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
                     baseline: bool = False, now: datetime | None = None, history_from: date | None = None) -> int:
        existing = self.store.latest_documents(self.source.code, [i.external_id for i in items])
        refresh_budget = channel.refresh_max_per_run if channel.refresh_after_days else 0
        now = now or datetime.now(timezone.utc)
        retry_left = self.settings.collect_attachment_retry_items
        for ref in items:
            if self.should_stop():
                rep.stopped = True
                break
            prev = existing.get(ref.external_id)
            if prev is not None and prev.channel and prev.channel != channel.name and prev.url != ref.url:
                # Aynı kimlik, başka kanalda başka belge (ör. BDDK /Duyuru/Detay/951 ≠ /Mevzuat/DokumanGetir/951).
                # Geçmişi bu belgenin üstüne yazmak her koşuda sahte "değişen" sürüm üretir; bu yüzden atlanır.
                rep.id_conflicts += 1
                rep.warnings.append(f"{ref.external_id}: kimlik {prev.channel} kanalındaki {prev.url} ile çakışıyor: "
                                    f"{ref.url}")
                continue
            listing_hash = ref.listing_hash()
            refreshing = False
            if prev is not None and prev.listing_hash == listing_hash:
                if refresh_budget <= 0 or budget <= 0 or not self._refresh_due(prev, channel, now):
                    rep.unchanged += 1
                    if prev.extra.get("missing_attachments"):   # içerik aynı ama daha önce alınamayan ekler var
                        retry_left = self._retry_missing(channel, ref, prev, run_id, rep, now, retry_left)
                    continue
                refresh_budget -= 1
                refreshing = True
                rep.refreshed += 1
            if budget <= 0:
                rep.deferred += 1
                continue
            budget -= 1
            missing: list[dict] = []
            try:
                docs = fetch_documents(self.source, channel, ref, self.fetcher, self.settings.collect_max_attachments,
                                       missing, self.settings.collect_attachment_max_attempts)
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
            is_baseline = prev is None and _baseline_item(baseline, ref.published_at, history_from)
            over =[m for m in missing if m["last_error"].startswith(OVER_LIMIT)]
            rep.att_pending += sum(1 for m in missing if not m["permanent"])
            rep.att_gave_up += sum(1 for m in missing if m["permanent"]) - len(over)
            if over:   # hata değil: sayfada sınırdan çok ek var. Koşuyu/hata listesini kirletmez, uyarıya tek satır
                rep.att_over_limit += len(over)
                rep.warnings.append(f"{ref.url}: {len(over)} ek, ek sınırı ({self.settings.collect_max_attachments}) "
                                    f"aşıldığı için alınmadı")
            parent = self._new_doc(channel, ref, main, key, sha, version, listing_hash, is_baseline, missing)
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

    def _retry_missing(self, channel: ChannelConfig, ref: ItemRef, prev: StoredDoc, run_id, rep: ChannelReport,
                       now: datetime, retry_left: int) -> int:
        """İçeriği değişmeyen öğenin daha önce indirilemeyen eklerini yeniden dener (yalnız ekler; ana belge sabit).

        Zamanı gelmiş ekler denenir; devre kesicisi açık host'a istek atılmaz ve deneme sayılmaz. Başarılı ekler
        aynı sürüme eklenir, başarısızların sayacı/bekleme süresi güncellenir. ``retry_left``: koşu başına öğe sınırı.
        """
        entries = [dict(m) for m in prev.extra.get("missing_attachments") or []]
        rep.att_pending += sum(1 for m in entries if not m.get("permanent"))   # bekleyenler koşu durumunu bozuk gösterir
        due = [m for m in entries if not m.get("permanent")
               and (not m.get("next_try_at") or datetime.fromisoformat(m["next_try_at"]) <= now)]
        if not due or retry_left <= 0:
            return retry_left
        got: list[NewDoc] = []
        remaining: list[dict] = []
        max_attempts = self.settings.collect_attachment_max_attempts
        for m in entries:
            if m.get("permanent") or m not in due or self.fetcher.host_blocked(m["url"]):
                remaining.append(m)
                continue
            try:
                r = self.fetcher.get(m["url"])
            except Exception as e:  # noqa: BLE001 — tek ekin hatası kalan ekleri/öğeleri durdurmasın
                log.warning("%s: ek yeniden denemesi başarısız: %s", self.source.code, e)
                bumped = _missing_entry(m["url"], m.get("title"), e, now, attempts=m.get("attempts", 1) + 1,
                                        first_failed_at=m.get("first_failed_at"), max_attempts=max_attempts)
                if bumped["permanent"]:
                    rep.att_pending -= 1
                    rep.att_gave_up += 1
                remaining.append(bumped)
                continue
            akey, asha = self.store.put_content(r.content)
            att = FetchedDocument(m["url"], r.final_url, r.content, r.content_type, now, role="attachment",
                                  title=m.get("title"))
            att_ref = ItemRef(ref.source, ref.channel, f"{ref.external_id}#{asha[:12]}", att.url,
                              att.title or ref.title, ref.published_at, ref.category)
            got.append(self._new_doc(channel, att_ref, att, akey, asha, prev.version, None, prev.is_baseline))
            rep.att_pending -= 1
            rep.att_recovered += 1
        if got or remaining != entries:
            self.store.add_late_attachments(prev, run_id, got, remaining)
        return retry_left - 1

    def repair_attachments(self, *, channels: list[str] | None = None, dry_run: bool = False, reopen: bool = False,
                           max_attachments: int | None = None, limit: int | None = None,
                           now: datetime | None = None) -> RepairReport:
        """Arşivdeki ana belgelerin içeriğinden ek bağlantılarını yeniden çıkarır; arşivde olmayanları indirir.

        Geçmiş hasarı (``missing_attachments`` kaydı tutulmadan önce kaybolan ekler) ve engel kalktıktan sonra
        bekleme süresini beklemeden yeniden denemeyi kapsar. Yeni fetch_run açılmaz (``last_success`` kayması olmasın).
        ``dry_run``: yalnızca eksikleri sayar. ``reopen``: kalıcı/vazgeçilmiş kayıtların sayacını sıfırlayıp yeniden
        dener. ``limit``: eksiği olan en çok bu kadar belge işlenir.
        """
        now = now or datetime.now(timezone.utc)
        cap = max_attachments or self.settings.collect_max_attachments
        max_attempts = self.settings.collect_attachment_max_attempts
        cfgs = {c.name: c for c in self.source.channels if c.attachment_pattern and (not channels or c.name in channels)}
        rep = RepairReport(self.source.code, dry_run)
        if not cfgs:
            return rep
        for info in self.store.iter_main_documents(self.source.code, list(cfgs)):
            if self.should_stop() or (limit and rep.docs_with_gaps >= limit):
                break
            ch = cfgs.get(info.channel)
            if ch is None or info.content_type != "text/html":
                continue
            rep.docs_checked += 1
            html = self.store.read_content(info.storage_key).decode("utf-8", errors="replace")
            prior = list(info.doc.extra.get("missing_attachments") or [])
            known = {m["url"]: m for m in prior}
            entries: list[dict] = []
            got: list[NewDoc] = []
            seen_sha: set[str] = set()
            gaps = 0
            for n, (url, title) in enumerate(attachment_links(html, info.final_url or info.url, ch.attachment_pattern), 1):
                if url in info.attachment_urls or not self.source.host_allowed(url):
                    continue
                m = known.get(url)
                if n > cap:   # sınır hâlâ aşılıyor: kayıt kalır (daha yüksek --max-attachments ile denenir)
                    entries.append(m or _missing_entry(url, title, FetchError(url, "ek sınırı aşıldı"), now,
                                                       reason=f"{OVER_LIMIT} ({cap}) aşıldı"))
                    continue
                over_limit = bool(m and m.get("last_error", "").startswith(OVER_LIMIT))   # hiç denenmemiş kayıt
                if m and m.get("permanent") and not over_limit and not reopen:
                    entries.append(m)
                    rep.gave_up += 1
                    continue
                gaps += 1
                if dry_run:
                    rep.gaps += 1
                    continue
                if self.fetcher.host_blocked(url):
                    entries.append(m or _missing_entry(url, title, TransientFetchError(
                        url, "host bu koşuda erişilemez (devre kesici açık)"), now, max_attempts=max_attempts))
                    rep.blocked += 1
                    continue
                attempts = 0 if (reopen or not m or over_limit) else m.get("attempts", 0)
                try:
                    r = self.fetcher.get(url)
                except Exception as e:  # noqa: BLE001 — tek ekin hatası onarımı durdurmasın
                    log.warning("%s: ek onarılamadı: %s", self.source.code, e)
                    entry = _missing_entry(url, (m or {}).get("title") or title, e, now, attempts=attempts + 1,
                                           first_failed_at=(m or {}).get("first_failed_at"), max_attempts=max_attempts)
                    entries.append(entry)
                    rep.gave_up += entry["permanent"]
                    rep.pending += not entry["permanent"]
                    continue
                akey, asha = self.store.put_content(r.content)
                rep.recovered += 1
                if asha in seen_sha:   # aynı bayt (farklı adres): tek ek
                    continue
                seen_sha.add(asha)
                att = FetchedDocument(url, r.final_url, r.content, r.content_type, now, role="attachment", title=title)
                att_ref = ItemRef(self.source.code, info.channel, f"{info.doc.external_id}#{asha[:12]}", url,
                                  title or info.title, info.published_at, info.category)
                got.append(self._new_doc(ch, att_ref, att, akey, asha, info.doc.version, None, info.doc.is_baseline))
            rep.docs_with_gaps += bool(gaps)
            if not dry_run and (got or entries != prior):
                self.store.add_late_attachments(info.doc, None, got, entries[:MAX_MISSING_ENTRIES])
        rep.requests = self.fetcher.request_count
        return rep

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
                 is_baseline: bool, missing: list[dict] | None = None) -> NewDoc:
        return NewDoc(
            source_code=self.source.code, channel=channel.name, external_id=ref.external_id, version=version,
            role=doc.role, url=doc.url, final_url=doc.final_url, title=ref.title, published_at=ref.published_at,
            category=ref.category, listing_hash=listing_hash, version_key=ref.version_key,
            content_type=doc.content_type, content_sha256=sha, size_bytes=len(doc.content), storage_key=key,
            fetched_at=doc.fetched_at, is_baseline=is_baseline,
            extra={**ref.extra, **({"summary": ref.summary} if ref.summary else {}),
                   **({"missing_attachments": missing} if missing else {})},
        )
