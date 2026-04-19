"""Regression tests for temperature module black-render bug.

Bug: illuminant=Camera + coeffs=[1,1,1,1] produces a pure black render.
Fix: guard in TemperatureCodec.encode + auto-seed in Session.set_module.
"""
import pytest

from dt_edit_mcp.codecs.temperature import TemperatureCodec


def test_encode_camera_illuminant_all_ones_raises():
    codec = TemperatureCodec()
    with pytest.raises(ValueError, match="illuminant=Camera requires real sensor"):
        codec.encode({"temperature": 4800, "tint": 1.0, "illuminant": "Camera"})


def test_encode_camera_illuminant_default_no_coeffs_raises():
    """Default illuminant is Camera — all-ones coeffs must still be rejected."""
    codec = TemperatureCodec()
    with pytest.raises(ValueError, match="illuminant=Camera requires real sensor"):
        codec.encode({"temperature": 4800, "tint": 1.0})


def test_encode_camera_illuminant_real_coeffs_ok():
    codec = TemperatureCodec()
    result = codec.encode({
        "temperature": 4800,
        "tint": 1.0,
        "illuminant": "Camera",
        "coeffs": [2.0, 1.0, 1.5, 1.0],
    })
    assert isinstance(result, str) and len(result) > 0


def test_encode_daylight_illuminant_no_coeffs_ok():
    """Non-Camera illuminants don't require real coefficients."""
    codec = TemperatureCodec()
    result = codec.encode({"temperature": 5500, "tint": 1.0, "illuminant": "Daylight"})
    assert isinstance(result, str) and len(result) > 0


def test_encode_roundtrip_with_real_coeffs():
    """Encode then decode should preserve temperature, tint, and coeffs."""
    codec = TemperatureCodec()
    params_in = {
        "temperature": 4800.0,
        "tint": 1.0,
        "illuminant": "Camera",
        "coeffs": [2.1, 1.0, 1.6, 1.0],
    }
    encoded = codec.encode(params_in)
    decoded = codec.decode(encoded)
    assert abs(decoded["temperature"] - 4800.0) < 1.0
    assert decoded["illuminant"] == "Camera"
    assert abs(decoded["coeffs"][0] - 2.1) < 0.01
    assert abs(decoded["coeffs"][2] - 1.6) < 0.01
