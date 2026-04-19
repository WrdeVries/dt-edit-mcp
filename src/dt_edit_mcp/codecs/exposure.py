"""exposure module codec — darktable modversion 6 and 7.

v6 struct (little-endian, 24 bytes):
  uint32  mode
  float   black
  float   exposure              (EV)
  float   deflicker_percentile
  float   deflicker_target_level
  int32   compensate_exposure_bias  (gboolean)

v7 struct (28 bytes) — adds:
  int32   compensate_hilite_pres    (gboolean)

Both are handled by a single codec keyed on byte length.
Verified against real XMP from darktable 4.6.1.
"""
from __future__ import annotations

import struct
from typing import Any

from .base import ModuleCodec, decode_params, encode_params

_FMT_V6 = "<Iffffi"
_FMT_V7 = "<Iffffii"
_SIZE_V6 = struct.calcsize(_FMT_V6)  # 24
_SIZE_V7 = struct.calcsize(_FMT_V7)  # 28


class ExposureCodec(ModuleCodec):
    operation = "exposure"
    modversion = 7  # darktable 4.6.1 uses v7 (28 bytes, includes compensate_hilite_pres)

    def decode(self, raw: str) -> dict[str, Any]:
        data = decode_params(raw)
        if len(data) >= _SIZE_V7:
            mode, black, exposure, defl_pct, defl_tgt, comp_bias, comp_hp = struct.unpack(_FMT_V7, data[:_SIZE_V7])
        else:
            mode, black, exposure, defl_pct, defl_tgt, comp_bias = struct.unpack(_FMT_V6, data[:_SIZE_V6])
            comp_hp = 0
        return {
            "mode": "deflicker" if mode else "manual",
            "black": round(black, 6),
            "exposure": round(exposure, 4),
            "deflicker_percentile": round(defl_pct, 2),
            "deflicker_target_level": round(defl_tgt, 2),
            "compensate_exposure_bias": bool(comp_bias),
            "compensate_hilite_pres": bool(comp_hp),
            "_struct_size": len(data),
        }

    def encode(self, params: dict[str, Any]) -> str:
        mode = 1 if params.get("mode") == "deflicker" else 0
        struct_size = params.get("_struct_size", _SIZE_V7)
        if struct_size <= _SIZE_V6:
            data = struct.pack(
                _FMT_V6,
                mode,
                float(params.get("black", 0.0)),
                float(params.get("exposure", 0.0)),
                float(params.get("deflicker_percentile", 50.0)),
                float(params.get("deflicker_target_level", -4.0)),
                int(params.get("compensate_exposure_bias", 1)),
            )
        else:
            data = struct.pack(
                _FMT_V7,
                mode,
                float(params.get("black", 0.0)),
                float(params.get("exposure", 0.0)),
                float(params.get("deflicker_percentile", 50.0)),
                float(params.get("deflicker_target_level", -4.0)),
                int(params.get("compensate_exposure_bias", 1)),
                int(params.get("compensate_hilite_pres", 0)),
            )
        return encode_params(data)

    @property
    def uses_gzip(self) -> bool:
        return False
