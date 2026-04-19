"""Read camera-embedded white balance coefficients from a RAW file."""
from __future__ import annotations

from pathlib import Path


def read_camera_wb(raw_path: Path) -> list[float]:
    """Return [R, G, B, G2] camera WB multipliers, G normalized to 1.0.

    Falls back to daylight-ish values if rawpy is unavailable or EXIF is missing,
    so the caller always gets usable coefficients (never all-ones).
    """
    try:
        import rawpy
        with rawpy.imread(str(raw_path)) as raw:
            r, g, b, g2 = raw.camera_whitebalance
            g_norm = g if g != 0.0 else 1.0
            return [r / g_norm, 1.0, b / g_norm, g2 / g_norm]
    except Exception:
        return [2.0, 1.0, 1.5, 1.0]
