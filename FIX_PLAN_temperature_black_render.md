# Fix Plan: temperature module black render on fresh add

**Bug file:** `BUGS/temperature_fresh_add_black_render.md`  
**Status:** Open — High severity

---

## Root cause (brief)

`TemperatureCodec.encode` defaults `coeffs` to `[1.0, 1.0, 1.0, 1.0]` and `illuminant` to
`"Camera"` (integer 20). With `illuminant=Camera`, darktable expects real sensor multipliers
in `coeffs` (e.g. `[2.0, 1.0, 1.5, 1.0]`). Receiving all-ones causes an invalid internal state
that produces a pure black render — silently, with no error raised.

---

## Implementation plan

### Step 1 — Guard in `TemperatureCodec.encode` (Option C)

**File:** `src/dt_edit_mcp/codecs/temperature.py`  
**Where:** top of `encode()`, after `coeffs = params.get(...)` (line 74)

Add:
```python
if illuminant_val == 20 and all(abs(c - 1.0) < 1e-6 for c in coeffs):
    raise ValueError(
        "temperature: illuminant=Camera requires real sensor coefficients. "
        "Either pass coeffs from get_module_params, or use illuminant='Daylight'. "
        "See BUGS/temperature_fresh_add_black_render.md."
    )
```

Note: `illuminant_val` must be resolved before the check — move the `str→int` resolution
for `illuminant` up before this guard (it is currently done later at line 83-84).

This converts the silent black render into a loud, actionable error. No new dependencies.

---

### Step 2 — EXIF camera WB reader utility (Option A, part 1)

**File:** `src/dt_edit_mcp/exif.py` (new file)

Add `rawpy` to dependencies in `pyproject.toml`:
```toml
dependencies = [
    ...
    "rawpy>=0.18",
]
```

Implement:
```python
"""Read camera-embedded white balance coefficients from a RAW file."""
from pathlib import Path

def read_camera_wb(raw_path: Path) -> list[float]:
    """
    Return [R, G, B, G2] camera WB multipliers from EXIF.
    G is normalized to 1.0 (darktable convention).
    Falls back to [2.0, 1.0, 1.5, 1.0] if rawpy is unavailable or EXIF missing.
    """
    try:
        import rawpy
        with rawpy.imread(str(raw_path)) as raw:
            r, g, b, g2 = raw.camera_whitebalance  # as-shot multipliers
            # Normalize so G == 1.0
            g_norm = g if g != 0 else 1.0
            return [r / g_norm, 1.0, b / g_norm, g2 / g_norm]
    except Exception:
        # Safe fallback: daylight-ish values, won't be black
        return [2.0, 1.0, 1.5, 1.0]
```

The fallback ensures the guard from Step 1 never fires from this code path.

---

### Step 3 — Seed coefficients in `Session.set_module` (Option A, part 2)

**File:** `src/dt_edit_mcp/session.py`  
**Where:** start of `Session.set_module()`, before `codec.encode(params)` (line 100)

```python
from .exif import read_camera_wb
from .xmp.history import find_entry

def set_module(self, operation, params, enabled=True, instance=0, blend=None):
    # Seed camera WB coefficients when adding temperature fresh
    if operation == "temperature" and "coeffs" not in params:
        existing = find_entry(self.doc, "temperature", instance)
        if existing is None:
            camera_coeffs = read_camera_wb(self.raw_path)
            params = {**params, "coeffs": camera_coeffs}

    codec = codec_registry.get(operation)
    ...
```

This is non-destructive: if the caller already supplies `coeffs`, they are left untouched.
If a prior temperature entry exists, `find_entry` returns a valid index and we skip the injection.

---

### Step 4 — Add a test

**File:** `tests/test_temperature_fresh.py` (new file)

```python
"""Regression test: temperature module must not produce all-ones coeffs on fresh add."""
from dt_edit_mcp.codecs.temperature import TemperatureCodec

def test_encode_camera_illuminant_all_ones_raises():
    codec = TemperatureCodec()
    import pytest
    with pytest.raises(ValueError, match="illuminant=Camera requires real sensor"):
        codec.encode({"temperature": 4800, "tint": 1.0, "illuminant": "Camera"})

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
    codec = TemperatureCodec()
    result = codec.encode({"temperature": 5500, "tint": 1.0, "illuminant": "Daylight"})
    assert isinstance(result, str) and len(result) > 0
```

---

### Step 5 — Update pitfalls reference

**File:** `darktable_editor_mcp.md` (or wherever pitfalls are documented)

Append:

> **Pitfall 11: temperature module black render when added fresh**  
> Symptom: black render after `set_module(temperature, ...)` on an image with no prior temperature history.  
> Cause: all-ones `coeffs` + `illuminant=Camera` — invalid combination darktable cannot render.  
> Fix: the agent now seeds real camera coefficients automatically from EXIF on fresh adds.

---

## Execution order

| # | Action | File | Risk |
|---|--------|------|------|
| 1 | Add `rawpy` to `pyproject.toml` | `pyproject.toml` | Low |
| 2 | Create `exif.py` with `read_camera_wb` | `src/dt_edit_mcp/exif.py` | Low |
| 3 | Add guard in `TemperatureCodec.encode` | `codecs/temperature.py:74` | Low |
| 4 | Seed coefficients in `Session.set_module` | `session.py:100` | Low |
| 5 | Add regression tests | `tests/test_temperature_fresh.py` | None |
| 6 | Update pitfalls doc | `darktable_editor_mcp.md` | None |

Steps 2 and 3 are independent and can be done in parallel.  
Step 4 depends on Step 2 (`exif.py` must exist first).  
Step 5 depends on Step 3 (tests the guard).

---

## What this does NOT change

- Existing behaviour for images where `temperature` is already in the XMP history — untouched.
- Caller-supplied `coeffs` — respected as-is.
- v3 (16-byte) modversion path in the codec — not affected.
