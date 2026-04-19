"""colorbalancergb module codec — darktable modversion 5.

Real struct layout verified from darktable library.db (132 bytes total):
  float[0-22]  23 core params: shadows/midtones/highlights Y,C,H; global Y,C,H;
               zone weights; chroma, vibrance, saturation; brilliance x4; hue_angle
  float[23-24] 2 unknown/reserved floats (0.0 in neutral state)
  uint32[25]   mask_type (0=none)
  float[26-31] 6-float trailer: [2]=0.1845, [4]=0.1845 (mask pivot values)
  uint32[32]   trailing marker (always 1)

Total: 25*4 + 4 + 6*4 + 4 = 132 bytes
"""
from __future__ import annotations

import struct
from typing import Any

from .base import ModuleCodec, decode_params, encode_params

_FIELDS = [
    "shadows_Y", "shadows_C", "shadows_H",
    "midtones_Y", "midtones_C", "midtones_H",
    "highlights_Y", "highlights_C", "highlights_H",
    "global_Y", "global_C", "global_H",
    "shadows_weight", "highlights_weight", "midtones_weight",
    "chroma", "vibrance", "saturation",
    "brilliance_shadows", "brilliance_midtones",
    "brilliance_highlights", "brilliance_global",
    "hue_angle",
]

# Full struct: 23 floats + 2 reserved floats + uint32 + 6 floats + uint32 = 132 bytes
_FULL_FMT = "<25fI6fI"
_FULL_SIZE = struct.calcsize(_FULL_FMT)  # = 132

# Default neutral trailer: floats[2] and [4] are 0.1845 (mask pivot), final uint32=1
_TRAILER_DEFAULTS = (0.0, 0.0, 0.1845, 0.0, 0.1845, 0.0, 1)


class ColorBalanceRGBCodec(ModuleCodec):
    operation = "colorbalancergb"
    modversion = 5

    @property
    def uses_gzip(self) -> bool:
        return True

    def decode(self, raw: str) -> dict[str, Any]:
        data = decode_params(raw)
        if len(data) < _FULL_SIZE:
            return {"_opaque": raw, "_error": f"too short ({len(data)} < {_FULL_SIZE})"}

        vals = struct.unpack_from(_FULL_FMT, data)

        named = dict(zip(_FIELDS, vals[:23]))
        named["_reserved0"] = vals[23]
        named["_reserved1"] = vals[24]
        named["mask_type"] = vals[25]
        named["mask_grey_fulcrum"] = round(vals[28], 4)
        named["mask_weight"] = round(vals[30], 4)
        named["_trailer_end"] = vals[32]

        for k in _FIELDS:
            named[k] = round(named[k], 4)

        named["shadows"] = {"Y": named["shadows_Y"], "C": named["shadows_C"], "H": named["shadows_H"]}
        named["midtones"] = {"Y": named["midtones_Y"], "C": named["midtones_C"], "H": named["midtones_H"]}
        named["highlights"] = {"Y": named["highlights_Y"], "C": named["highlights_C"], "H": named["highlights_H"]}
        named["global"] = {"Y": named["global_Y"], "C": named["global_C"], "H": named["global_H"]}

        extra = data[_FULL_SIZE:]
        if extra:
            named["_extra"] = extra.hex()
        return named

    def encode(self, params: dict[str, Any]) -> str:
        for zone in ("shadows", "midtones", "highlights", "global"):
            sub = params.get(zone, {})
            if isinstance(sub, dict):
                for ch in ("Y", "C", "H"):
                    params.setdefault(f"{zone}_{ch}", sub.get(ch, 0.0))

        _DEFAULTS = {"shadows_weight": 1.0, "midtones_weight": 1.0}

        def g(name, default=0.0):
            return float(params.get(name, _DEFAULTS.get(name, default)))

        vals_23 = [g(f) for f in _FIELDS]
        reserved0 = g("_reserved0", 0.0)
        reserved1 = g("_reserved1", 0.0)
        mask_type = int(params.get("mask_type", 0))

        mgf = g("mask_grey_fulcrum", 0.1845)
        mw = g("mask_weight", 1.0)

        # Use roundtripped trailer if present, otherwise apply neutral defaults
        if "_trailer" in params:
            # Legacy: old codec stored raw trailer hex — try to use it
            trailer_bytes = bytes.fromhex(params["_trailer"])
            trailer_floats = list(struct.unpack_from("<6f", trailer_bytes[:24])) if len(trailer_bytes) >= 24 else list(_TRAILER_DEFAULTS[:6])
            trailer_end = struct.unpack_from("<I", trailer_bytes[24:28])[0] if len(trailer_bytes) >= 28 else 1
        else:
            trailer_floats = list(_TRAILER_DEFAULTS[:6])
            trailer_floats[2] = mgf
            trailer_floats[4] = mgf
            trailer_end = int(params.get("_trailer_end", 1))

        data = struct.pack(
            _FULL_FMT,
            *vals_23, reserved0, reserved1,
            mask_type,
            *trailer_floats,
            trailer_end,
        )
        return encode_params(data, gzip=True)
