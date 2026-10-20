"""Baseline sınırı (yeniden-baseline): sınır tarihinden ÖNCE yayımlananlar baseline kalır, sınır tarihi ve sonrası YZ
hattına girer. Yönetim sayfasından elle tetiklenir (``rebaseline`` işi).

Kaynak başına iki durum ele alınır:
- Zaten taranmış kayıtlar: ``publish_date >= sınır`` olan BASELINE düzenlemeler ``NEW`` yapılır ve YZ zincirine
  (classify → summarize → match) girer. ``raw_document.is_baseline`` değişmez: "ilk taramada toplandı" bilgisidir.
- Sınır tarihine kadar taranmamış kaynaklar: ``backfill(history_from=sınır)`` ile taranır; baseline kararı yayım tarihine
  göre verilir (sınırdan önceki BASELINE, sonrası yeni → mevcut zincirle YZ'ye gider). RG gün gün, sayfalayabilen
  kaynaklar (WordPress API, sayfalı JSON/CSS liste) sınıra kadar geriye okunur; tek sayfa/RSS listeleri sitenin
  gösterdiği kadarını verir ve önizlemede ``shallow`` olarak işaretlenir.

İşlem idempotenttir; yalnızca BASELINE → NEW yönünde çalışır (sınır ileri alınırsa işlenmiş kayıtlar geri çevrilmez).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.collectors.config import SourceConfig, load_sources
from app.db import AppSetting, AuditEvent, JobRun, RawDocument, Regulation
from app.settings import Settings

log = logging.getLogger(__name__)

SETTING_KEY = "baseline_cutoff"
RG = "RESMI_GAZETE"
RG_CHUNK_DAYS = 62   # tek tarama talebinin gün sayısı (Yönetim'deki "Geçmişi tara" sınırıyla aynı)


def get_cutoff(session: Session) -> date | None:
    row = session.get(AppSetting, SETTING_KEY)
    try:
        return date.fromisoformat(row.value) if row and row.value else None
    except ValueError:
        log.warning("app_setting %s geçersiz: %r", SETTING_KEY, row.value)
        return None


def set_cutoff(session: Session, cutoff: date, by: str | None = None) -> None:
    row = session.get(AppSetting, SETTING_KEY)
    if row is None:
        session.add(AppSetting(key=SETTING_KEY, value=cutoff.isoformat(), updated_by=by))
    else:
        row.value, row.updated_by = cutoff.isoformat(), by


def deep_capable(source: SourceConfig) -> bool:
    """Kaynak sınır tarihine kadar geriye okunabilir mi? (``depth_until`` destekleyen strateji/parametre)"""
    for ch in source.channels:
        if not ch.enabled:
            continue
        p = ch.params
        if ch.strategy in ("resmi_gazete", "wordpress_api"):
            return True
        if ch.strategy == "css_list" and p.get("pagination"):
            return True
        if ch.strategy == "json_api" and any("page=" in u for u in p.get("urls", [])):
            return True
    return False


def _to_convert(cutoff: date):
    return select(Regulation.id).where(Regulation.processing_status == "BASELINE", Regulation.publish_date >= cutoff)


def plan_rebaseline(session: Session, settings: Settings, cutoff: date, *, today: date | None = None) -> dict:
    """Önizleme: kaynak başına dönüştürülecek kayıt sayısı ve taranması gereken aralık (hiçbir şey değişmez)."""
    today = today or datetime.now(ZoneInfo(settings.timezone)).date()
    converted = dict(session.execute(
        select(Regulation.primary_source_code, func.count()).where(
            Regulation.processing_status == "BASELINE", Regulation.publish_date >= cutoff
        ).group_by(Regulation.primary_source_code)).all())
    earliest = dict(session.execute(
        select(RawDocument.source_code, func.min(RawDocument.published_at)).group_by(RawDocument.source_code)).all())
    rows = []
    for src in load_sources(settings.sources_file).enabled():
        first = earliest.get(src.code)
        deep = deep_capable(src)
        if first is not None and first <= cutoff:
            action = "covered"          # arşiv sınır tarihine kadar uzanıyor
        elif deep:
            action = "crawl"
        elif first is None:
            action = "crawl_shallow"    # hiç taranmamış: sitenin gösterdiği kadarı alınır
        else:
            action = "shallow"          # taranmış ama liste daha geriye gitmiyor: yalnızca mevcut kayıtlar dönüştürülür
        span = None
        if action == "crawl" and src.code == RG:
            end = min(today, first - timedelta(days=1)) if first else today
            span = {"from": cutoff.isoformat(), "to": end.isoformat()} if end >= cutoff else None
        rows.append({"source": src.code, "name": src.name, "action": action, "converted": converted.get(src.code, 0),
                     "earliest": first.isoformat() if first else None, "range": span})
    current = get_cutoff(session)
    return {"cutoff": cutoff.isoformat(), "current": current.isoformat() if current else None,
            "converted": sum(r["converted"] for r in rows), "sources": rows,
            "crawl": [r["source"] for r in rows if r["action"] in ("crawl", "crawl_shallow")]}


def _rg_chunks(span: dict) -> list[tuple[date, date]]:
    d_from, d_to = date.fromisoformat(span["from"]), date.fromisoformat(span["to"])
    out = []
    while d_from <= d_to:
        end = min(d_from + timedelta(days=RG_CHUNK_DAYS - 1), d_to)
        out.append((d_from, end))
        d_from = end + timedelta(days=1)
    return out



def crawl_key(cutoff: date, code: str, d_from: date, d_to: date) -> str:
    """Bir tarama parçasının kimliği (tekrar çalıştırmada aynı taramayı ikinci kez açmamak için). RG parçası aralığıyla,
    diğer kaynaklar yalnızca sınırla tanımlanır: bitiş tarihi "bugün" olduğundan ertesi gün değişir."""
    return f"rebaseline:{cutoff}:{code}:{d_from}" + (f":{d_to}" if code == RG else "")


def _save_progress(sf, job_id: int | None, result: dict) -> None:
    """İlerlemeyi ``job_run.result``'a yazar: iş yarıda ölürse neyin bittiği (ve devam eden işin neleri atlayacağı) görünür."""
    if job_id is None:
        return
    from app.db import JobRun
    from app.services.jobs import _jsonable

    with sf() as s:
        job = s.get(JobRun, job_id)
        if job is not None:
            job.result = _jsonable(result)
            s.commit()


