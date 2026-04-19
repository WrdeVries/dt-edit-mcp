"""Preview cache keyed on (xmp_content_hash, width, dt_version)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional


class PreviewCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def key(self, xmp_bytes: bytes, width: int, dt_version: str) -> str:
        h = hashlib.sha256()
        h.update(xmp_bytes)
        h.update(str(width).encode())
        h.update(dt_version.encode())
        return h.hexdigest()[:24]

    def get(self, cache_key: str) -> Optional[Path]:
        p = self.cache_dir / f"{cache_key}.jpg"
        return p if p.exists() else None

    def put(self, cache_key: str, src: Path) -> Path:
        dst = self.cache_dir / f"{cache_key}.jpg"
        import shutil
        shutil.copy2(src, dst)
        return dst

    def path_for(self, cache_key: str) -> Path:
        return self.cache_dir / f"{cache_key}.jpg"
