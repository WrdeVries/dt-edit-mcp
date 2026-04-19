"""Module codec registry — maps (operation, modversion) to codec instances."""
from __future__ import annotations

from .base import ModuleCodec, OpaqueCodec
from .exposure import ExposureCodec
from .temperature import TemperatureCodec
from .colorbalancergb import ColorBalanceRGBCodec

_CODECS: dict[str, ModuleCodec] = {}


def _register(codec: ModuleCodec) -> None:
    _CODECS[codec.operation] = codec


_register(ExposureCodec())
_register(TemperatureCodec())
_register(ColorBalanceRGBCodec())


def get(operation: str, modversion: int | None = None) -> ModuleCodec:
    """
    Return the best codec for the given operation.
    Codecs that handle multiple versions (e.g. exposure v6/v7, temperature v3/v5)
    detect the version internally via byte length. Only warn on a truly unknown
    modversion (newer than anything the codec claims to handle).
    """
    codec = _CODECS.get(operation)
    if codec is None:
        return OpaqueCodec(operation, modversion or 0)
    # Only fall through if the file version is strictly *newer* than the codec maximum.
    # Codecs internally handle older versions via byte-length detection.
    if modversion is not None and modversion > codec.modversion + 2:
        import warnings
        warnings.warn(
            f"Module '{operation}' modversion={modversion} is newer than "
            f"our codec (max v{codec.modversion}); treating as opaque.",
            stacklevel=3,
        )
        return OpaqueCodec(operation, modversion)
    return codec


def supported_operations() -> list[dict]:
    return [
        {
            "operation": codec.operation,
            "modversion": codec.modversion,
            "uses_gzip": codec.uses_gzip,
        }
        for codec in _CODECS.values()
    ]
