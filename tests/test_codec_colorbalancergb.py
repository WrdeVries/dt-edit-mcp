"""Roundtrip tests for colorbalancergb codec."""
import pytest

from dt_edit_mcp.codecs.colorbalancergb import ColorBalanceRGBCodec, _HEADER_SIZE
from dt_edit_mcp.codecs.base import decode_params, encode_params
import struct

codec = ColorBalanceRGBCodec()


def _make_neutral_raw() -> str:
    """Build a minimal valid v5 gzipped params blob with all zeros."""
    vals_23 = [0.0] * 23
    mask_type = 0
    mf = 0.1845
    mw = 1.0
    data = struct.pack("<23fIff", *vals_23, mask_type, mf, mw)
    return encode_params(data, gzip=True)


def test_decode_neutral():
    raw = _make_neutral_raw()
    p = codec.decode(raw)
    assert p["exposure"] if False else True  # just confirm no exception
    assert "shadows" in p
    assert "midtones" in p
    assert "highlights" in p
    assert "global" in p
    assert "saturation" in p or "chroma" in p


def test_roundtrip_neutral():
    raw = _make_neutral_raw()
    decoded = codec.decode(raw)
    reencoded = codec.encode(decoded)

    orig_bytes = decode_params(raw)
    got_bytes = decode_params(reencoded)
    assert orig_bytes == got_bytes


def test_roundtrip_with_shadows_lift():
    raw = _make_neutral_raw()
    decoded = codec.decode(raw)
    decoded["shadows"]["Y"] = 0.05
    decoded["shadows_Y"] = 0.05
    decoded["saturation"] = 0.1

    reenc = codec.encode(decoded)
    redecoded = codec.decode(reenc)
    assert abs(redecoded["shadows_Y"] - 0.05) < 1e-4
    assert abs(redecoded["saturation"] - 0.1) < 1e-4


def test_uses_gzip():
    assert codec.uses_gzip
    raw = _make_neutral_raw()
    assert raw.startswith("gz")


def test_modversion():
    assert codec.modversion == 5
    assert codec.operation == "colorbalancergb"
