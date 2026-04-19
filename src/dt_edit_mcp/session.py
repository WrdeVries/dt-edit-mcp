"""Session management — one session per open RAW file."""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .xmp import parser as xmp_parser
from .xmp.history import upsert_module, set_history_end, disable_module, find_entry
from .codecs import registry as codec_registry
from .exif import read_camera_wb
from .codecs.blend import neutral as blend_neutral, with_opacity, with_luminance_mask
from .renderer import darktable_cli
from .renderer.cache import PreviewCache
from .snapshots import SnapshotManager

_PROJECT_ROOT: Path | None = None


def set_project_root(root: Path) -> None:
    global _PROJECT_ROOT
    _PROJECT_ROOT = root.resolve()


def _check_path(p: Path) -> Path:
    p = p.resolve()
    if _PROJECT_ROOT and not str(p).startswith(str(_PROJECT_ROOT)):
        raise PermissionError(
            f"Path '{p}' is outside the allowed project root '{_PROJECT_ROOT}'. "
            "Set a broader root via DTMCP_ROOT env var if needed."
        )
    return p


@dataclass
class Session:
    session_id: str
    raw_path: Path
    xmp_path: Path
    work_dir: Path          # .dtmcp/ subfolder
    doc: xmp_parser.XmpDoc
    snapshots: SnapshotManager
    cache: PreviewCache
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def info(self) -> dict:
        return {
            "session_id": self.session_id,
            "raw_path": str(self.raw_path),
            "xmp_path": str(self.xmp_path),
            "history_length": len(self.doc.history),
            "history_end": self.doc.history_end,
            "modules": [e.operation for e in self.doc.history[:self.doc.history_end]],
        }

    def get_history(self) -> list[dict]:
        out = []
        for i, e in enumerate(self.doc.history):
            active = i < self.doc.history_end
            entry: dict = {
                "num": e.num,
                "operation": e.operation,
                "enabled": e.enabled,
                "modversion": e.modversion,
                "active": active,
                "instance": e.multi_priority,
            }
            # Decode if we have a codec
            codec = codec_registry.get(e.operation, e.modversion)
            if not isinstance(codec, codec_registry.OpaqueCodec):
                try:
                    entry["params"] = codec.decode(e.params)
                except Exception as exc:
                    entry["params_error"] = str(exc)
            out.append(entry)
        return out

    def get_module_params(self, operation: str, instance: int = 0) -> Optional[dict]:
        from .xmp.history import find_entry
        idx = find_entry(self.doc, operation, instance)
        if idx is None:
            return None
        e = self.doc.history[idx]
        codec = codec_registry.get(e.operation, e.modversion)
        return codec.decode(e.params)

    def set_module(
        self,
        operation: str,
        params: dict,
        enabled: bool = True,
        instance: int = 0,
        blend: Optional[dict] = None,
    ) -> dict:
        # When adding temperature fresh (no prior XMP entry, no caller-supplied coeffs),
        # seed real camera WB multipliers from EXIF so illuminant=Camera stays valid.
        if operation == "temperature" and "coeffs" not in params:
            if find_entry(self.doc, "temperature", instance) is None:
                params = {**params, "coeffs": read_camera_wb(self.raw_path)}

        codec = codec_registry.get(operation)
        encoded_params = codec.encode(params)

        # Blend / mask
        if blend is None:
            bv, bp = blend_neutral()
        else:
            opacity = float(blend.get("opacity", 1.0))
            if "luma_low" in blend or "luma_high" in blend:
                bv, bp = with_luminance_mask(
                    opacity=opacity,
                    luma_low=float(blend.get("luma_low", 0.0)),
                    luma_high=float(blend.get("luma_high", 1.0)),
                    luma_low_feather=float(blend.get("luma_low_feather", 0.0)),
                    luma_high_feather=float(blend.get("luma_high_feather", 0.0)),
                )
            else:
                bv, bp = with_opacity(opacity)

        with self._lock:
            entry = upsert_module(
                self.doc,
                operation=operation,
                params=encoded_params,
                modversion=codec.modversion,
                enabled=enabled,
                blendop_version=bv,
                blendop_params=bp,
                instance=instance,
            )
            xmp_parser.save(self.doc)

        return {
            "operation": entry.operation,
            "modversion": entry.modversion,
            "enabled": entry.enabled,
            "params": codec.decode(encoded_params),
            "history_end": self.doc.history_end,
        }

    def disable_module_op(self, operation: str, instance: int = 0) -> bool:
        with self._lock:
            ok = disable_module(self.doc, operation, instance)
            if ok:
                xmp_parser.save(self.doc)
        return ok

    def undo(self, steps: int = 1) -> int:
        with self._lock:
            set_history_end(self.doc, self.doc.history_end - steps)
            xmp_parser.save(self.doc)
        return self.doc.history_end

    def redo(self, steps: int = 1) -> int:
        with self._lock:
            set_history_end(self.doc, self.doc.history_end + steps)
            xmp_parser.save(self.doc)
        return self.doc.history_end

    def reset_all(self) -> None:
        with self._lock:
            self.doc.history_end = 0
            xmp_parser.save(self.doc)

    def snapshot(self, label: str) -> str:
        with self._lock:
            self.snapshots.save(self.xmp_path, label)
        return label

    def restore_snapshot(self, label: str) -> None:
        with self._lock:
            self.snapshots.restore(label, self.xmp_path)
            self.doc = xmp_parser.load(self.xmp_path)

    def render(
        self,
        width: int = 1280,
        snapshot: Optional[str] = None,
        hq: bool = False,
    ) -> Path:
        if snapshot:
            xmp_to_use = self.snapshots.path(snapshot)
            if not xmp_to_use.exists():
                raise FileNotFoundError(f"Snapshot '{snapshot}' not found")
        else:
            xmp_to_use = self.xmp_path

        xmp_bytes = xmp_to_use.read_bytes()
        dt_ver = darktable_cli.dt_version()
        cache_key = self.cache.key(xmp_bytes, width, dt_ver)

        cached = self.cache.get(cache_key)
        if cached:
            return cached

        tmp_out = self.work_dir / "preview" / f"render_{cache_key}.jpg"
        tmp_out.parent.mkdir(parents=True, exist_ok=True)

        darktable_cli.render(
            raw_path=self.raw_path,
            xmp_path=xmp_to_use,
            output_path=tmp_out,
            width=width,
            hq=hq,
        )
        return self.cache.put(cache_key, tmp_out)


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def open(self, raw_path_str: str, reset: bool = False) -> Session:
        raw_path = _check_path(Path(raw_path_str))
        if not raw_path.exists():
            raise FileNotFoundError(f"RAW file not found: {raw_path}")

        session_id = _session_id(raw_path)

        with self._lock:
            if session_id in self._sessions and not reset:
                return self._sessions[session_id]

        # Setup .dtmcp working directory next to the RAW
        work_dir = raw_path.parent / ".dtmcp"
        work_dir.mkdir(exist_ok=True)

        xmp_path = raw_path.with_suffix(".xmp")

        _check_darktable_warnings(raw_path)

        if reset:
            doc = xmp_parser.load_or_create(xmp_path)
            doc.history = []
            doc.history_end = 0
            # 0 = darktable will re-apply its default pipeline (denoise, filmic, etc.)
            # on next render. Setting 1 with empty history strips the auto-presets.
            doc.auto_presets_applied = 0
        else:
            doc = xmp_parser.load_or_create(xmp_path)

        snapshots = SnapshotManager(work_dir / "snapshots")
        cache = PreviewCache(work_dir / "preview_cache")

        session = Session(
            session_id=session_id,
            raw_path=raw_path,
            xmp_path=xmp_path,
            work_dir=work_dir,
            doc=doc,
            snapshots=snapshots,
            cache=cache,
        )
        xmp_parser.save(doc)

        with self._lock:
            self._sessions[session_id] = session

        return session

    def get(self, session_id: str) -> Session:
        s = self._sessions.get(session_id)
        if s is None:
            raise KeyError(f"Session '{session_id}' not found. Call open_image first.")
        return s

    def close(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


def _session_id(raw_path: Path) -> str:
    return hashlib.sha256(str(raw_path.resolve()).encode()).hexdigest()[:16]



def _check_darktable_warnings(raw_path: Path) -> None:
    """Warn about GUI running or OneDrive path."""
    warnings_out = []

    # Check if darktable GUI is running
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq darktable.exe"],
            capture_output=True, text=True, timeout=5
        )
        if "darktable.exe" in result.stdout:
            warnings_out.append(
                "WARNING: darktable GUI appears to be running. "
                "Close the image in the GUI before editing, or XMP writes may conflict."
            )
    except Exception:
        pass

    # Check for OneDrive path
    rp = str(raw_path.resolve())
    if "OneDrive" in rp:
        warnings_out.append(
            "WARNING: File is in a OneDrive-synced folder. "
            "XMP writes may occasionally fail due to sync locks. "
            "Consider working from a non-synced location."
        )

    # Check if image is in darktable library DB (read-only check)
    db_path = Path.home() / "AppData" / "Local" / "darktable" / "library.db"
    if db_path.exists():
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
            cur = con.execute(
                "SELECT id FROM images WHERE filename=? LIMIT 1",
                (raw_path.name,)
            )
            if cur.fetchone():
                warnings_out.append(
                    "NOTE: This image appears to be in your darktable library. "
                    "When you next open it in the GUI, click 'load from XMP' "
                    "to see our edits."
                )
            con.close()
        except Exception:
            pass

    if warnings_out:
        for w in warnings_out:
            warnings.warn(w, stacklevel=3)
