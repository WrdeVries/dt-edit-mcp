"""Snapshot management — copy XMP state to named snapshots."""
from __future__ import annotations

import shutil
from pathlib import Path


class SnapshotManager:
    def __init__(self, snapshot_dir: Path):
        self.dir = snapshot_dir
        snapshot_dir.mkdir(parents=True, exist_ok=True)

    def save(self, xmp_path: Path, label: str) -> Path:
        dst = self.dir / f"{label}.xmp"
        shutil.copy2(xmp_path, dst)
        return dst

    def restore(self, label: str, xmp_path: Path) -> None:
        src = self.dir / f"{label}.xmp"
        if not src.exists():
            raise FileNotFoundError(f"Snapshot '{label}' not found")
        shutil.copy2(src, xmp_path)

    def list(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.xmp"))

    def path(self, label: str) -> Path:
        return self.dir / f"{label}.xmp"

    def exists(self, label: str) -> bool:
        return (self.dir / f"{label}.xmp").exists()
