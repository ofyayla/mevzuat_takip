"""İK-2 komut satırı aracı.

  mevzuat-process run [--limit N] [--source KOD]      # bekleyen ham belgeleri işle (çıkar + tekilleştir)
  mevzuat-process reextract [--all] [--id N ...]      # metni yeniden çıkar (varsayılan: OCR bekleyenler)
  mevzuat-process show REG_ID                         # düzenleme ve bağlı belgeler
  mevzuat-process text RAW_ID [--chars N]             # bir belgenin çıkarılmış metni (hata ayıklama)
  mevzuat-process review                              # tekilleştirme onayı bekleyen düzenlemeler
  mevzuat-process merge FROM_ID INTO_ID               # onay: iki düzenlemeyi birleştir
  mevzuat-process split RAW_ID                        # yanlış birleştirmeyi geri al
  mevzuat-process ocr-check [DOSYA.pdf]               # OCR servisine bağlantı testi
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import select

from app.db import RawDocument, Regulation, RegulationSourceLink, make_sessionmaker
from app.processing.dedupe import merge_regulations, split_document
from app.processing.ocr import OcrError, get_ocr_engine
from app.processing.pipeline import DocumentProcessor
from app.settings import get_settings
from app.storage import FileSystemStorage


def _ctx():
    s = get_settings()
    return s, make_sessionmaker(s.database_url), FileSystemStorage(s.raw_storage_dir)


def _print_report(r) -> None:
    print(f"işlenen={r.processed} bağlanan={r.linked} yeni düzenleme={r.new_regulations} "
          f"tekilleştirilen={r.merged} hata={r.failed} OCR bekleyen={r.ocr_pending}")
    for e in r.errors[:20]:
        print(f"  HATA {e}")


def cmd_run(args) -> int:
    from app.ai.dedupe_confirm import make_confirmer
    from app.ai.llm_client import get_llm

    s, sf, st = _ctx()
    llm = get_llm(s)
    confirmer = make_confirmer(llm, sf, s) if llm is not None else None   # belirsiz bant (0.70–0.90) LLM'e sorulur
    r = DocumentProcessor(s, st, confirmer=confirmer).process_pending(sf, limit=args.limit, source=args.source)
    _print_report(r)
    return 1 if r.failed else 0


def cmd_reextract(args) -> int:
    s, sf, st = _ctx()
    r = DocumentProcessor(s, st).reextract(sf, ocr_pending_only=not args.all and not args.id,
                                           raw_ids=args.id, limit=args.limit)
    _print_report(r)
    return 1 if r.failed else 0


def cmd_show(args) -> int:
    _, sf, _ = _ctx()
    with sf() as ses:
        reg = ses.get(Regulation, args.reg_id)
        if reg is None:
            print("düzenleme yok")
            return 1
        print(f"#{reg.id} [{reg.processing_status}] {reg.title}")
        print(f"  kurum={reg.issuer or '?'} ({reg.issuer_name or '-'}) tür={reg.reg_type or '?'} "
              f"değişiklik={'evet' if reg.is_amendment else 'hayır'} yayım={reg.publish_date}")
        print(f"  ek bilgi={reg.extra}")
        rows = ses.execute(select(RegulationSourceLink, RawDocument).join(
            RawDocument, RawDocument.id == RegulationSourceLink.raw_document_id
        ).where(RegulationSourceLink.regulation_id == reg.id).order_by(RawDocument.id))
        for link, raw in rows:
            score = f" {link.match_score:.2f}" if link.match_score is not None else ""
            print(f"  - raw {raw.id:<6} {raw.source_code:<12} {raw.role:<10} {link.match_method}{score} "
                  f"{raw.extraction_method or '-'} {len(raw.text or '')} kr  {raw.url[:90]}")
    return 0


def cmd_text(args) -> int:
    _, sf, _ = _ctx()
    with sf() as ses:
        raw = ses.get(RawDocument, args.raw_id)
        if raw is None:
            return 1
        print(f"[{raw.processing_status}] {raw.extraction_method} OCR güveni={raw.ocr_confidence} "
              f"sayfa={raw.page_count} hata={raw.extract_error} ek={raw.extra}")
        print((raw.text or "")[:args.chars])
    return 0


def cmd_review(args) -> int:
    _, sf, _ = _ctx()
    with sf() as ses:
        for reg in ses.scalars(select(Regulation).where(Regulation.needs_dedupe_review.is_(True))):
            for c in (reg.extra or {}).get("dedupe_candidates", []):
                other = ses.get(Regulation, c["regulation_id"])
                print(f"#{reg.id} ↔ #{other.id} skor={c['score']:.2f}\n    {reg.title[:100]}\n    {other.title[:100]}")
    return 0


def cmd_merge(args) -> int:
    _, sf, _ = _ctx()
    with sf() as ses:
        merge_regulations(ses, args.from_id, args.into_id)
        ses.commit()
    print(f"#{args.from_id} → #{args.into_id} birleştirildi")
    return 0


def cmd_split(args) -> int:
    _, sf, _ = _ctx()
    with sf() as ses:
        new_id = split_document(ses, args.raw_id)
        ses.commit()
    print(f"raw {args.raw_id} yeni düzenlemeye taşındı: #{new_id}")
    return 0


def sample_scanned_pdf() -> bytes:
    """Metin katmanı olmayan (görüntüden oluşan) tek sayfalık Türkçe örnek PDF."""
    import pymupdf

    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 120), "Bankacilik Duzenleme ve Denetleme Kurumundan:", fontsize=14)
    page.insert_text((72, 150), "OCR baglanti testi - Mevzuat Takip", fontsize=14)
    pix = page.get_pixmap(dpi=200)
    out = pymupdf.open()
    img_page = out.new_page(width=page.rect.width, height=page.rect.height)
    img_page.insert_image(img_page.rect, stream=pix.tobytes("png"))
    return out.tobytes()


def cmd_ocr_check(args) -> int:
    s = get_settings()
    engine = get_ocr_engine(s)
    if engine is None:
        print("OCR_PROVIDER=none — OCR yapılandırılmamış (bkz. .env.example)")
        return 1
    content = Path(args.file).read_bytes() if args.file else sample_scanned_pdf()
    try:
        res = engine.analyze(content, "application/pdf", pages=[1])
    except OcrError as e:
        print(f"HATA {e}")
        return 1
    print(f"OK  {engine.name}: {len(res.pages)} sayfa, {res.word_count} kelime, güven={res.confidence}")
    print((res.pages.get(1) or "")[:500])
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mevzuat-process", description="Belge işleme ve tekilleştirme (İK-2)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--source")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("reextract")
    p.add_argument("--all", action="store_true", help="OCR bekleyenlerle sınırlama")
    p.add_argument("--id", type=int, action="append")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(fn=cmd_reextract)
    p = sub.add_parser("show")
    p.add_argument("reg_id", type=int)
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("text")
    p.add_argument("raw_id", type=int)
    p.add_argument("--chars", type=int, default=3000)
    p.set_defaults(fn=cmd_text)
    sub.add_parser("review").set_defaults(fn=cmd_review)
    p = sub.add_parser("merge")
    p.add_argument("from_id", type=int)
    p.add_argument("into_id", type=int)
    p.set_defaults(fn=cmd_merge)
    p = sub.add_parser("split")
    p.add_argument("raw_id", type=int)
    p.set_defaults(fn=cmd_split)
    p = sub.add_parser("ocr-check")
    p.add_argument("file", nargs="?")
    p.set_defaults(fn=cmd_ocr_check)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
