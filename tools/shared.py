"""mevzuat-core ile mevzuat-crawler arasındaki ortak dosyaları denetler / eşitler.

İki servis ayrı kurum reposu olarak yayımlanır; bu yüzden ortak kod (settings, db, storage, mongo, collectors, crawler,
sources.yaml, sertifikalar, ortak testler ve fixture'lar) her iki dizinde de bulunur. Kaynak **mevzuat-core**'dur:
ortak dosyalar önce orada değiştirilir, sonra crawler'a eşitlenir.

  python tools/shared.py check    # fark varsa 1 ile çıkar (CI / commit öncesi)
  python tools/shared.py sync     # core → crawler kopyala (crawler'a özgü dosyalara dokunmaz)

Crawler'a özgü kalanlar (eşitlenmez): app/pyproject.toml, app/requirements.txt, app/.env.example, Dockerfile, deploy/.
Crawler'ın yalnızca bir alt kümesini kullandığı yerler (monitoring/, tests/, fixtures) CRAWLER_* listeleriyle belirlenir;
crawler'da core'da bulunmayan dosya olamaz. Bağımlılıklar da denetlenir: crawler'ın `dependencies` listesi core'un
temel listesiyle aynı olmalıdır.
"""
from __future__ import annotations

import argparse
import filecmp
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "mevzuat-core" / "app"
CRAWLER = ROOT / "mevzuat-crawler" / "app"
IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", "*.egg-info", ".DS_Store", "*.pyc",
                                "data", ".venv", ".env")

# Crawler'ın ihtiyaç duyduğu modüller (içe aktarma kapanışı; tests/test_crawler_service.py ile doğrulanır)
MODULES = ["__init__.py", "settings.py", "db.py", "storage.py", "mongo.py", "collectors", "crawler",
           "monitoring/__init__.py", "monitoring/reprocess.py"]
CONFIG = ["config/sources.yaml", "config/certs"]
TESTS = ["__init__.py", "conftest.py", "test_dates.py", "test_http.py", "test_resolvers.py", "test_resmi_gazete.py",
         "test_runner.py", "test_sources_config.py", "test_sources_replay.py", "test_crawler_service.py"]
FIXTURES_EXCLUDE = {"processing"}      # yalnızca core'un işleme testleri kullanır


def shared_paths() -> list[str]:
    """Core'a göre yol (dosya veya dizin) listesi."""
    paths = [f"app/{m}" for m in MODULES] + CONFIG + [f"tests/{t}" for t in TESTS]
    paths += [f"tests/fixtures/{d.name}" for d in sorted((CORE / "tests" / "fixtures").iterdir())
              if d.is_dir() and d.name not in FIXTURES_EXCLUDE]
    return paths


def files(root: Path, rel: str) -> set[str]:
    p = root / rel
    if p.is_dir():
        return {str(f.relative_to(root)) for f in p.rglob("*")
                if f.is_file() and not any(part in ("__pycache__", ".pytest_cache", "data") for part in f.parts)
                and f.suffix != ".pyc"}
    return {rel} if p.is_file() else set()


def dependencies(pyproject: Path) -> str:
    return re.search(r"^dependencies = \[\n(.*?)^\]", pyproject.read_text(), re.S | re.M).group(1)


def check() -> list[str]:
    problems: list[str] = []
    for rel in shared_paths():
        core_files, crawler_files = files(CORE, rel), files(CRAWLER, rel)
        for f in sorted(core_files | crawler_files):
            if f not in crawler_files:
                problems.append(f"crawler'da eksik: {f}")
            elif f not in core_files:
                problems.append(f"core'da yok (crawler'a özgü dosya olamaz): {f}")
            elif not filecmp.cmp(CORE / f, CRAWLER / f, shallow=False):
                problems.append(f"farklı: {f}")
    # crawler/app/app, config, tests altında paylaşılan listede olmayan dosya kalmasın
    listed = {f for rel in shared_paths() for f in files(CRAWLER, rel)}
    for top in ("app", "config", "tests"):
        for f in sorted(files(CRAWLER, top) - listed):
            problems.append(f"paylaşılan listede yok (shared.py'ye ekleyin ya da silin): {f}")
    if dependencies(CORE / "pyproject.toml") != dependencies(CRAWLER / "pyproject.toml"):
        problems.append("pyproject.toml: temel `dependencies` listeleri farklı")
    return problems


def sync() -> None:
    for rel in shared_paths():
        src, dst = CORE / rel, CRAWLER / rel
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=IGNORE)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    # core'da kalkıp crawler'da kalmış listelenmemiş dosyaları temizle
    listed = {f for rel in shared_paths() for f in files(CRAWLER, rel)}
    for top in ("app", "config", "tests"):
        for f in files(CRAWLER, top) - listed:
            (CRAWLER / f).unlink()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["check", "sync"])
    args = ap.parse_args()
    if args.command == "sync":
        sync()
    problems = check()
    for p in problems:
        print(f"  {p}")
    if problems:
        print(f"{len(problems)} fark — düzeltmek için: python tools/shared.py sync")
        return 1
    print(f"ortak dosyalar eşit ({len(shared_paths())} yol)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
