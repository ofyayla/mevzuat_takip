"""Kaynaklar arası çapraz kontrol (İK-7): bir kaynağın adaptörü bozulduğunda diğer kaynak onu ele verir.

- RG → kurum: RG'de izlenen bir kurum adına yayımlanmış düzenleme, ``cross_check_hours`` (48 saat) sonra hâlâ o kurumun
  kendi kaynağında görülmediyse alarm.
- kurum → RG: kurum belgesi "… tarih ve N sayılı Resmî Gazete'de yayımlanmıştır" diyorsa (İK-2 ``rg_date``) ama RG
  kopyası 48 saat sonra hâlâ yoksa alarm.

Alarmlar ``warning`` düzeyindedir: kaynağın durumunu değiştirmez, yönetim ekranında ve logda görünür.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import RawDocument, Regulation, RegulationSourceLink
from app.monitoring.checks import Finding
from app.settings import Settings

TYPES = ("Yönetmelik", "Tebliğ", "Kurul Kararı", "Genelge", "İlke Kararı", "Usul ve Esaslar")


def _sources_of(session: Session, reg_id: int) -> set[str]:
    return set(session.scalars(select(RawDocument.source_code).join(
        RegulationSourceLink, RegulationSourceLink.raw_document_id == RawDocument.id
    ).where(RegulationSourceLink.regulation_id == reg_id)))


def cross_check(session: Session, settings: Settings, now: datetime) -> list[Finding]:
    issuers = {c.strip() for c in settings.cross_check_issuers.split(",") if c.strip()}
    cutoff = (now - timedelta(hours=settings.cross_check_hours)).date()
    oldest = (now - timedelta(days=settings.cross_check_lookback_days)).date()
    regs = session.scalars(select(Regulation).where(
        Regulation.issuer.in_(issuers), Regulation.processing_status.not_in(("MERGED", "BASELINE")),
        Regulation.publish_date.between(oldest, cutoff))).all()
    out = []
    for reg in regs:
        if (reg.ai_reg_type or reg.reg_type) not in TYPES:
            continue
        sources = _sources_of(session, reg.id)
        rg_date = (reg.extra or {}).get("rg_date")
        if "RESMI_GAZETE" in sources and reg.issuer not in sources:
            out.append(Finding(
                f"cross_check_miss:{reg.issuer}:reg:{reg.id}", "cross_check_miss", "warning",
                f"RG'de {reg.publish_date:%d.%m.%Y} tarihinde yayımlanan \"{reg.title[:90]}\" {reg.issuer} kaynağında "
                f"{settings.cross_check_hours} saattir görülmedi", reg.issuer,
                {"regulation_id": reg.id, "direction": "rg_to_source"}))
        elif reg.issuer in sources and "RESMI_GAZETE" not in sources and rg_date \
                and date.fromisoformat(rg_date) <= cutoff:
            out.append(Finding(
                f"cross_check_miss:RESMI_GAZETE:reg:{reg.id}", "cross_check_miss", "warning",
                f"{reg.issuer} belgesi {rg_date} tarihli RG'de yayımlandığını belirtiyor, RG kaynağında görülmedi: "
                f"\"{reg.title[:90]}\"", "RESMI_GAZETE", {"regulation_id": reg.id, "direction": "source_to_rg",
                                                          "rg_issue": (reg.extra or {}).get("rg_issue")}))
    return out


def ai_pipeline(session: Session) -> list[Finding]:
    stuck = session.scalars(select(Regulation).where(Regulation.processing_status == "AI_FAILED")).all()
    exhausted = [r for r in stuck if (r.extra or {}).get("ai_attempts", 0) >= 3]
    if not exhausted:
        return []
    stages = sorted({(r.extra or {}).get("ai_stage", "?") for r in exhausted})
    return [Finding("ai_failure", "ai_failure", "warning",
                    f"{len(exhausted)} düzenleme YZ hattında deneme hakkını tüketti ({', '.join(stages)}); "
                    f"inceleme veya yeniden işleme gerekli", None,
                    {"regulation_ids": [r.id for r in exhausted][:50], "stages": stages})]
