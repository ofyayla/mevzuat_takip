"""Ham içerik arşivi — içerik adresli (sha256). Aynı içerik iki kez yazılmaz.

- ``FileSystemStorage``: yerel disk / paylaşımlı volume (``RAW_STORAGE=fs``).
- ``GridFsStorage``: MongoDB GridFS (``RAW_STORAGE=gridfs``). DMZ crawler buraya yazar, LAN'daki ana servis buradan
  okur; K8s tarafında paylaşımlı disk gerekmez. Anahtar biçimi iki depoda aynıdır (``raw/ab/<sha256>``).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def content_key(sha256: str) -> str:
    return f"raw/{sha256[:2]}/{sha256}"


class FileSystemStorage:
    def __init__(self, root: Path):
        self.root = Path(root)

    key_for = staticmethod(content_key)

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


class GridFsStorage:
    BUCKET = "raw"

    def __init__(self, db):
        import gridfs

        self.db = db
        self.fs = gridfs.GridFSBucket(db, bucket_name=self.BUCKET)

    key_for = staticmethod(content_key)

    def put(self, content: bytes) -> tuple[str, str]:
        sha = hashlib.sha256(content).hexdigest()
        key = self.key_for(sha)
        # Eşzamanlı iki yazım aynı içeriği iki kez saklayabilir; zararsızdır (okuma en son sürümü alır)
        if not self.exists(key):
            self.fs.upload_from_stream(key, content, metadata={"sha256": sha, "size": len(content)})
        return key, sha

    def get(self, key: str) -> bytes:
        import gridfs

        try:
            return self.fs.open_download_stream_by_name(key).read()
        except gridfs.errors.NoFile:
            raise FileNotFoundError(key) from None

    def exists(self, key: str) -> bool:
        return self.db[f"{self.BUCKET}.files"].find_one({"filename": key}, {"_id": 1}) is not None


RawStorage = FileSystemStorage | GridFsStorage


def get_storage(settings) -> RawStorage:
    if settings.raw_storage == "gridfs":
        from app.mongo import crawl_db

        return GridFsStorage(crawl_db(settings))
    return FileSystemStorage(settings.raw_storage_dir)
