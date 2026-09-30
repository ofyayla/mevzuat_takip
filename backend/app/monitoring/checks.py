"""Kaynak sağlığı kontrolleri (İK-7, plan §6). Her kontrol bir ``Finding`` üretir; kaynağın durumu bulguların en
ağırından türetilir (down > delayed > ok). "Sessiz bozulma" — site açılıyor ama içerik gelmiyor — uyum açısından en
riskli durum olduğu için sessizlik, hacim ve yapı kontrolleri ayrı ayrı bakar.

| Kontrol          | Mantık                                                                                     | Durum   |
|------------------|--------------------------------------------------------------------------------------------|---------|
| fetch_error      | son N tarama başarısız (N=3) → down; yalnızca son tarama başarısız → delayed               |         |
| silence          | son yeni öğeden beri geçen süre > tolerans; iş günü takvimi (business_days_only).          | delayed |
|                  | Tolerans: sources.yaml, veri birikince (28 gün, ≥8 aralık) tarihsel aralıkların p95'i       |         |
| volume_low/high  | son 7 gün < geçmiş 8 haftanın 7 günlük ortalamasının %20'si / > ort + 3σ                   | delayed |
| structure_change | son taramada kanal yapı imzası değişti veya 200 dönen liste boş (structure_alert)          | delayed |

"Yeni öğe": ilk taramada sitede zaten duran içerik (BASELINE) sayılmaz; yalnızca sonradan görülen belgeler.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collectors.config import SourceConfig
from app.db import FetchRun, RawDocument
from app.monitoring.calendar import business_hours_between
from app.settings import Settings

RANK = {"ok": 0, "warning": 0, "delayed": 1, "down": 2}


@dataclass
class Finding:
    key: str
    alert_type: str
    severity: str             # down | delayed | warning
    message: str
    source_code: str | None = None
    details: dict = field(default_factory=dict)


@dataclass
class SourceStatus:
    code: str
    name: str
    status: str
    last_success_at: datetime | None
    last_item_at: datetime | None
    message: str | None
    findings: list[Finding]
    tolerance_hours: float | None = None
    tolerance_basis: str | None = None


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def ago(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "hiç"
    mins = int((now - dt).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 1)} dakika önce"
    if mins < 48 * 60:
        return f"{mins // 60} saat önce"
    return f"{mins // 1440} gün önce"


def _elapsed_hours(start: datetime, end: datetime, src: SourceConfig, settings: Settings) -> float:
    if src.expected.business_days_only:
        return business_hours_between(start, end, settings.timezone)
    return (end - start).total_seconds() / 3600


def new_item_times(session: Session, code: str, since: datetime | None = None) -> list[datetime]:
    """Yeni öğelerin görüldüğü anlar (tarama bazında; aynı taramadaki öğeler tek varış sayılır)."""
    q = select(func.min(RawDocument.fetched_at)).where(
        RawDocument.source_code == code, RawDocument.version == 1, RawDocument.role != "attachment",
        RawDocument.is_baseline.is_(False)).group_by(RawDocument.fetch_run_id)
    if since is not None:
        q = q.having(func.min(RawDocument.fetched_at) >= since)
    return sorted(_aware(t) for t in session.scalars(q) if t is not None)


def learned_tolerance(session: Session, src: SourceConfig, settings: Settings, now: datetime) -> tuple[float, str]:
    """Kaynağın yayın aralıklarının p95'i (yeterli veri varsa), yoksa sources.yaml toleransı."""
    configured = float(src.expected.silence_tolerance_hours)
    first_run = _aware(session.scalar(select(func.min(FetchRun.started_at)).where(FetchRun.source_code == src.code)))
    if first_run is None or (now - first_run).days < settings.silence_learn_min_days:
        return configured, "yapılandırma"
    times = new_item_times(session, src.code, since=now - timedelta(days=90))
    gaps = [_elapsed_hours(a, b, src, settings) for a, b in zip(times, times[1:])]
    gaps = [g for g in gaps if g > 0]
    if len(gaps) < settings.silence_learn_min_gaps:
        return configured, "yapılandırma"
    p95 = statistics.quantiles(gaps, n=20, method="inclusive")[18]
    return max(p95, 12.0), f"geçmiş p95 ({len(gaps)} aralık)"