def _done_crawls(sf, cutoff: date) -> set[str]:
    """Aynı sınırla daha önce (kesilmiş olanlar dahil) tamamlanmış yerel taramaların anahtarları."""
    from app.db import JobRun

    keys: set[str] = set()
    with sf() as s:
        for job in s.scalars(select(JobRun).where(JobRun.kind == "rebaseline").order_by(JobRun.id.desc()).limit(50)):
            if (job.params or {}).get("cutoff") != cutoff.isoformat():
                continue
            for e in (job.result or {}).get("crawl") or []:
                if e.get("status") == "done" and e.get("key"):
                    keys.add(e["key"])
    return keys


def _pending_ai_ids(session: Session, cutoff: date) -> dict[str, list[int]]:
    """Bu sınırla YZ hattına alınmış ve henüz bitmemiş kayıtlar, bekledikleri aşamaya göre. Kesilmiş bir işin devamında
    önceki koşuda NEW yapılanlar da zincire girer (yalnızca o koşuda dönüştürülenlere bakılsa sahipsiz kalırlardı)."""
    mark = cutoff.isoformat()
    out: dict[str, list[int]] = {"NEW": [], "RELEVANT": [], "SUMMARIZED": []}
    q = select(Regulation).where(Regulation.processing_status.in_(tuple(out)), Regulation.publish_date >= cutoff)
    for reg in session.scalars(q.order_by(Regulation.id)):
        if (reg.extra or {}).get("rebaselined_at") == mark:
            out[reg.processing_status].append(reg.id)
    return out


