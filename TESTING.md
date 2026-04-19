# dt-edit-mcp Testing Plan

## Overview

This document covers how to systematically test the dt-edit-mcp tool end-to-end,
what to watch for at each stage, and how to diagnose and fix the most common failure modes.

---

## Prerequisites

- darktable installed at `C:\Program Files\darktable\bin\darktable-cli.exe`
  (or set `DARKTABLE_CLI` env var)
- A test RAW file (any `.ARW`, `.CR2`, `.NEF`, `.DNG`)
- MCP server running and connected (verify with `list_supported_modules`)

---

## Test Suite

### 1. Connectivity

**Goal:** Confirm the MCP server is alive and the codec registry is correct.

```
list_supported_modules
```

**Expected:** Three entries — `exposure` (v6), `temperature` (v3), `colorbalancergb` (v5, gzip).

**Fix if missing entries:** Check that `src/dt_edit_mcp/codecs/registry.py` registers all three codecs.

---

### 2. Open Image

**2a. Fresh open (no existing XMP)**

```
open_image(raw_path="...", reset=False)
```

Expected:
- `session_id` returned
- `xmp_path` points to a `.xmp` next to the RAW
- `history_length: 0`, `history_end: 0`
- XMP file created on disk

**2b. Open with reset**

```
open_image(raw_path="...", reset=True)
```

Expected: same as above but any existing history is cleared.

**2c. Re-open without reset**

Open same file twice without reset. Second call should return the same session_id and NOT clear history.

**Fix — session not found after /mcp restart:**
Sessions are in-memory only. Always call `open_image` again after restarting the MCP server.

---

### 3. Baseline Render

```
open_image(reset=True)
render_preview(width=1280)
```

**Expected:** A correctly exposed, colour-accurate JPEG of the RAW.

**If render hangs / times out:**

| Symptom | Cause | Fix |
|---|---|---|
| Timeout after exactly 120s, first run ever | `stdin` not redirected — darktable-cli blocks reading from inherited stdin | Add `stdin=subprocess.DEVNULL` to `subprocess.run` in `renderer/darktable_cli.py` |
| Two concurrent darktable-cli processes | Prewarm thread + explicit render racing | Remove or join prewarm thread before new render (see `session.py:_prewarm`) |
| Slow but completes after 60–90s | OpenCL driver hang on init | Add `--conf plugins/opencl/opencl=FALSE` to cmd in `darktable_cli.py` |

**If render fails with exit 0 but file not found:**

Darktable-cli may have written the file with an `_01` suffix (avoids overwriting). Cause: stale file at the target path from a previous failed run.

Fix: clear `.dtmcp/preview/` and `.dtmcp/preview_cache/` between runs, OR ensure unique output paths (cache key already does this if the old file was truly deleted).

**If render fails with garbled output path (backslashes stripped):**

darktable-cli on Windows strips backslashes from arguments passed as `str(Path)`.

Fix: use `path.as_posix()` for all paths passed to darktable-cli in `renderer/darktable_cli.py`.

---

### 4. Exposure Module

```
set_module(operation="exposure", params={"exposure": -1.0})
render_preview(width=1280)
```

**Expected:** Image noticeably darker than baseline.

**If image is pure white (even at small positive values like 0.2):**

The RAW file may already be bright (e.g. Sony auto-exposure pushes highlights near clipping).
Test with negative values first. If negative values render correctly, the exposure codec is fine —
you just need to work in the negative range for this particular image.

Verify codec correctness by decoding the params back:
```
get_module_params(session_id, "exposure")
# should return {"exposure": -1.0, "black": 0, ...}
```

**If image is black at any exposure value:**

Unrelated to exposure codec. Check if `colorbalancergb` or another module in history is
producing black output (see section 5 below).

---

### 5. colorbalancergb Module

```
set_module(operation="colorbalancergb", params={"vibrance": 0.2, "saturation": 0.08})
render_preview(width=1280)
```

**Expected:** Slightly more vivid colours vs baseline, no other change.

**If image is black:**

Most likely cause: the encoded struct is too short. darktable's colorbalancergb v5 struct is
**180 bytes** (44 floats + 1 uint32). The codec packs the 26 known fields into 104 bytes.
If the remaining 76 bytes are missing, darktable reads garbage for the v5 additions and
produces black output.

Fix in `codecs/colorbalancergb.py` → `encode()`:
```python
elif len(data) < 180:
    data += b'\x00' * (180 - len(data))
```

This pads the fresh-encoded struct to the expected size with neutral zeros.

**Roundtrip check:** Decode the module after setting it and verify your values appear in the output. Values you did not set should be 0 (or the defaults):
```
get_module_params(session_id, "colorbalancergb")
# {"vibrance": 0.2, "saturation": 0.08, "chroma": 0.0, ...}
```

**Important parameter naming:**

