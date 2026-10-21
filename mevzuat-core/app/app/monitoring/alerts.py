"""Alarm yaşam döngüsü (İK-7): bulgular → alert tablosu. Aynı ``key`` açıksa güncellenir; bulgu kaybolursa kapanır.

İletim (Faz 1): portal/yönetim ekranı + yapılandırılmış log. ``ALERT_WEBHOOK_URL`` tanımlıysa açılış ve kapanışlar
JSON olarak POST edilir (kurum izleme sistemi); webhook hatası izlemeyi durdurmaz.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Alert
from app.monitoring.checks import Finding
from app.settings import Settings

log = logging.getLogger("mevzuat.alerts")


@dataclass
class SyncResult:
    opened: list[Alert] = field(default_factory=list)
    resolved: list[Alert] = field(default_factory=list)
    still_open: int = 0
    snoozed: int = 0


def sync_alerts(session: Session, findings: list[Finding], settings: Settings, now: datetime) -> SyncResult:
    res = SyncResult()
    open_by_key = {a.key: a for a in session.scalars(select(Alert).where(Alert.resolved_at.is_(None)))}
    current = {f.key: f for f in findings}
    for key, f in current.items():
        a = open_by_key.get(key)
        if a is None and _snoozed(session, key, settings, now):
            res.snoozed += 1                  # uzman elle kapattı; bulgu sürse de süre dolana kadar yeniden açılmaz
            continue
        if a is None:
            a = Alert(key=key, source_code=f.source_code, alert_type=f.alert_type, severity=f.severity,
                      message=f.message, details=f.details, opened_at=now, last_seen_at=now)
            session.add(a)
            res.opened.append(a)
        else:
            a.severity, a.message, a.details, a.last_seen_at = f.severity, f.message, f.details, now
            res.still_open += 1
    for key, a in open_by_key.items():
        if key not in current:
            a.resolved_at = now
            res.resolved.append(a)
    session.flush()
    for a in res.opened:
        _emit("alert.opened", a, settings)
    for a in res.resolved:
        _emit("alert.resolved", a, settings)
    return res


def _emit(event: str, a: Alert, settings: Settings) -> None:
    payload = {"event": event, "key": a.key, "type": a.alert_type, "severity": a.severity, "source": a.source_code,
               "message": a.message, "opened_at": a.opened_at.isoformat() if a.opened_at else None,
               "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None}
    (log.warning if event == "alert.opened" else log.info)(json.dumps(payload, ensure_ascii=False))
    if settings.alert_webhook_url:
        try:
            httpx.post(settings.alert_webhook_url, json=payload, timeout=10)
        except httpx.HTTPError as e:
            log.error("alarm webhook'u gönderilemedi: %s", e)


def _snoozed(session: Session, key: str, settings: Settings, now: datetime) -> bool:
    last = session.scalars(select(Alert).where(Alert.key == key, Alert.resolved_at.is_not(None))
                           .order_by(Alert.resolved_at.desc()).limit(1)).first()
    if last is None or not (last.details or {}).get("acknowledged_by"):
        return False
    at = last.resolved_at if last.resolved_at.tzinfo else last.resolved_at.replace(tzinfo=now.tzinfo)
    return now - at < timedelta(hours=settings.alert_snooze_hours)


ALERT_LINKS = {"fetch_error": "kaynaklar", "structure_change": "kaynaklar", "volume_low": "kaynaklar",
               "volume_high": "kaynaklar", "crawler_down": "kaynaklar", "ingest_lag": "kaynaklar",
               "crawl_request": "kaynaklar", "cross_check_miss": "kaynaklar"}


def alert_link(alert_type: str) -> str:
    """Portalda alarmın çözüleceği Yönetim sekmesi."""
    return ALERT_LINKS.get(alert_type, "genel")