def check_source(session: Session, src: SourceConfig, settings: Settings, now: datetime) -> SourceStatus:
    findings: list[Finding] = []
    runs = session.scalars(select(FetchRun).where(FetchRun.source_code == src.code, FetchRun.status != "running")
                           .order_by(FetchRun.started_at.desc()).limit(max(settings.monitor_failed_runs_down, 1))).all()
    last_ok = _aware(session.scalar(select(func.max(FetchRun.started_at)).where(
        FetchRun.source_code == src.code, FetchRun.status.in_(("success", "partial")))))
    items = new_item_times(session, src.code)
    last_item = items[-1] if items else None

    # 1) erişim hatası
    n = settings.monitor_failed_runs_down
    if not runs:
        findings.append(Finding(f"fetch_error:{src.code}", "fetch_error", "down", "Henüz tarama yapılmadı",
                                src.code))
    elif len(runs) >= n and all(r.status == "failed" for r in runs[:n]):
        findings.append(Finding(f"fetch_error:{src.code}", "fetch_error", "down",
                                f"Son {n} tarama başarısız: {'; '.join(runs[0].errors or [])[:200]}", src.code,
                                {"errors": runs[0].errors}))
    elif runs[0].status == "failed":
        findings.append(Finding(f"fetch_error:{src.code}", "fetch_error", "delayed",
                                f"Son tarama başarısız: {'; '.join(runs[0].errors or [])[:200]}", src.code,
                                {"errors": runs[0].errors}))

    # 2) sessizlik — başvuru noktası: son yeni öğe; hiç yoksa izlemenin başladığı ilk başarılı tarama
    tol, basis = learned_tolerance(session, src, settings, now)
    ref = last_item
    if ref is None and runs:
        ref = _aware(session.scalar(select(func.min(FetchRun.started_at)).where(
            FetchRun.source_code == src.code, FetchRun.status.in_(("success", "partial")))))
    if ref is not None:
        elapsed = _elapsed_hours(ref, now, src, settings)
        if elapsed > tol:
            findings.append(Finding(
                f"silence:{src.code}", "silence", "delayed",
                f"{ago(ref, now)} yeni içerik gelmedi (tolerans {tol:.0f} {'iş ' if src.expected.business_days_only else ''}saati, {basis})",
                src.code, {"elapsed_hours": round(elapsed, 1), "tolerance_hours": round(tol, 1), "basis": basis}))

    # 3) hacim anomalisi
    findings.extend(_volume(session, src, settings, now))

    # 4) yapı değişikliği / sessiz bozulma
    if runs and runs[0].status != "failed":
        for name, ch in (runs[0].channels or {}).items():
            if ch.get("error"):
                continue
            if ch.get("empty_listing"):
                findings.append(Finding(f"structure_change:{src.code}:{name}", "structure_change", "delayed",
                                        f"'{name}' kanalı açıldı ama beklenen içerik gelmedi (yapı değişmiş olabilir)",
                                        src.code, {"channel": name, "items": ch.get("items")}))
            elif ch.get("structure_changed"):
                findings.append(Finding(f"structure_change:{src.code}:{name}", "structure_change", "warning",
                                        f"'{name}' kanalının sayfa yapısı değişti (erken uyarı)", src.code,
                                        {"channel": name, "signature": ch.get("signature")}))

    worst = max((f.severity for f in findings), key=lambda s: RANK[s], default="ok")
    status = worst if worst in ("down", "delayed") else "ok"
    msg = next((f.message for f in sorted(findings, key=lambda f: -RANK[f.severity])), None)
    return SourceStatus(src.code, src.name, status, last_ok, last_item, msg, findings, tol, basis)


def _volume(session: Session, src: SourceConfig, settings: Settings, now: datetime) -> list[Finding]:
    weeks = settings.volume_history_weeks
    first_run = _aware(session.scalar(select(func.min(FetchRun.started_at)).where(FetchRun.source_code == src.code)))
    if first_run is None or (now - first_run).days < 7 * min(weeks, 4) + 7:
        return []   # en az 4 hafta geçmiş + son hafta gerekli
    counts = []
    for w in range(weeks + 1):
        end, start = now - timedelta(days=7 * w), now - timedelta(days=7 * (w + 1))
        if start < first_run:
            break
        counts.append(session.scalar(select(func.count()).select_from(RawDocument).where(
            RawDocument.source_code == src.code, RawDocument.version == 1, RawDocument.role != "attachment",
            RawDocument.is_baseline.is_(False), RawDocument.fetched_at >= start, RawDocument.fetched_at < end)))
    current, history = counts[0], counts[1:]
    if len(history) < 4:
        return []
    mean = statistics.mean(history)
    sd = statistics.pstdev(history)
    details = {"last_7_days": current, "history_mean": round(mean, 2), "history_sd": round(sd, 2),
               "history": history}
    if mean >= 1 and current < settings.volume_low_ratio * mean:
        return [Finding(f"volume_low:{src.code}", "volume_low", "delayed",
                        f"Son 7 günde {current} yeni içerik; olağan ortalama {mean:.1f}", src.code, details)]
    if sd > 0 and current > mean + settings.volume_high_sigma * sd:
        return [Finding(f"volume_high:{src.code}", "volume_high", "warning",
                        f"Son 7 günde olağandışı yüksek hacim: {current} (ortalama {mean:.1f})", src.code, details)]
    return []
