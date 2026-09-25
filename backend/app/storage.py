"""Ham içerik arşivi — içerik adresli (sha256) dosya deposu. Aynı içerik iki kez yazılmaz."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


class FileSystemStorage:
    def __init__(self, root: Path):
        self.root = Path(root)

    @staticmethod
    def key_for(sha256: str) -> str:
        return f"raw/{sha256[:2]}/{sha256}"

    def put(self, content: bytes) -> tuple[str, str]:
        sha = hashlib.sha256(content).hexdigest()
        key = self.key_for(sha)
        path = self.root / key
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(content)
            os.replace(tmp, path)  # atomik
        return key, sha

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()