def apply_rebaseline(sf: sessionmaker[Session], settings: Settings, cutoff: date, *, llm, actor=None,
                     job_id: int | None = None) -> dict:
    """Sınırı kaydeder, ``publish_date >= sınır`` BASELINE kayıtları YZ hattına sokar ve taranmamış kaynakları tarar.

    Uzak modda tarama DMZ crawler'a talep olarak bırakılır (sonuç ingest ile gelir, YZ'ye mevcut zincirle girer); yerel
    modda bu işin içinde sırayla taranır. LLM kapalıysa kayıtlar ``NEW`` kalır ve zamanlanmış classify işi alır.

    Süreç ölürse kayıp yoktur ve işin yeniden çalıştırılması güvenlidir (``resume_interrupted`` bunu otomatik yapar):
    dönüşüm yalnızca BASELINE kayıtlara dokunur, tarama talepleri ``dedupe_key`` ile tekilleşir, tamamlanmış yerel taramalar
    atlanır ve YZ zinciri önceki koşuda dönüştürülmüş bekleyenleri de alır. ``job_id`` verilirse ilerleme ``job_run.result``'a
    adım adım yazılır."""
    from app.monitoring.reprocess import run_ai_chain
    from app.services.audit import add_event

    if llm is None:
        raise ValueError("bu işlem için LLM gerekli (LLM_PROVIDER)")
    with sf() as s:
        plan = plan_rebaseline(s, settings, cutoff)
        set_cutoff(s, cutoff, by=getattr(actor, "name", None))
        ids = list(s.scalars(_to_convert(cutoff).order_by(Regulation.id)))
        for reg in s.scalars(select(Regulation).where(Regulation.id.in_(ids))):
            reg.processing_status = "NEW"
            reg.extra = {**(reg.extra or {}), "ai_attempts": 0, "rebaselined_at": cutoff.isoformat()}
        s.commit()
    result: dict = {"cutoff": cutoff.isoformat(), "phase": "crawl", "converted": len(ids), "crawl": []}
    log.info("baseline sınırı %s: %d kayıt NEW yapıldı; taranacak kaynaklar: %s", cutoff, len(ids), plan["crawl"] or "-")
    _save_progress(sf, job_id, result)
    # Tarama talepleri YZ zincirinden ÖNCE açılır: zincir yüzlerce kayıtta saatler sürebilir, taramalar beklememeli
    def on_entry(entry: dict) -> None:
        result["crawl"].append(entry)
        _save_progress(sf, job_id, result)

    _start_crawls(sf, settings, cutoff, plan, actor, on_entry)
    with sf() as s:
        pending = _pending_ai_ids(s, cutoff)
    todo = sum(len(v) for v in pending.values())
    log.info("baseline sınırı: %d tarama talebi/çalıştırması açıldı; YZ zinciri başlıyor (%d kayıt: %s)",
             len(result["crawl"]), todo, {k: len(v) for k, v in pending.items()})
    result.update(phase="ai", ai_pending=todo)
    _save_progress(sf, job_id, result)
    all_ids = [i for v in pending.values() for i in v]
    result["llm_unavailable"] = (run_ai_chain(sf, llm, settings, classify_ids=pending["NEW"],
                                              summarize_ids=pending["RELEVANT"], match_ids=pending["SUMMARIZED"])
                                 if todo else False)
    with sf() as s:
        regs = list(s.scalars(select(Regulation).where(Regulation.id.in_(all_ids)))) if all_ids else []
        have_event = {e.regulation_id for e in s.scalars(select(AuditEvent).where(
            AuditEvent.regulation_id.in_(all_ids), AuditEvent.event_type == "reprocessed",
            AuditEvent.note.like(f"Baseline sınırı {cutoff}%")))} if all_ids else set()
        for reg in regs:
            if reg.is_relevant and reg.id not in have_event:
                add_event(s, reg.id, "reprocessed", actor=actor, note=f"Baseline sınırı {cutoff}: YZ hattına alındı")
        s.commit()
        by_status: dict[str, int] = {}
        for reg in regs:
            by_status[reg.processing_status] = by_status.get(reg.processing_status, 0) + 1
    result.update(phase="done", by_status=by_status)
    result["shallow"] = [r["source"] for r in plan["sources"] if r["action"] in ("shallow", "crawl_shallow")]
    _save_progress(sf, job_id, result)
    return result


def _start_crawls(sf, settings: Settings, cutoff: date, plan: dict, actor, on_entry=None) -> list[dict]:
    sources = [r for r in plan["sources"] if r["action"] in ("crawl", "crawl_shallow")]
    done = _done_crawls(sf, cutoff) if settings.crawl_mode != "remote" else set()   # uzak modda tekilleştirme Mongo'da
    out: list[dict] = []
    for row in sources:
        code = row["source"]
        if code == RG:
            chunks = _rg_chunks(row["range"]) if row["range"] else []
        else:
            chunks = [(cutoff, datetime.now(ZoneInfo(settings.timezone)).date())]
        for d_from, d_to in chunks:
            key = crawl_key(cutoff, code, d_from, d_to)
            if key in done:
                entry = {"source": code, "from": d_from.isoformat(), "to": d_to.isoformat(), "status": "done",
                         "skipped": "önceki koşuda tamamlandı"}
            else:
                entry = _crawl(sf, settings, code, d_from, d_to, cutoff, actor)
            entry["key"] = key
            out.append(entry)
            if on_entry:
                on_entry(entry)
    return out


