"""temperature (white balance) module codec.

Two struct versions are supported:

modversion 3 — 16 bytes, coefficients only:
  float  coeffs[4]   (R, G, B, G2 — raw sensor multipliers; G=1.0 normalized)

modversion 5+ — 32 bytes, full representation:
  float  temperature  (K)
  float  tint         (-1..+1)
  float  coeffs[4]
  uint32 illuminant
  uint32 adaptation

Verified against darktable 4.6.1 XMP (version 3 produced by colorchecker pipeline).
"""
from __future__ import annotations

import struct
from typing import Any

from .base import ModuleCodec, decode_params, encode_params

# v3: just raw sensor coefficients
_FMT_V3 = "<4f"
_SIZE_V3 = struct.calcsize(_FMT_V3)  # 16

# v5+: full K/tint + coefficients + illuminant/adaptation
_FMT_V5 = "<ff4fII"
_SIZE_V5 = struct.calcsize(_FMT_V5)  # 32

_ILLUMINANT_NAMES = {
    0: "A", 1: "D", 2: "E", 3: "F", 4: "LED", 5: "BB",
    6: "Daylight", 7: "Cloudy", 8: "Shade", 9: "Tungsten",
    10: "Fluorescent", 11: "Flash", 12: "Custom",
    20: "Camera", 21: "DNG", 22: "Equal",
}
_ILLUMINANT_BY_NAME = {v: k for k, v in _ILLUMINANT_NAMES.items()}

_ADAPTATION_NAMES = {
    0: "linear_bradford", 1: "cat16", 2: "non_linear_bradford",
    3: "xyz", 4: "last",
}
_ADAPTATION_BY_NAME = {v: k for k, v in _ADAPTATION_NAMES.items()}


class TemperatureCodec(ModuleCodec):
    operation = "temperature"
    modversion = 3  # lowest supported; also handles v5+ by byte-length detection

    def decode(self, raw: str) -> dict[str, Any]:
        data = decode_params(raw)
        if len(data) <= _SIZE_V3:
            # v3: coefficients only
            r, g, b, g2 = struct.unpack(_FMT_V3, data[:_SIZE_V3])
            return {
                "coeffs": [round(r, 6), round(g, 6), round(b, 6), round(g2, 6)],
                "_modversion": 3,
                "_note": "v3 stores raw sensor coefficients only; G is normalized to 1.0",
            }
        else:
            # v5+: full representation
            temp, tint, r, g, b, g2, illuminant, adaptation = struct.unpack(_FMT_V5, data[:_SIZE_V5])
            return {
                "temperature": round(temp, 1),
                "tint": round(tint, 4),
                "coeffs": [round(r, 6), round(g, 6), round(b, 6), round(g2, 6)],
                "illuminant": _ILLUMINANT_NAMES.get(illuminant, str(illuminant)),
                "adaptation": _ADAPTATION_NAMES.get(adaptation, str(adaptation)),
                "_modversion": 5,
            }

    def encode(self, params: dict[str, Any]) -> str:
        coeffs = params.get("coeffs", [1.0, 1.0, 1.0, 1.0])
        if params.get("_modversion", 5) <= 3 and "temperature" not in params:
            # Encode as v3
            data = struct.pack(_FMT_V3,
                float(coeffs[0]), float(coeffs[1]),
                float(coeffs[2]), float(coeffs[3]))
        else:
            illuminant = params.get("illuminant", "Camera")
            adaptation = params.get("adaptation", "cat16")
            if isinstance(illuminant, str):
                illuminant = _ILLUMINANT_BY_NAME.get(illuminant, 20)
            if isinstance(adaptation, str):
                adaptation = _ADAPTATION_BY_NAME.get(adaptation, 1)
            # illuminant=Camera (20) with all-ones coeffs is an invalid state that
            # produces a pure black render in darktable — catch it before encoding.
            if illuminant == 20 and all(abs(c - 1.0) < 1e-6 for c in coeffs):
                raise ValueError(
                    "temperature: illuminant=Camera requires real sensor coefficients. "
                    "Pass 'coeffs' from get_module_params, or use illuminant='Daylight'. "
                    "See BUGS/temperature_fresh_add_black_render.md."
                )
            data = struct.pack(
                _FMT_V5,
                float(params.get("temperature", 5500.0)),
                float(params.get("tint", 1.0)),
                float(coeffs[0]), float(coeffs[1]),
                float(coeffs[2]), float(coeffs[3]),
                int(illuminant), int(adaptation),
            )
        return encode_params(data)
