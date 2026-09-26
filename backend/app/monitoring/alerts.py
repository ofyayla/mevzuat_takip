"""Alarm yaşam döngüsü (İK-7): bulgular → alert tablosu. Aynı ``key`` açıksa güncellenir; bulgu kaybolursa kapanır.

İletim (Faz 1): portal/yönetim ekranı + yapılandırılmış log. ``ALERT_WEBHOOK_URL`` tanımlıysa açılış ve kapanışlar
JSON olarak POST edilir (kurum izleme sistemi); webhook hatası izlemeyi durdurmaz.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

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


def sync_alerts(session: Session, findings: list[Finding], settings: Settings, now: datetime) -> SyncResult:
    res = SyncResult()
    open_by_key = {a.key: a for a in session.scalars(select(Alert).where(Alert.resolved_at.is_(None)))}
    current = {f.key: f for f in findings}
    for key, f in current.items():
        a = open_by_key.get(key)
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
