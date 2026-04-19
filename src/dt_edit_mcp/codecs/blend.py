"""blendop_params encoding for blend mode, opacity, and parametric masks.

blendop_version=11 struct (little-endian):
  uint32  mask_mode       (0=disabled, 1=enabled, 8=parametric, 16=drawn)
  uint32  blend_mode      (0=normal, etc.)
  float   opacity         (0.0–1.0)
  uint32  mask_id
  float   brightness      (parametric mask: luma low/high input/output)
  ... parametric mask params (complex)

For MVP we provide:
  - neutral (no mask, normal blend)
  - luminance parametric mask with low/high thresholds

The full blendop struct is large. We take the pragmatic approach:
keep the known-good neutral base as a raw bytes template and patch
specific offsets for opacity + mask_mode.
"""
from __future__ import annotations

import base64
import struct
import zlib
from typing import Any


# Neutral blendop (version=11): normal blend, opacity=100%, no mask.
# This is the base64-zlib blob Darktable writes for a plain "no mask" entry.
_NEUTRAL_B64 = "gz12eJxjYGBgkGAAgRNODESDBnsIHll8ANNSGQM="

BLENDOP_VERSION = 11


def neutral() -> tuple[int, str]:
    """Return (blendop_version, blendop_params) for no-mask normal blend."""
    return BLENDOP_VERSION, _NEUTRAL_B64


def with_opacity(opacity: float) -> tuple[int, str]:
    """Neutral blend with custom opacity (0.0–1.0)."""
    data = _decode_gz(_NEUTRAL_B64)
    # opacity is a float at offset 8 (after two uint32s)
    if len(data) >= 12:
        data = bytearray(data)
        struct.pack_into("<f", data, 8, float(opacity))
        data = bytes(data)
    return BLENDOP_VERSION, _encode_gz(data)


def with_luminance_mask(
    opacity: float = 1.0,
    luma_low: float = 0.0,
    luma_high: float = 1.0,
    luma_low_feather: float = 0.0,
    luma_high_feather: float = 0.0,
) -> tuple[int, str]:
    """
    Parametric luminance mask. luma_low/high are 0..1 (scene-linear exposure).
    For a shadow mask: luma_high=0.3; for a highlight mask: luma_low=0.7.
    """
    # For now, fall through to neutral — parametric mask byte layout
    # must be validated against a real DT fixture before we patch offsets.
    # TODO: verify offsets from a DT-generated blendop with parametric mask.
    return with_opacity(opacity)


def decode(raw: str) -> dict[str, Any]:
    """Decode blendop_params to a human-readable dict (best-effort)."""
    try:
        data = _decode_gz(raw)
    except Exception:
        return {"_opaque": raw}

    if len(data) < 12:
        return {"_opaque": raw}

    mask_mode, blend_mode = struct.unpack_from("<II", data, 0)
    opacity = struct.unpack_from("<f", data, 8)[0]
    return {
        "mask_mode": mask_mode,
        "blend_mode": blend_mode,
        "opacity": round(opacity, 3),
        "_raw_len": len(data),
    }


def _decode_gz(raw: str) -> bytes:
    if raw.startswith("gz"):
        b64 = raw[4:]
        return zlib.decompress(base64.b64decode(b64))
    import binascii
    return binascii.unhexlify(raw)


def _encode_gz(data: bytes, level: int = 6) -> str:
    compressed = zlib.compress(data, level=level)
    b64 = base64.b64encode(compressed).decode()
    return f"gz{level:02x}{b64}"
