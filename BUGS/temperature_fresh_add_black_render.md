# Bug: temperature module produces black render when added fresh (no pre-existing XMP entry)

**Status:** Open  
**Severity:** High — blocks all white-balance correction workflows initiated via the agent  
**Discovered:** 2026-04-19, during skill validation session on `DSC09315.ARW`

---

## Summary

When `set_module` is called with `operation="temperature"` on an image whose XMP has **no pre-existing temperature history entry**, the resulting render is pure black. The module encodes successfully (no exception, valid base64 output), but the pipeline produces an invalid result.

---

## Reproduction

```python
# Prerequisites: open a RAW that has no temperature entry in its XMP history
open_image("DSC09315.ARW")           # history_length shows no "temperature" module
set_module(session_id, "temperature", {"temperature": 4800, "tint": 1.0})
render_preview(session_id)           # → pure black image
```

Confirmed on:
- Image: `DSC09315.ARW` (Sony ARW, darktable 4.6.1)
- After `reset_all` (history cleared to 0)
- After fresh `open_image` on an ARW whose XMP never had a temperature entry

Does **not** reproduce when:
- The temperature module already exists in the XMP from a prior darktable GUI session
- The user sets WB in the darktable GUI, saves the XMP, then calls `set_module` to adjust it

---

## Root cause

### The coefficients are the actual WB — temperature/tint are UI metadata

In darktable's temperature module, the `coeffs[4]` (R, G, B, G2 raw sensor multipliers) are what actually get applied to the RAW data. The `temperature` (K) and `tint` fields are display-only metadata that the GUI uses to show a human-readable value; they do **not** drive the pipeline.

Setting `temperature=4800` without updating `coeffs` has **no effect** on the rendered output. The render is entirely determined by `coeffs`.

### The codec defaults coeffs to `[1.0, 1.0, 1.0, 1.0]`

`temperature.py` line 74:
```python
coeffs = params.get("coeffs", [1.0, 1.0, 1.0, 1.0])
```

All-ones coefficients mean equal scaling of all channels — no actual white balance correction.

### `illuminant=20` ("Camera") with all-ones coefficients is an invalid state

The codec defaults `illuminant` to `"Camera"` (integer 20, line 81):
```python
illuminant = params.get("illuminant", "Camera")
```

With `illuminant=20`, darktable expects `coeffs` to contain the camera's actual embedded WB multipliers (e.g. R≈2.0, G=1.0, B≈1.5 for typical daylight). Receiving `[1, 1, 1, 1]` combined with `illuminant=Camera` creates an internal contradiction in darktable's adaptation math that produces a black output.

### Observed symptoms

When the fresh temperature module is decoded back, the params show:
```json
{
  "temperature": 4800.0,
  "tint": 1.0,
  "coeffs": [1.0, 1.0, 1.0, 1.0],
  "illuminant": "Camera",
  "adaptation": "cat16",
  "_modversion": 5
}
```

The `temperature` field reflects exactly what was written. The `coeffs` are all ones. The render is black.

---

## Why it works when a prior GUI entry exists

When the user opens the image in the darktable GUI and sets WB, darktable writes a temperature entry where `coeffs` contains the camera's actual sensor multipliers (computed from the EXIF-embedded values and the chosen illuminant). The agent then calls `set_module` to change `temperature` from e.g. 5500→4800 — the codec preserves the existing coefficients (since they're present in the decoded params and passed back in), and only the K value changes. The pipeline receives valid coefficients and renders correctly.

---

## Impact

- Any workflow that tries to correct a warm/cool WB cast on a freshly-opened image (or after `reset_all`) fails silently with a black render.
- The `temperature` module appears to work (no error raised, history entry created) but produces no visible correction.
- This blocks the "too orange" fix for RAWs with warm camera WB.

---

## Fix options

### Option A — Seed coefficients from RAW EXIF (correct, but requires EXIF read)

On `set_module(temperature, ...)` when no existing temperature entry exists, read the camera's embedded WB multipliers from the RAW file EXIF (e.g. via `exiftool` or `rawpy`). Use those as the baseline `coeffs`, then scale them to match the requested `temperature` K value.

```python
# Pseudocode in server.py / session.py
if "temperature" not in existing_modules:
    coeffs = read_camera_wb_from_exif(session.raw_path)  # [R, G, B, G2]
    params.setdefault("coeffs", coeffs)
```

This is the most correct fix. The camera's embedded WB coefficients are the natural baseline; adjusting K then scales them proportionally.

### Option B — Switch illuminant to a fixed standard (simpler, slightly lossy)

When adding fresh with no existing coeffs, default `illuminant` to `"Daylight"` (6) instead of `"Camera"` (20), and set `coeffs` to a plausible daylight WB (e.g. `[2.0, 1.0, 1.5, 1.0]` as a rough approximation). Daylight illuminant does not depend on camera-specific coefficients.

Downside: the absolute WB accuracy will be off compared to the camera's actual sensor response. But it won't produce a black render, and the user can tune from there.

### Option C — Validate coeffs before encode, refuse to write all-ones with Camera illuminant

Add a guard in `TemperatureCodec.encode`:

```python
if illuminant == 20 and all(c == 1.0 for c in coeffs):
    raise ValueError(
        "temperature: illuminant=Camera requires real sensor coefficients. "
        "Pass coeffs from get_module_params or use illuminant='Daylight' instead."
    )
```

This at least makes the failure loud rather than silent.

### Recommended fix: Option A + Option C

- Option C as a guard (immediate, prevents silent failures)
- Option A as the proper fix (requires adding an EXIF reader dependency)

---

## Workaround (until fixed)

1. Open the image in the **darktable GUI**.
2. In the temperature module, set WB to any preset (e.g. "Daylight", "As Shot", or a manual K value).
3. Save (Ctrl+S or let darktable auto-save the XMP).
4. Re-open with `open_image(raw_path)` in the agent.
5. Now `set_module(session_id, "temperature", {"temperature": 4800})` will work because the XMP contains valid camera coefficients.

---

## New pitfall to add to `reference/pitfalls.md`

> **Pitfall 11: temperature module black render when added fresh**  
> Symptom: black render after `set_module(temperature, ...)` on an image with no prior temperature history.  
> Cause: codec defaults `coeffs=[1,1,1,1]` + `illuminant=Camera` — invalid combination.  
> Fix: set WB in darktable GUI first, then adjust via agent. Do not add temperature fresh.
