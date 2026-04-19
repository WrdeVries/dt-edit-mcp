"""Roundtrip tests for the exposure codec."""
import struct
import binascii
import pytest

from dt_edit_mcp.codecs.exposure import ExposureCodec

codec = ExposureCodec()


def _make_hex_v7(mode, black, exposure, pct, tgt, comp_bias, comp_hp):
    return binascii.hexlify(struct.pack("<Iffffii", mode, black, exposure, pct, tgt, comp_bias, comp_hp)).decode()

def _make_hex_v6(mode, black, exposure, pct, tgt, comp_bias):
    return binascii.hexlify(struct.pack("<Iffffi", mode, black, exposure, pct, tgt, comp_bias)).decode()

# Use v7 for most tests (backward compat alias)
def _make_hex(mode, black, exposure, pct, tgt, comp_bias, comp_hp):
    return _make_hex_v7(mode, black, exposure, pct, tgt, comp_bias, comp_hp)


def test_decode_neutral():
    raw = _make_hex(0, 0.0, 0.0, 50.0, -4.0, 0, 0)
    p = codec.decode(raw)
    assert p["mode"] == "manual"
    assert p["exposure"] == 0.0
    assert p["black"] == 0.0


def test_decode_half_ev():
    raw = _make_hex(0, 0.0, 0.5, 50.0, -4.0, 1, 1)
    p = codec.decode(raw)
    assert abs(p["exposure"] - 0.5) < 1e-4
    assert p["compensate_exposure_bias"] is True


def test_roundtrip_zero():
    raw = _make_hex(0, 0.0, 0.0, 50.0, -4.0, 0, 0)
    assert codec.encode(codec.decode(raw)) == raw


def test_roundtrip_positive_ev():
    raw = _make_hex(0, 0.0, 1.5, 50.0, -4.0, 1, 0)
    assert codec.encode(codec.decode(raw)) == raw


def test_roundtrip_negative_black():
    raw = _make_hex(0, -0.01, -0.3, 50.0, -4.0, 0, 0)
    result = codec.encode(codec.decode(raw))
    # Allow tiny FP rounding
    orig_bytes = binascii.unhexlify(raw)
    got_bytes = binascii.unhexlify(result)
    assert orig_bytes == got_bytes


def test_encode_from_dict():
    p = {"exposure": 0.75, "black": 0.001, "compensate_exposure_bias": True}
    raw = codec.encode(p)
    decoded = codec.decode(raw)
    assert abs(decoded["exposure"] - 0.75) < 1e-4
    assert decoded["compensate_exposure_bias"] is True


def test_modversion():
    assert codec.modversion == 6  # minimum; handles both v6 and v7
    assert codec.operation == "exposure"

def test_roundtrip_v6():
    raw = _make_hex_v6(0, 0.0, 0.7, 50.0, -4.0, 1)
    decoded = codec.decode(raw)
    assert abs(decoded["exposure"] - 0.7) < 1e-4
    assert decoded["_struct_size"] == 24
    reenc = codec.encode(decoded)
    assert binascii.unhexlify(reenc) == binascii.unhexlify(raw)

def test_v6_real_xmp_bytes():
    # Actual bytes from DSC09173.ARW.xmp exposure entry
    raw = "00000000000080b93333333f00004842000080c001000000"
    decoded = codec.decode(raw)
    assert abs(decoded["exposure"] - 0.7) < 0.001
    assert decoded["mode"] == "manual"
    assert decoded["_struct_size"] == 24


def test_not_gzipped():
    assert not codec.uses_gzip
    raw = _make_hex(0, 0.0, 0.0, 50.0, -4.0, 0, 0)
    assert not raw.startswith("gz")