def _crawl(sf, settings: Settings, code: str, d_from: date, d_to: date, cutoff: date, actor) -> dict:
    entry: dict = {"source": code, "from": d_from.isoformat(), "to": d_to.isoformat()}
    log.info("baseline sınırı taraması: %s %s – %s (%s mod)", code, d_from, d_to, settings.crawl_mode)
    try:
        if settings.crawl_mode == "remote":   # LAN internete çıkamaz: DMZ crawler'a talep
            from app.mongo import crawl_db, submit_crawl_request_once

            entry["requestId"], created = submit_crawl_request_once(
                crawl_db(settings), "backfill", code,
                {"date_from": d_from.isoformat(), "date_to": d_to.isoformat(), "history_from": cutoff.isoformat()},
                crawl_key(cutoff, code, d_from, d_to), requested_by=getattr(actor, "id", None) or "rebaseline")
            entry["status"] = "queued"
            if not created:
                entry["skipped"] = "aynı talep zaten var"
        else:
            from app.collectors.locks import LockBusy, source_lock
            from app.monitoring.reprocess import backfill, local_store

            try:
                with source_lock(code, redis_url=settings.redis_url, lock_dir=settings.lock_dir):
                    rep = backfill(local_store(sf, settings), settings, code, d_from, d_to, history_from=cutoff)
                entry.update(status="done", new=rep.new, days=rep.days)
            except LockBusy:
                entry["status"] = "skipped"
                entry["error"] = "başka bir tarama sürüyor"
    except Exception as e:  # noqa: BLE001 — bir kaynağın hatası diğerlerini durdurmasın
        log.exception("baseline sınırı taraması başarısız: %s", code)
        entry.update(status="failed", error=f"{type(e).__name__}: {e}")
    return entry


MAX_AUTO_RESUMES = 5   # art arda kesilen iş zinciri bu kadar kez otomatik sürdürülür (sonsuz döngü olmasın)


def resume_interrupted(sf: sessionmaker[Session], settings: Settings, *, llm) -> dict:
    """Son ``rebaseline`` işi ``interrupted`` ise (süreç ölmüş: dağıtım, OOM, worker yeniden başlaması) aynı sınırla yeni bir
    iş açıp sürdürür. Çalışan/kuyruktaki iş varsa dokunmaz. Beat'ten periyodik çağrılır (``tasks.ai.resume_rebaseline``)."""
    from app.services.jobs import create_job, sweep_stale, track_job

    with sf() as s:
        sweep_stale(s, settings)   # ölü işi önce "interrupted" yap: çağıran bunu beklemek zorunda kalmasın
        s.commit()
        jobs = list(s.scalars(select(JobRun).where(JobRun.kind == "rebaseline").order_by(JobRun.id.desc()).limit(MAX_AUTO_RESUMES + 1)))
        if not jobs or jobs[0].status != "interrupted":
            return {"status": "nothing_to_resume"}
        streak = 0
        for j in jobs:
            if j.status != "interrupted":
                break
            streak += 1
        if streak > MAX_AUTO_RESUMES:
            return {"status": "gave_up", "reason": f"{MAX_AUTO_RESUMES} kez art arda kesildi; Yönetim'den elle başlatın"}
        last = jobs[0]
        cutoff = date.fromisoformat(last.params["cutoff"])
        jid = create_job(s, "rebaseline", requested_by="otomatik devam",
                         params={**last.params, "resumedFrom": last.id})
    log.warning("rebaseline işi %s kesilmişti; %s sınırıyla %s olarak sürdürülüyor", last.id, cutoff, jid)
    with track_job(sf, jid) as h:
        h.result = apply_rebaseline(sf, settings, cutoff, llm=llm, job_id=jid)
    return {"status": "resumed", "jobId": jid, "resumedFrom": last.id}
