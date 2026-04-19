"""Roundtrip tests for the temperature codec (v3 and v5)."""
import struct
import binascii
import math

from dt_edit_mcp.codecs.temperature import TemperatureCodec

codec = TemperatureCodec()


def _make_v3(r, g, b, g2):
    return binascii.hexlify(struct.pack("<4f", r, g, b, g2)).decode()


def _make_v5(temp, tint, r, g, b, g2, illuminant=20, adaptation=1):
    return binascii.hexlify(
        struct.pack("<ff4fII", temp, tint, r, g, b, g2, illuminant, adaptation)
    ).decode()


def test_decode_v3_real_xmp():
    # Actual bytes from DSC09173.ARW.xmp temperature entry
    raw = "6c6c35400000803f36d4bf3f0000807f"
    p = codec.decode(raw)
    assert p["_modversion"] == 3
    assert "coeffs" in p
    assert abs(p["coeffs"][1] - 1.0) < 1e-5  # G normalized to 1.0
    assert math.isinf(p["coeffs"][3])  # G2 = inf for this sensor


def test_roundtrip_v3():
    raw = _make_v3(2.5, 1.0, 1.3, float("inf"))
    decoded = codec.decode(raw)
    reenc = codec.encode(decoded)
    assert binascii.unhexlify(reenc) == binascii.unhexlify(raw)


def test_roundtrip_v3_typical():
    raw = _make_v3(1.8, 1.0, 1.4, 1.0)
    decoded = codec.decode(raw)
    reenc = codec.encode(decoded)
    assert binascii.unhexlify(reenc) == binascii.unhexlify(raw)


def test_decode_v5():
    raw = _make_v5(5500.0, 1.0, 2.1, 1.0, 1.5, 1.0)
    p = codec.decode(raw)
    assert p["_modversion"] == 5
    assert abs(p["temperature"] - 5500.0) < 0.1
    assert abs(p["tint"] - 1.0) < 1e-4


def test_roundtrip_v5():
    raw = _make_v5(4200.0, 0.97, 2.2, 1.0, 1.6, 1.0, illuminant=7, adaptation=1)
    decoded = codec.decode(raw)
    reenc = codec.encode(decoded)
    assert binascii.unhexlify(reenc) == binascii.unhexlify(raw)


def test_operation_name():
    assert codec.operation == "temperature"
