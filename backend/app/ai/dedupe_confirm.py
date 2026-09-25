"""İK-2 tekilleştirmesinin belirsiz bandı (0.70–0.90) için LLM onayı (thinking kapalı, basit görev)."""
from __future__ import annotations

import logging

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from app.ai.common import messages
from app.ai.llm_client import LLM, LLMError
from app.db import Regulation
from app.processing.dedupe import DocFacts, Linker

log = logging.getLogger(__name__)
PROMPT = "v1"


class DedupeOut(BaseModel):
    same: bool
    rationale: str


def make_confirmer(llm: LLM, session_factory: sessionmaker[Session], settings):
    """``DocumentProcessor(confirmer=...)`` için: True/False, LLM hatasında None (insan onayına kalır).

    Her onay kendi kısa oturumunda çalışır ve ``llm_call`` kaydı hemen yazılır (işleme hattının oturumuyla aynı anda
    açık yazma işlemi tutmamak için; SQLite'ta kilitlenme olur)."""

    def confirm(doc: DocFacts, reg: Regulation, score: float) -> bool | None:
        with session_factory() as session:
            return _confirm(session, doc, reg, score)

    def _confirm(session: Session, doc: DocFacts, reg: Regulation, score: float) -> bool | None:
        other_text = Linker(session, settings)._primary_text(reg.id)
        msgs = messages("dedupe_confirm", PROMPT, {}, {
            "a": {"title": doc.title.title, "issuer": doc.issuer_name or doc.issuer, "source": doc.source_code,
                  "date": doc.official_date, "text": doc.text[:2500]},
            "b": {"title": reg.title, "issuer": reg.issuer_name or reg.issuer, "source": reg.primary_source_code,
                  "date": reg.publish_date, "text": other_text[:2500]}})
        try:
            res = llm.complete_json("dedupe_confirm", f"dedupe_confirm.{PROMPT}", msgs, DedupeOut, temperature=0.0,
                                    session=session, regulation_id=reg.id)
        except LLMError as e:
            session.commit()
            log.warning("tekilleştirme onayı alınamadı: %s", e)
            return None
        session.commit()
        return res.outputs[0].same

    return confirm
