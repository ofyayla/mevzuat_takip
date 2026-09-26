"""Kaynak sağlığı — İK-7 izleme servisine yönlendirme (geriye uyumluluk için)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.monitoring.service import health_summary
from app.settings import Settings


def sources_health(session: Session, settings: Settings, now: datetime | None = None) -> dict:
    return health_summary(session, settings, now)
