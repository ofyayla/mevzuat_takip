"""Aynı kaynağın eşzamanlı iki kez taranmasını önleyen kilit. Redis varsa Redis, yoksa dosya kilidi."""
from __future__ import annotations

import contextlib
import fcntl
from pathlib import Path
from typing import Iterator


class LockBusy(Exception):
    pass


@contextlib.contextmanager
def source_lock(code: str, *, redis_url: str | None, lock_dir: Path, ttl_s: int = 3600) -> Iterator[None]:
    if redis_url:
        import redis

        client = redis.Redis.from_url(redis_url)
        lock = client.lock(f"lock:collect:{code}", timeout=ttl_s, blocking=False)
        if not lock.acquire():
            raise LockBusy(code)
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                lock.release()
        return
    lock_dir.mkdir(parents=True, exist_ok=True)
    with open(lock_dir / f"{code}.lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockBusy(code) from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
