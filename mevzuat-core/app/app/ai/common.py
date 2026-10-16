"""YZ adımlarının ortak parçaları: prompt şablonları, taksonomi/kılavuz, düzenleme bağlamı, çalışma zamanı ayarları.

Prompt sürümü = şablon sürümü + taksonomi sürümü + kılavuz içeriğinin özeti. Taksonomi veya kılavuz değiştiğinde
sürüm değişir; böylece ``llm_call`` önbelleği eski kriterlerle üretilmiş sonucu yeniden kullanmaz ve her çıktının hangi
kriterlerle üretildiği izlenebilir.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import AppSetting, RawDocument, Regulation, RegulationSourceLink
from app.settings import Settings

log = logging.getLogger(__name__)
PROMPTS_DIR = Path(__file__).parent / "prompts"
REG_TYPES = ["Kanun", "Cumhurbaşkanı Kararı", "Cumhurbaşkanlığı Kararnamesi", "Yönetmelik", "Tebliğ", "Genelge",
             "Rehber", "Kurul Kararı", "İlke Kararı", "Usul ve Esaslar", "Düzenleme Taslağı", "Duyuru",
             "Basın Duyurusu", "Bülten", "İlan", "Resmi Yazı", "Diğer"]
SEVERITIES = ["Kritik", "Yüksek", "Orta", "Düşük"]
_SOURCE_TAG = re.compile(r"<(\s*/?\s*kaynak_metin\b[^<>]*)>", re.I)


def neutralize_source_tags(value):
    """Belge metnindeki ``<kaynak_metin>`` / ``</kaynak_metin>`` etiketleri ``[…]`` biçimine çevrilir: metin
    istemdeki sınırı kapatıp ardından gelen satırları talimat gibi gösteremez (prompt injection)."""
    return _SOURCE_TAG.sub(r"[\1]", value) if isinstance(value, str) else value


_env = Environment(loader=FileSystemLoader(PROMPTS_DIR), undefined=StrictUndefined, keep_trailing_newline=False,
                   trim_blocks=False, autoescape=False)
# tojson varsayılan olarak Türkçe karakterleri \u011f biçiminde kaçırır; model alıntıları bu kaçış dizileriyle kopyalayıp
# bozuyor (canlı ölçüm: map-reduce'ta "Tebli\u001fin" gibi alıntılar). Prompt'a düz UTF-8 JSON verilir.
_env.policies["json.dumps_kwargs"] = {"ensure_ascii": False, "sort_keys": False}
# Kullanıcı mesajı şablonları belge verisi taşır: her {{ … }} çıktısında etiketler etkisizleştirilir. Şablondaki gerçek
# etiketler şablon metnidir, ifade değildir; etkilenmez. Sistem istemine uygulanmaz: güvenilir kılavuz etiketi adıyla
# anar ("Kaynak metin `<kaynak_metin>` etiketleri arasındadır").
_user_env = _env.overlay(finalize=neutralize_source_tags)


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
    env = _user_env if name.endswith(".user.j2") else _env
    return env.get_template(name).render(**ctx).strip()


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

@dataclass(frozen=True)
class RuntimeSpec:
    type: type
    min: float
    max: float
    description: str


# Çalışma zamanında değiştirilebilen ayarlar: tip ve aralık. Değer Settings alanının tipine çevrilerek okunur
# (``effective_settings``); bu yüzden int alan "3.0" ile kaydedilirse sonraki her okuma kırılırdı.
RUNTIME_SPECS: dict[str, RuntimeSpec] = {
    "relevance_threshold": RuntimeSpec(float, 0, 1, "Bu skorun altındaki kayıtlar portala düşmez (IRRELEVANT)"),
    "relevance_weight_model": RuntimeSpec(float, 0, 1, "İlgililik skorunda modelin kendi skorunun ağırlığı"),
    "relevance_weight_votes": RuntimeSpec(float, 0, 1, "İlgililik skorunda örnek oylamasının ağırlığı"),
    "relevance_weight_rules": RuntimeSpec(float, 0, 1, "İlgililik skorunda kural sinyallerinin ağırlığı"),
    "confidence_high": RuntimeSpec(float, 0, 1, "Bu skor ve üstü 'Yüksek' güven bandı"),
    "confidence_medium": RuntimeSpec(float, 0, 1, "Bu skor ve üstü 'Orta' güven bandı (altı 'Düşük')"),
    "relevance_samples": RuntimeSpec(int, 1, 9, "İlgililikte self-consistency örnek sayısı"),
    "dedupe_auto_threshold": RuntimeSpec(float, 0, 1, "Bu skor ve üstü otomatik birleştirilir"),
    "dedupe_review_threshold": RuntimeSpec(float, 0, 1, "Bu skor ile otomatik eşik arası LLM/insan onayına gider"),
}
RUNTIME_KEYS = set(RUNTIME_SPECS)


def parse_runtime_value(key: str, value: str):
    """Değeri ayarın tipine çevirir ve aralığı denetler; geçersizse ``ValueError``. ``"3.0"`` int alan için geçersizdir."""
    spec = RUNTIME_SPECS[key]
    try:
        parsed = spec.type(str(value).strip())
    except (TypeError, ValueError) as e:
        kind = "tam sayı" if spec.type is int else "sayı"
        raise ValueError(f"{key}: {kind} olmalı (verilen: {value!r})") from e
    if not spec.min <= parsed <= spec.max:    # NaN karşılaştırması da yanlış döner ve reddedilir
        raise ValueError(f"{key}: {spec.min:g} ile {spec.max:g} arasında olmalı (verilen: {value!r})")
    return parsed


def effective_settings(session: Session, settings: Settings) -> Settings:
    """``app_setting`` tablosundaki çalışma zamanı değerleriyle ezilmiş ayarlar.

    Bozuk bir satır (elle yazılmış, eski sürümden kalmış) atlanır ve uyarı yazılır: tüm YZ hattını düşürmemeli.
    """
    rows = session.scalars(select(AppSetting).where(AppSetting.key.in_(RUNTIME_KEYS))).all()
    update = {}
    for row in rows:
        try:
            update[row.key] = parse_runtime_value(row.key, row.value)
        except ValueError as e:
            log.warning("app_setting %s yok sayıldı: %s", row.key, e)
    return settings.model_copy(update=update) if update else settings


def check_runtime_settings(session: Session, settings: Settings) -> None:
    """Birbirine bağlı ayarların tutarlılığı (tek tek aralık denetimi yetmez); ihlalde ``ValueError``."""
    eff = effective_settings(session, settings)
    if not eff.dedupe_review_threshold < eff.dedupe_auto_threshold:
        raise ValueError("dedupe_review_threshold, dedupe_auto_threshold'tan küçük olmalı "
                         f"({eff.dedupe_review_threshold:g} / {eff.dedupe_auto_threshold:g})")
    if not eff.confidence_medium < eff.confidence_high:
        raise ValueError("confidence_medium, confidence_high'dan küçük olmalı "
                         f"({eff.confidence_medium:g} / {eff.confidence_high:g})")


def set_runtime_setting(session: Session, key: str, value: str, by: str | None = None) -> None:
    if key not in RUNTIME_KEYS:
        raise KeyError(f"değiştirilebilir ayarlar: {', '.join(sorted(RUNTIME_KEYS))}")
    value = str(parse_runtime_value(key, value))    # doğrulanmış ve kanonik biçim ("0.50" → "0.5", int → "3")
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value, updated_by=by))
    else:
        row.value, row.updated_by = value, by
