"""Portal API uç noktalarını FastAPI OpenAPI şemasından ``mevzuat-core/API_REFERENCE.md`` olarak üretir.

  cd mevzuat-core/app && python scripts/gen_api_reference.py [--check]

``--check``: dosya güncel değilse 1 ile çıkar (dosyaya yazmaz). Uç nokta değişince dosya yeniden üretilip commit edilir.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
OUT = APP_DIR.parent / "API_REFERENCE.md"
sys.path.insert(0, str(APP_DIR))


def api_reference() -> str:
    from app.api.main import create_app
    from app.db import make_sessionmaker
    from app.settings import Settings

    with tempfile.TemporaryDirectory() as tmp:
        s = Settings(_env_file=None, database_url=f"sqlite:///{tmp}/api.db", cors_origins="")
        spec = create_app(s, make_sessionmaker(s.database_url, auto_create=True)).openapi()
    lines = ["# mevzuat-core — Portal API Referansı", "",
             "Portal (`/`) ve Albatros bu uç noktaları kullanır. Kimlik doğrulama: Keycloak (`narui` realm) Bearer JWT; "
             "`AUTH_MODE=disabled` yalnızca demo içindir. Tüm yanıtlar JSON. Etkileşimli şema: `/docs`, `/openapi.json`.",
             "", "Bu dosya `app/scripts/gen_api_reference.py` ile OpenAPI şemasından üretilir; elle düzenlenmez.", ""]
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
    ap.add_argument("--check", action="store_true", help="dosya güncel değilse 1 ile çık")
    args = ap.parse_args()
    text = api_reference()
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT} güncel değil: python scripts/gen_api_reference.py")
            return 1
        print(f"{OUT.name} güncel")
        return 0
    OUT.write_text(text)
    print(f"Yazıldı: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
