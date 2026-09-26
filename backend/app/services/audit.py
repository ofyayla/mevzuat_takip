"""Denetim izi olayları ve portal gösterimi (etiket + ikon; plan §5.2)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import AuditEvent, Regulation

SYSTEM_ACTOR = "Mevzuat Takip Motoru"

EVENT_META = {
    "detected": ("YZ tarafından tespit edildi", "bot"),
    "viewed": ("İncelemeye alındı", "eye"),
    "approved": ("Onaylandı", "check-circle-2"),
    "rejected": ("Reddedildi", "x-circle"),
    "units_changed": ("Birim değiştirildi", "shuffle"),
    "summary_regenerated": ("Özet yeniden üretildi", "refresh-cw"),
    "reprocessed": ("Yeniden işlendi", "rotate-cw"),
}


@dataclass
class Actor:
    id: str
    name: str
    title: str | None
    roles: set[str]
    client_ip: str | None = None
    user_agent: str | None = None

    @property
    def label(self) -> str:
        return f"{self.name} · {self.title}" if self.title else self.name


def add_event(session: Session, reg_id: int, event_type: str, *, actor: Actor | None = None, note: str | None = None,
              payload: dict | None = None) -> AuditEvent:
    ev = AuditEvent(regulation_id=reg_id, event_type=event_type, actor_type="user" if actor else "system",
                    actor_id=actor.id if actor else None, actor_name=actor.name if actor else SYSTEM_ACTOR,
                    actor_title=actor.title if actor else None, payload=payload or {}, note=note,
                    client_ip=actor.client_ip if actor else None,
                    user_agent=(actor.user_agent or "")[:300] if actor else None)
    session.add(ev)
    return ev


def ensure_detected(session: Session, reg: Regulation) -> None:
    """İlgili bulunan düzenleme için bir kez "YZ tarafından tespit edildi" olayı."""
    exists = session.scalar(select(AuditEvent.id).where(AuditEvent.regulation_id == reg.id,
                                                        AuditEvent.event_type == "detected").limit(1))
    if exists is None:
        add_event(session, reg.id, "detected", payload={
            "relevance_score": reg.relevance_score, "confidence": reg.confidence_band, "severity": reg.severity})


def trail(session: Session, reg: Regulation) -> list[dict]:
    rows = session.scalars(select(AuditEvent).where(AuditEvent.regulation_id == reg.id)
                           .order_by(AuditEvent.created_at, AuditEvent.id)).all()
    out = []
    if not any(r.event_type == "detected" for r in rows) and reg.classified_at:
        # denetim izinden önce sınıflandırılmış kayıtlar için (kalıcı değil, gösterim)
        out.append({"action": EVENT_META["detected"][0], "actor": SYSTEM_ACTOR, "timestamp": _iso(reg.classified_at),
                    "icon": "bot", "note": None, "eventType": "detected"})
    for r in rows:
        label, icon = EVENT_META.get(r.event_type, (r.event_type, "circle"))
        actor = f"{r.actor_name} · {r.actor_title}" if r.actor_title else r.actor_name
        out.append({"action": label, "actor": actor, "timestamp": _iso(r.created_at), "icon": icon, "note": r.note,
                    "eventType": r.event_type})
    return out


def _iso(dt) -> str | None:
    if dt is None:
        return None
    from datetime import timezone

    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()
