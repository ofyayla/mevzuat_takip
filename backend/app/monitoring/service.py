"""İzleme servisi: tüm kontrolleri çalıştırır, portal sağlık özetini üretir ve (isteğe bağlı) alarmları senkronlar.

``monitor`` kuyruğunda 10 dakikada bir ``run_monitor`` çalışır. ``/sources/health`` aynı hesabı salt okunur yapar
(9 kaynak için birkaç sorgu; ayrı önbellek gerekmiyor).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.collectors.config import load_sources
from app.monitoring.alerts import SyncResult, sync_alerts
from app.monitoring.checks import Finding, SourceStatus, ago, check_source
from app.monitoring.cross_check import ai_pipeline, cross_check
from app.settings import Settings


@dataclass
class MonitorResult:
    statuses: list[SourceStatus]
    findings: list[Finding]
    sync: SyncResult | None = None


def evaluate(session: Session, settings: Settings, now: datetime | None = None) -> MonitorResult:
    now = now or datetime.now(timezone.utc)
    statuses = [check_source(session, src, settings, now) for src in load_sources(settings.sources_file).enabled()]
    findings = [f for s in statuses for f in s.findings]
    findings += cross_check(session, settings, now) + ai_pipeline(session)
    return MonitorResult(statuses, findings)


def run_monitor(session: Session, settings: Settings, now: datetime | None = None) -> MonitorResult:
    now = now or datetime.now(timezone.utc)
    res = evaluate(session, settings, now)
    res.sync = sync_alerts(session, res.findings, settings, now)
    return res


def health_summary(session: Session, settings: Settings, now: datetime | None = None) -> dict:
    """Portal durum çubuğu ve uyarı şeridi (asimetrik gösterim: sorun yoksa sade, varsa açıklayıcı)."""
    now = now or datetime.now(timezone.utc)
    res = evaluate(session, settings, now)
    sources = [{"code": s.code, "name": s.name, "status": s.status,
                "lastSuccessAt": s.last_success_at.isoformat() if s.last_success_at else None,
                "lastItemAt": s.last_item_at.isoformat() if s.last_item_at else None,
                "lastFetch": ago(s.last_success_at, now), "message": s.message,
                "toleranceHours": round(s.tolerance_hours, 1) if s.tolerance_hours else None,
                "toleranceBasis": s.tolerance_basis} for s in res.statuses]
    down = [s for s in sources if s["status"] == "down"]
    delayed = [s for s in sources if s["status"] == "delayed"]
    parts = [f"{s['name']}: {s['message']}" for s in down] + [f"{s['name']}: {s['message']}" for s in delayed]
    return {"sources": sources, "overall": "down" if down else "delayed" if delayed else "healthy",
            "bannerText": " · ".join(parts),
            "warnings": [{"type": f.alert_type, "source": f.source_code, "message": f.message}
                         for f in res.findings if f.severity == "warning"]}
