"""YZ adımlarının ortak parçaları: prompt şablonları, taksonomi/kılavuz, düzenleme bağlamı, çalışma zamanı ayarları.

Prompt sürümü = şablon sürümü + taksonomi sürümü + kılavuz içeriğinin özeti. Taksonomi veya kılavuz değiştiğinde
sürüm değişir; böylece ``llm_call`` önbelleği eski kriterlerle üretilmiş sonucu yeniden kullanmaz ve her çıktının hangi
kriterlerle üretildiği izlenebilir.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import AppSetting, RawDocument, Regulation, RegulationSourceLink
from app.settings import Settings

PROMPTS_DIR = Path(__file__).parent / "prompts"
REG_TYPES = ["Kanun", "Cumhurbaşkanı Kararı", "Cumhurbaşkanlığı Kararnamesi", "Yönetmelik", "Tebliğ", "Genelge",
             "Rehber", "Kurul Kararı", "İlke Kararı", "Usul ve Esaslar", "Düzenleme Taslağı", "Duyuru",
             "Basın Duyurusu", "Bülten", "İlan", "Resmi Yazı", "Diğer"]
SEVERITIES = ["Kritik", "Yüksek", "Orta", "Düşük"]
_env = Environment(loader=FileSystemLoader(PROMPTS_DIR), undefined=StrictUndefined, keep_trailing_newline=False,
                   trim_blocks=False, autoescape=False)
# tojson varsayılan olarak Türkçe karakterleri \u011f biçiminde kaçırır; model alıntıları bu kaçış dizileriyle kopyalayıp
# bozuyor (canlı ölçüm: map-reduce'ta "Tebli\u001fin" gibi alıntılar). Prompt'a düz UTF-8 JSON verilir.
_env.policies["json.dumps_kwargs"] = {"ensure_ascii": False, "sort_keys": False}


@dataclass(frozen=True)
class Knowledge:
    taxonomy: dict
    guide: str
    version: str           # taksonomi sürümü + kılavuz özeti


@lru_cache(maxsize=4)
def _load_knowledge(taxonomy_path: str, guide_path: str, mtimes: tuple) -> Knowledge:
    taxonomy = yaml.safe_load(Path(taxonomy_path).read_text("utf-8"))
    guide = Path(guide_path).read_text("utf-8")
    digest = hashlib.sha256((guide + yaml.safe_dump(taxonomy, allow_unicode=True)).encode()).hexdigest()[:8]
    return Knowledge(taxonomy, guide, f"{taxonomy.get('version', '?')}+{digest}")


def knowledge(settings: Settings) -> Knowledge:
    t, g = settings.taxonomy_file, settings.labeling_guide_file
    return _load_knowledge(str(t), str(g), (t.stat().st_mtime, g.stat().st_mtime))


def render(name: str, **ctx) -> str:
    return _env.get_template(name).render(**ctx).strip()


def messages(task: str, version: str, system_ctx: dict, user_ctx: dict) -> list[dict]:
    return [{"role": "system", "content": render(f"{task}.{version}.system.j2", **system_ctx)},
            {"role": "user", "content": render(f"{task}.{version}.user.j2", **user_ctx)}]


# ------------------------------------------------------------------------------------------------ bağlam


@dataclass
class RegulationContext:
    regulation: Regulation
    title: str
    issuer: str | None
    sources: list[str]
    publish_date: str | None
    reg_type_hint: str | None
    text: str
    has_text: bool
    alt_titles: list[str] = None  # bağlı diğer kaynaklardaki başlıklar (RG başlığı konuyu anlatmayabilir)

    @property
    def all_titles(self) -> str:
        return "\n".join([self.title, *(self.alt_titles or [])])


def regulation_context(session: Session, reg: Regulation, max_chars: int) -> RegulationContext:
    """Düzenlemenin bağlı belgelerinden LLM girdisi: en dolu ana metin (tercihen RG) + eklerin metni.

    BDDK duyurularında asıl metin ekteki PDF'tedir; ana belge yalnızca başlık içerir. Metin uzunsa baştan kesilir
    (amaç/kapsam maddeleri başta yer alır)."""
    docs = session.scalars(select(RawDocument).join(
        RegulationSourceLink, RegulationSourceLink.raw_document_id == RawDocument.id
    ).where(RegulationSourceLink.regulation_id == reg.id, RawDocument.is_latest.is_(True))
        .order_by(RawDocument.id)).all()
    mains = [d for d in docs if d.role != "attachment"]
    mains.sort(key=lambda d: (d.source_code == "RESMI_GAZETE" and len(d.text or "") > 300, len(d.text or "")),
               reverse=True)
    parts: list[str] = []
    if mains and (mains[0].text or "").strip():
        parts.append(mains[0].text)
    primary_id = mains[0].id if mains else None
    for att in (d for d in docs if d.role == "attachment" and (d.text or "").strip()):
        if primary_id is None or att.parent_id in {m.id for m in mains}:
            parts.append(f"[Ek: {att.title or 'belge'}]\n{att.text}")
    text = "\n\n".join(parts).replace("\f", "\n")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[… metin kısaltıldı]"
    from app.processing.normalize import clean_title, title_key

    seen, alt = {title_key(reg.title)}, []
    for d in mains:
        if d.title and title_key(d.title) not in seen and len(d.title) >= 10:
            seen.add(title_key(d.title))
            alt.append(clean_title(d.title))
    return RegulationContext(
        alt_titles=alt,
        regulation=reg, title=reg.title, issuer=reg.issuer_name or reg.issuer,
        sources=sorted({d.source_code for d in mains}), publish_date=str(reg.publish_date or "") or None,
        reg_type_hint=reg.reg_type, text=text or reg.title, has_text=len(text.strip()) >= 200)


# ------------------------------------------------------------------------------------------------ app_setting

RUNTIME_KEYS = {"relevance_threshold", "relevance_weight_model", "relevance_weight_votes", "relevance_weight_rules",
                "confidence_high", "confidence_medium", "relevance_samples", "dedupe_auto_threshold",
                "dedupe_review_threshold"}


def effective_settings(session: Session, settings: Settings) -> Settings:
    """``app_setting`` tablosundaki çalışma zamanı değerleriyle ezilmiş ayarlar."""
    rows = session.scalars(select(AppSetting).where(AppSetting.key.in_(RUNTIME_KEYS))).all()
    update = {}
    for row in rows:
        current = getattr(settings, row.key)
        update[row.key] = type(current)(row.value)
    return settings.model_copy(update=update) if update else settings


def set_runtime_setting(session: Session, key: str, value: str, by: str | None = None) -> None:
    if key not in RUNTIME_KEYS:
        raise KeyError(f"değiştirilebilir ayarlar: {', '.join(sorted(RUNTIME_KEYS))}")
    float(value)  # doğrulama
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value, updated_by=by))
    else:
        row.value, row.updated_by = value, by
