"""Kurum repolarını üretir: mevzuat-core, mevzuat-crawler ve ai-uat-charts (kurum örneğindeki düzen).

Tek kaynak bu repodur (backend/, docs/, portal). Kurumdaki iki repo ve chart klasörleri bu betikle üretilir ve olduğu
gibi kopyalanır; ortak kod (settings, db, storage, mongo, collectors) iki repoda da bulunur ama elle çoğaltılmaz.

  backend/.venv/bin/python kurum/export.py [ÇIKIŞ_DİZİNİ] [--force]     # varsayılan: ~/Downloads/mevzuat-kurum

Çıktı:
  mevzuat-core/      app/ (backend), portal/, docs/, deploy/, Dockerfile, pip.conf, gunicorn_config.py, Jenkinsfile,
                     README.md, API_REFERENCE.md (Portal API'den üretilir)
  mevzuat-crawler/   app/ (yalnızca toplama + crawler), deploy/, Dockerfile, pip.conf, Jenkinsfile, README.md,
                     API_REFERENCE.md
  ai-uat-charts/     mevzuat-core/, mevzuat-crawler/
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
KURUM = ROOT / "kurum"
IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", "*.egg-info", ".DS_Store", "*.pyc",
                                "data", ".venv", ".env")

# Crawler'ın ihtiyaç duyduğu modüller (içe aktarma kapanışı; tests/test_crawler_service.py ile doğrulanır)
CRAWLER_MODULES = ["__init__.py", "settings.py", "db.py", "storage.py", "mongo.py", "collectors", "crawler",
                   "monitoring/__init__.py", "monitoring/reprocess.py"]
CRAWLER_TESTS = ["__init__.py", "conftest.py", "test_dates.py", "test_http.py", "test_resolvers.py",
                 "test_resmi_gazete.py", "test_runner.py", "test_sources_config.py", "test_sources_replay.py",
                 "test_crawler_service.py"]
CRAWLER_FIXTURES_EXCLUDE = {"processing"}


def copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, ignore=IGNORE, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def skeleton(name: str, out: Path) -> None:
    """kurum/<repo>/ altındaki repo dosyaları (requirements.txt app/ içine gider)."""
    for item in (KURUM / name).iterdir():
        if item.name in ("requirements.txt", ".DS_Store"):
            continue
        copy(item, out / item.name)
    copy(KURUM / name / "requirements.txt", out / "app" / "requirements.txt")


def export_core(out: Path) -> None:
    skeleton("mevzuat-core", out)
    app = out / "app"
    for item in ("app", "config", "migrations", "tests", "scripts", "alembic.ini", ".env.example", "README.md"):
        copy(BACKEND / item, app / item)
    pyproject = (BACKEND / "pyproject.toml").read_text()
    (app / "pyproject.toml").write_text(pyproject.replace('name = "mevzuat-takip-backend"', 'name = "mevzuat-core"'))
    for item in ("Mevzuat Takip Portali.dc.html", "support.js"):
        copy(ROOT / item, out / "portal" / item)
    for doc in sorted((ROOT / "docs").glob("*.md")):
        copy(doc, out / "docs" / doc.name)
    (out / "API_REFERENCE.md").write_text(api_reference())


def export_crawler(out: Path) -> None:
    skeleton("mevzuat-crawler", out)
    app = out / "app"
    for mod in CRAWLER_MODULES:
        copy(BACKEND / "app" / mod, app / "app" / mod)
    copy(BACKEND / "config" / "sources.yaml", app / "config" / "sources.yaml")
    copy(BACKEND / "config" / "certs", app / "config" / "certs")
    for test in CRAWLER_TESTS:
        copy(BACKEND / "tests" / test, app / "tests" / test)
    for fx in (BACKEND / "tests" / "fixtures").iterdir():
        if fx.is_dir() and fx.name not in CRAWLER_FIXTURES_EXCLUDE:
            copy(fx, app / "tests" / "fixtures" / fx.name)
    copy(KURUM / "mevzuat-crawler" / ".env.example", app / ".env.example")
    (out / ".env.example").unlink(missing_ok=True)
    (app / "pyproject.toml").write_text(crawler_pyproject())


def crawler_pyproject() -> str:
    """Temel bağımlılıklar (core ekstraları olmadan) ve yalnızca crawler komutları."""
    src = (BACKEND / "pyproject.toml").read_text()
    deps = re.search(r"^dependencies = \[\n(.*?)^\]", src, re.S | re.M).group(1)
    tail = src[src.index("[build-system]"):]
    return f'''[project]
name = "mevzuat-crawler"
version = "0.1.0"
description = "Mevzuat Takip — kaynak tarama servisi (DMZ crawler → MongoDB)"
requires-python = ">=3.11"
dependencies = [
{deps}]

[project.optional-dependencies]
dev = ["pytest>=8", "respx>=0.21", "ruff>=0.4"]

[project.scripts]
mevzuat-collect = "app.collectors.cli:main"
mevzuat-crawler = "app.crawler.cli:main"

{tail}'''


def api_reference() -> str:
    """Portal API uç noktaları (FastAPI OpenAPI şemasından)."""
    sys.path.insert(0, str(BACKEND))
    from app.api.main import create_app
    from app.db import make_sessionmaker
    from app.settings import Settings

    with tempfile.TemporaryDirectory() as tmp:
        s = Settings(_env_file=None, database_url=f"sqlite:///{tmp}/api.db", cors_origins="")
        spec = create_app(s, make_sessionmaker(s.database_url, auto_create=True)).openapi()
    lines = ["# mevzuat-core — Portal API Referansı", "",
             "Portal (`/`) ve Albatros bu uç noktaları kullanır. Kimlik doğrulama: Keycloak (`narui` realm) Bearer JWT; "
             "`AUTH_MODE=disabled` yalnızca demo içindir. Tüm yanıtlar JSON. Etkileşimli şema: `/docs`, `/openapi.json`.",
             "", "Bu dosya `kurum/export.py` ile OpenAPI şemasından üretilir; elle düzenlenmez.", ""]
    groups: dict[str, list[str]] = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            group = path.split("/")[3] if path.startswith("/api/v1/") and len(path.split("/")) > 3 else "sistem"
            params = ", ".join(f"`{p['name']}`" for p in op.get("parameters", []) if p.get("in") in ("query", "path"))
            body = "evet" if "requestBody" in op else ""
            desc = (op.get("summary") or op.get("description") or "").strip().splitlines()[0] if (
                op.get("summary") or op.get("description")) else ""
            groups.setdefault(group, []).append(f"| `{method.upper()}` | `{path}` | {params} | {body} | {desc} |")
    for group in sorted(groups):
        lines += [f"## {group}", "", "| Yöntem | Yol | Parametreler | Gövde | Açıklama |", "|---|---|---|---|---|",
                  *groups[group], ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", nargs="?", default=str(Path.home() / "Downloads" / "mevzuat-kurum"))
    ap.add_argument("--force", action="store_true", help="çıkış dizini varsa silip yeniden üret")
    args = ap.parse_args()
    out = Path(args.out).expanduser().resolve()
    if out.exists():
        if not args.force:
            print(f"{out} zaten var (üzerine yazmak için --force)")
            return 1
        shutil.rmtree(out)
    export_core(out / "mevzuat-core")
    export_crawler(out / "mevzuat-crawler")
    copy(KURUM / "ai-uat-charts", out / "ai-uat-charts")
    for p in out.rglob(".DS_Store"):
        p.unlink()
    print(f"Üretildi: {out}")
    for repo in ("mevzuat-core", "mevzuat-crawler", "ai-uat-charts/mevzuat-core", "ai-uat-charts/mevzuat-crawler"):
        n = sum(1 for p in (out / repo).rglob("*") if p.is_file())
        print(f"  {repo:<30} {n} dosya")
    return 0


if __name__ == "__main__":
    sys.exit(main())
