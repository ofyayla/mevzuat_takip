"""Kaynak sağlığı (portal durum çubuğu ve uyarı şeridi). İK-7 bu hesabı iş günü takvimi, hacim anomalisi ve yapı
imzası değişikliğiyle genişletecek; burada temel kurallar var:

- down:    son tarama başarısız (tüm kanallar hata) ya da hiç başarılı tarama yok
- delayed: son başarılı tarama ``silence_tolerance_hours``'tan eski, ya da son taramada "sessiz bozulma" şüphesi
           (200 dönen ama beklenen öğeyi içermeyen liste — structure_alert) veya kanal hatası (partial)
- ok:      diğer durumlar
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collectors.config import load_sources
from app.db import FetchRun, RawDocument
from app.settings import Settings


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def _ago(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "hiç"
    mins = int((now - dt).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 1)} dakika önce"
    if mins < 48 * 60:
        return f"{mins // 60} saat önce"
    return f"{mins // 1440} gün önce"


def sources_health(session: Session, settings: Settings, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = []
    for src in load_sources(settings.sources_file).enabled():
        last = session.scalar(select(FetchRun).where(FetchRun.source_code == src.code, FetchRun.status != "running")
                              .order_by(FetchRun.started_at.desc()).limit(1))
        last_ok = _aware(session.scalar(select(func.max(FetchRun.started_at)).where(
            FetchRun.source_code == src.code, FetchRun.status.in_(("success", "partial")))))
        last_item = _aware(session.scalar(select(func.max(RawDocument.fetched_at)).where(
            RawDocument.source_code == src.code)))
        tol_h = src.expected.silence_tolerance_hours
        status, message = "ok", None
        if last is None or last_ok is None:
            status, message = "down", "Henüz başarılı tarama yok"
        elif last.status == "failed":
            status, message = "down", f"Son tarama başarısız: {'; '.join(last.errors or [])[:200]}"
        elif (now - last_ok).total_seconds() > tol_h * 3600:
            status, message = "delayed", f"{_ago(last_ok, now)} başarılı tarama yapıldı (tolerans {tol_h} saat)"
        elif last.structure_alert:
            status, message = "delayed", "Sayfa açıldı ama beklenen içerik gelmedi (yapı değişmiş olabilir)"
        elif last.status == "partial":
            status, message = "delayed", f"Bazı kanallar hatalı: {'; '.join(last.errors or [])[:200]}"
        out.append({"code": src.code, "name": src.name, "status": status, "lastSuccessAt": _iso(last_ok),
                    "lastItemAt": _iso(last_item), "lastFetch": _ago(last_ok, now), "message": message})
    down = [s for s in out if s["status"] == "down"]
    delayed = [s for s in out if s["status"] == "delayed"]
    overall = "down" if down else "delayed" if delayed else "healthy"
    parts = [f"{s['name']} {s['lastFetch'].replace(' önce', '')} önce içerik getirmedi (gecikmeli)" for s in delayed]
    parts += [f"{s['name']} yanıt vermiyor ({s['message']})" for s in down]
    return {"sources": out, "overall": overall, "bannerText": " · ".join(parts)}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
