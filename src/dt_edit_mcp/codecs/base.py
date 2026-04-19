"""ModuleCodec ABC plus gzip/hex utilities shared by all codecs."""
from __future__ import annotations

import base64
import binascii
import struct
import zlib
from abc import ABC, abstractmethod
from typing import Any


def hex_to_bytes(s: str) -> bytes:
    return binascii.unhexlify(s)


def bytes_to_hex(b: bytes) -> str:
    return binascii.hexlify(b).decode()


def decode_params(raw: str) -> bytes:
    """Decode a darktable params string (plain hex or gz##<base64>) to raw bytes."""
    if raw.startswith("gz"):
        # gz## where ## is two hex digits (zlib level indicator, ignored for decode)
        b64 = raw[4:]
        compressed = base64.b64decode(b64)
        return zlib.decompress(compressed)
    return hex_to_bytes(raw)


def encode_params(data: bytes, gzip: bool = False, level: int = 6) -> str:
    """Encode raw bytes back to darktable params string."""
    if gzip:
        compressed = zlib.compress(data, level=level)
        b64 = base64.b64encode(compressed).decode()
        return f"gz{level:02x}{b64}"
    return bytes_to_hex(data)


class ModuleCodec(ABC):
    """Base class for per-module parameter encode/decode."""

    @property
    @abstractmethod
    def operation(self) -> str: ...

    @property
    @abstractmethod
    def modversion(self) -> int: ...

    @property
    def uses_gzip(self) -> bool:
        return False

    @abstractmethod
    def decode(self, raw: str) -> dict[str, Any]:
        """Decode a raw params string to a human-readable dict."""
        ...

    @abstractmethod
    def encode(self, params: dict[str, Any]) -> str:
        """Encode a params dict back to a raw params string."""
        ...

    def blendop_defaults(self) -> tuple[int, str]:
        """Return (blendop_version, blendop_params) for a new entry with no masking."""
        return DEFAULT_BLENDOP_VERSION, DEFAULT_BLENDOP_PARAMS


# Neutral blendop: normal blend mode, opacity 100%, no mask
# Decoded from a real Darktable XMP with no mask applied (blendop_version=11)
DEFAULT_BLENDOP_VERSION = 11
DEFAULT_BLENDOP_PARAMS = "gz12eJxjYGBgkGAAgRNODESDBnsIHll8ANNSGQM="


class OpaqueCodec(ModuleCodec):
    """Passthrough for unknown modules — preserves params bytes unchanged."""

    def __init__(self, operation: str, modversion: int):
        self._operation = operation
        self._modversion = modversion

    @property
    def operation(self) -> str:
        return self._operation

    @property
    def modversion(self) -> int:
        return self._modversion

    def decode(self, raw: str) -> dict[str, Any]:
        return {"_opaque": raw}

    def encode(self, params: dict[str, Any]) -> str:
        return params.get("_opaque", "")