| Intended effect | Field name |
|---|---|
| Global vibrance | `vibrance` |
| Global saturation | `saturation` |
| Global chroma | `chroma` |
| Per-zone chroma | `shadows_C`, `midtones_C`, `highlights_C` |
| Per-zone luma | `shadows_Y`, `midtones_Y`, `highlights_Y` |
| Per-zone hue shift | `shadows_H`, `midtones_H`, `highlights_H` |
| Zone range weights | `shadows_weight`, `midtones_weight`, `highlights_weight` |

Note: `contrast`, `global_saturation`, `global_vibrance` are NOT valid field names —
these will silently be ignored and default to 0.

---

### 6. Temperature Module

```
set_module(operation="temperature", params={"temperature": 5500, "tint": 1.0})
render_preview(width=1280)
```

**Expected:** Cooler/warmer colour cast vs baseline.

No known encoding issues with temperature. If the render looks wrong, check
`codecs/temperature.py` for coefficient calculation bugs.

---

### 7. Multiple Modules Stacked

```
open_image(reset=True)
set_module("exposure", {"exposure": -0.8})
set_module("colorbalancergb", {"vibrance": 0.25, "saturation": 0.1})
render_preview()
```

**Expected:** Darker image with enhanced colours.

Watch for `history_end` value after each `set_module` call:
- After first: `history_end=1`
- After second: `history_end=2`

If `history_end` jumps unexpectedly (e.g. goes to 2 when re-setting an already-present module),
there may be a bug in `xmp/history.py:upsert_module` — it should update in-place, not append.

---

### 8. Undo / Redo

```
set_module("exposure", {"exposure": -0.5})   # history_end=1
set_module("colorbalancergb", {...})          # history_end=2
undo(steps=1)                                # history_end=1
render_preview()                             # should show only exposure applied
redo(steps=1)                                # history_end=2
render_preview()                             # both modules applied again
```

**Expected:** Undo/redo change `history_end`, render reflects active module count.

**Fix if undo has no visible effect:** Check that `set_history_end` in `xmp/history.py` saves
the XMP and that `darktable-cli` is reading the updated `history_end` attribute correctly.

---

### 9. Snapshots and Compare

```
open_image(reset=True)
render_preview()                                      # baseline
snapshot(label="baseline")
set_module("colorbalancergb", {"vibrance": 0.3})
snapshot(label="vivid")
compare(label_a="baseline", label_b="vivid", mode="split")
```

**Expected:** A split composite JPEG with the two states side-by-side.

**Fix if snapshot not found:** Check that `SnapshotManager.path()` returns the correct path
and that the `.dtmcp/snapshots/` directory was created.

---

### 10. Export Final

```
export_final(session_id, output_path="C:/tmp/out.jpg", format="jpg", width=0)
```

**Expected:** Full-resolution JPEG at the specified path. `size_bytes` in the response
should be several megabytes for a full-res Sony ARW.

**Watch for path issues:** Same as render — ensure `as_posix()` is used for all paths
passed to darktable-cli. `export_final` uses `hq=True` so expect a slower render (30–90s).

---

### 11. Cache Behaviour

Run `render_preview` twice with identical XMP state:

```
render_preview(width=1280)  # → renders via darktable-cli (slow)
render_preview(width=1280)  # → returns cached JPEG instantly
```

**Expected:** Second call completes in milliseconds.

Cache key is `sha256(xmp_bytes + width + dt_version)[:24]`. If cache is not hitting,
check `renderer/cache.py:key()` — ensure the same XMP bytes produce the same hash.

**Cache invalidation:** Any `set_module`, `undo`, or `redo` changes the XMP → new cache key →
forces a fresh render. This is by design.

**Clearing cache manually:**
```bash
find .dtmcp -name "*.jpg" -delete
```

---

## Common Failure Modes — Quick Reference

| Symptom | Most Likely Cause | Fix Location |
|---|---|---|
| render_preview hangs 120s | `stdin` inherited by darktable-cli | `renderer/darktable_cli.py` → add `stdin=DEVNULL` |
| Black image after any edit | colorbalancergb struct too short (104 vs 180 bytes) | `codecs/colorbalancergb.py` → pad to 180 bytes |
| White image at small positive EV | RAW already near clipping; no issue with codec | Use negative EV values |
| File not found (exit 0) | Path backslashes stripped by darktable-cli on Windows | Use `path.as_posix()` for all paths |
| `_01` suffix on output file | Stale file exists at target path | Clear `.dtmcp/preview/` before test run |
| Session not found | Server restarted; session is in-memory only | Call `open_image` again |
| `history_end` wrong after upsert | Bug in `xmp/history.py:upsert_module` | Verify it finds existing entry by operation name before inserting |

---

## Running the Existing Test Suite

```bash
cd Darktable_MCP_tool
uv run pytest tests/ -v
```

Tests cover codec roundtrip only (encode → decode → verify values).
They do not invoke darktable-cli. Add integration tests using a known small RAW
fixture (e.g. a 1MP DNG) that can render in under 10 seconds for CI use.
