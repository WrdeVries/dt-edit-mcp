# Darktable Editor MCP — Implementation Plan

**Working title:** `dt-edit-mcp`
**Target platform:** Windows 11 (primary), cross-platform compatible
**Target Darktable version:** 4.6.1 (installed at `C:\Program Files\darktable\`)
**Author goal:** Enable an interactive coding-agent workflow where a user points to a RAW file with editing intent (optionally a reference image), and the agent performs incremental, human-in-the-loop edits with side-by-side comparisons at every step.

---

## 1. Executive Summary

We will build an MCP server that exposes Darktable's non-destructive, parametric RAW editing pipeline to an LLM agent. The agent does not manipulate Darktable's GUI; instead, it **reads and writes Darktable's XMP sidecar files directly**, then invokes `darktable-cli.exe` to render JPEG previews that the agent shows to the user for approval. This gives headless, fully scriptable access to the *complete* Darktable pipeline — not just the CLI's export subset.

The core insight: **`darktable-cli` + an XMP sidecar is mathematically equivalent to opening the image in the darkroom with that edit history loaded.** Every module, every parameter, every mask — all live in the XMP file as a history stack. If we can write a valid XMP, we can drive the full feature set.

---

## 2. Problem Statement & Desired UX

### 2.1 User flow
1. User: *"Edit `DSC_1234.NEF` — I want warm, cinematic color grading like the reference `teal_orange.jpg`. Lift shadows a bit and keep skin tones natural."*
2. Agent produces **Step 1** (e.g. exposure + white balance), renders before/after, shows user.
3. User: *"Good, but a touch too warm."*
4. Agent adjusts, re-renders, shows diff.
5. Loop until user approves each discrete step, then moves to next (tone, color grading, local contrast, …).
6. Final: agent writes an approved XMP + optionally exports a full-resolution JPEG/TIFF.

### 2.2 Requirements this implies
- **Stepwise edit application** (not "one-shot export").
- **Low-latency preview rendering** (sub-10s round-trip for a 1280 px preview).
- **Deterministic, reversible state** — every step must be undo-able and restorable.
- **Side-by-side rendering** the agent/user can actually view.
- **Reference-image color guidance** when supplied.
- **Full module coverage over time** (MVP can target ~10 key modules).

---

## 3. Research Findings (What's Possible)

### 3.1 `darktable-cli` — what we can drive
Confirmed from `darktable-cli.exe --help` (v4.6.1) and official docs:
- Reads a RAW + optional XMP → writes any supported format (JPG, PNG, TIFF, WebP, EXR, JXL, PDF).
- All processing modules, masks, presets run exactly as in the GUI pipeline.
- Flags: `--width`, `--height`, `--hq`, `--upscale`, `--style`, `--style-overwrite`, `--apply-custom-presets`, `--icc-type`, `--icc-file`, `--icc-intent`, `--out-ext`, `--export_masks`.
- Format-specific options via `--core --conf plugins/imageio/format/jpeg/quality=85` etc.
- **No display required** — pure console mode. Can render on a headless machine.
- **Limitations:** no interactive edit manipulation, no thumbnail cache generation (use `darktable-generate-cache.exe` separately if needed), no library DB mutations.

### 3.2 XMP sidecar format (the key)
Confirmed by reading `src/common/exif.cc` references and the darktable-released `dtstyle_to_xmp.py` tool:

```xml
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0-Exiv2">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
        xmlns:xmp="http://ns.adobe.com/xap/1.0/"
        xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
        xmlns:dc="http://purl.org/dc/elements/1.1/"
        xmlns:darktable="http://darktable.sf.net/"
        darktable:xmp_version="5"
        darktable:raw_params="0"
        darktable:auto_presets_applied="1"
        darktable:history_end="3"
        darktable:iop_order_version="2">

      <darktable:history>
        <rdf:Seq>
          <rdf:li
            darktable:num="0"
            darktable:operation="exposure"
            darktable:enabled="1"
            darktable:modversion="7"
            darktable:params="0000000040a0093bd8ce374000004842000080c0000001"
            darktable:multi_name=""
            darktable:multi_priority="0"
            darktable:blendop_version="11"
            darktable:blendop_params="gz12eJxjYGBgkGAAgRNODESDBnsIHll8ANNSGQM="/>
          <!-- more <rdf:li> entries -->
        </rdf:Seq>
      </darktable:history>

      <darktable:masks_history>
        <rdf:Seq/>
      </darktable:masks_history>

      <darktable:iop_order_list>...</darktable:iop_order_list>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
```

**Per-entry attributes that matter to us:**
| Attribute | Meaning |
|---|---|
| `darktable:num` | History index (0-based) |
| `darktable:operation` | Module name (e.g. `exposure`, `colorbalancergb`, `filmicrgb`) |
| `darktable:enabled` | `1` or `0` |
| `darktable:modversion` | Schema version for `params` |
| `darktable:params` | **Packed C-struct → hex string** (or `gz##<base64>` when gzipped) |
| `darktable:blendop_version` | Schema version for blend params |
| `darktable:blendop_params` | Blend/mask params (typically gzipped) |
| `darktable:multi_name` / `multi_priority` | Multi-instance support |

**Top-level attributes that matter:**
- `darktable:xmp_version` — bumps when darktable's XMP schema changes (currently 5 for 4.6.x). We must track this.
- `darktable:history_end` — where playback stops (allows "soft undo" — entries beyond this index are kept but inactive).
- `darktable:iop_order_version` / `iop_order_list` — module execution order.
- `auto_presets_applied` — set to 1 after first load so darktable doesn't re-apply its defaults.

### 3.3 Param encoding — the format we must implement
Two forms:
1. **Plain hex:** bytes of a packed C struct, concatenated as lowercase hex. Example for `exposure` (7 fields):
   - `uint32 mode`, `float black`, `float exposure`, `float deflicker_percentile`, `float deflicker_target_level`, `gboolean(int32) compensate_exposure_bias`, `gboolean(int32) compensate_hilite_pres`
   - Total: 4 + 4*4 + 4 + 4 = 28 bytes → 56 hex chars.
2. **Gzipped:** `gz##` + base64, where `##` is two hex digits indicating zlib compression parameters. Used when the raw param block is large (typical for `blendop_params`, `colorbalancergb` with 30+ floats, mask data, etc.).

**Implementation:** a Python `ModuleCodec` layer. For each supported `(operation, modversion)` tuple, define a `struct.pack`/`unpack` schema. Handle:
- Endianness: native little-endian on x86_64.
- `gboolean` is a 4-byte int (GLib).
- C enum types = 4-byte int.
- Struct alignment/padding: darktable uses `#pragma pack` equivalents in some modules — need to verify per module, but most are naturally aligned with 4/8-byte fields.

### 3.4 Styles (`.dtstyle` files)
Same info as XMP history entries, wrapped in `<darktable_style><style><plugin>…`. A style applied via `--style NAME` appends those entries to the image's history (or overwrites with `--style-overwrite`). **Useful for us:** we can materialize agent-proposed edits as styles, save them, and re-apply them to other images — but it's not required for the core MVP.

### 3.5 Lua API — not the right tool
Confirmed the Darktable Lua API has **no low-level edit manipulation**. It controls metadata, collections, tags, the GUI, and export — but cannot create or mutate history entries. It also has no headless mode and no socket IPC. Abandoning Lua as an integration path.

### 3.6 Reference-image color matching
The primary lever is the **vision-capable agent itself**: it can see the reference and propose module params semantically ("warm shadows ~3500K, teal highlights, lifted blacks"). That respects user intent and doesn't drag skin tones the way global statistics do.

Three approaches, in priority order:
1. **LLM-heuristic (primary, MVP and beyond)**: agent describes the grade and sets `colorbalancergb` / `temperature` / `filmicrgb` params directly. Enhanced by `analyze_reference` returning a compact, quantitative summary (dominant hues via k-means, LAB means per tone region, rough contrast level) so the agent reasons over numbers instead of vibes.
2. **Hald-CLUT + `lut3d` module (phase 3)**: when the reference is content-matched (same scene, same subject), generate a PNG hald-CLUT from the (neutral-processed RAW → reference) mapping, write it into `{configdir}/lut/`, and apply via `lut3d`. Principled and reversible.
3. **Reinhard LAB transfer (demoted — optional hint only)**: global LAB mean/std shift. Included only as a seed suggestion the agent may consult and override; *never* applied blind, because it overcorrects on mismatched compositions and shifts skin tones. Work in linear RGB before converting to LAB to avoid washed-out results.

MVP delivers (1) with a strong `analyze_reference`. Phase 3 adds (2). (3) is a 50-line helper, not a milestone.

### 3.7 Module-param schemas — the long tail
Every module has its own struct, often with multiple versions. Hand-curating these is the single biggest ongoing cost of this project, and we plan to minimize that cost rather than absorb it.

**Primary strategy: introspection-driven codec generation.** Darktable emits `DT_MODULE_INTROSPECTION(<version>, <struct>)` macros in `src/iop/*.c` and generates runtime field/offset/type tables via `tools/introspection_*`. We have two routes, in priority order:

1. **Parse introspection at build time.** Clone the pinned darktable source for the target version, run its introspection generator, and emit a JSON schema file per `(operation, modversion)` that our codec layer consumes. One generator job produces codecs for *every* module, not ten.
2. **Link `libdarktable` at runtime.** Call into the introspection API directly to decode/encode. Heavier dependency; keep as fallback if the build-time parse misses a module.

**Hand-written fallback** (the current list below) exists only to unblock spike/MVP while the generator is being built. The moment the generator lands, hand-written codecs become roundtrip fixtures against it.

Cross-reference with the JS `darkroom-xmp-tools` library for validation where it overlaps.

**Spike/MVP hand-written module set (priority order):**
| Module (`operation`) | Purpose | Notes |
|---|---|---|
| `temperature` | White balance | 4 floats (coeffs for R,G,B,G2) |
| `exposure` | EV + black level | v7, ~28 bytes |
| `colorbalancergb` | Primary color grading | v5, 30+ floats — **gzipped** |
| `filmicrgb` | Tone mapping | Complex; gzipped |
| `toneequal` | Tone equalizer (zone-based tone) | Gzipped |
| `sharpen` / `diffuse` | Sharpening | |
| `denoiseprofile` | Profiled denoise | Gzipped |
| `colorzones` | Hue/sat/lum per-hue | Gzipped |
| `channelmixerrgb` | Color calibration / WB | Gzipped |

All other modules are treated as **opaque passthrough** (copy bytes unchanged if we inherit them from an existing XMP) until the generator covers them. Cropping is explicitly **out of scope** — users can crop trivially in the GUI or downstream, and it adds IOP-ordering and geometry complexity for no agent-workflow benefit.

**Struct layout discipline.** Don't assume "naturally aligned." For every schema, emit explicit `struct` format strings with padding bytes where needed, and treat the roundtrip test (§11.1) as the source of truth. Enums are compiler-dependent; `gboolean` is 4 bytes on GLib but verify per fixture.

### 3.8 MCP SDK choice
Use **FastMCP** (Python, `pip install fastmcp`):
- `@mcp.tool` decorator for tools.
- `fastmcp.utilities.types.Image` for returning preview PNG/JPEGs inline to the agent.
- Class-based server for shared state (open-image session, preview cache).
- Structured dataclass returns for params/state queries.

---

## 4. Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                       Coding agent (LLM)                       │
│                                                                │
│  observes previews, proposes edits, asks user for feedback     │
└───────────────────────────────┬────────────────────────────────┘
                                │  MCP (stdio)
                                ▼
┌────────────────────────────────────────────────────────────────┐
│                    dt-edit-mcp (FastMCP server)                │
│                                                                │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────────────┐   │
│  │ Session     │   │ XMP engine  │   │ Renderer            │   │
│  │ manager     │◄──┤  read/write │   │  darktable-cli.exe  │   │
│  │ (image IDs, │   │  history    │◄──┤  preview & export   │   │
│  │  snapshots) │   │  stack ops  │   │                     │   │
│  └─────┬───────┘   └─────┬───────┘   └─────────┬───────────┘   │
│        │                 │                     │               │
│  ┌─────▼───────┐   ┌─────▼───────┐   ┌─────────▼───────────┐   │
│  │ Module      │   │ Codec       │   │ Compare viewer      │   │
│  │ registry    │   │ (struct     │   │  HTML side-by-side, │   │
│  │ (schemas)   │   │   pack/gz)  │   │  composite image    │   │
│  └─────────────┘   └─────────────┘   └─────────────────────┘   │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Color-match helper (LAB stats, hald-CLUT, later)         │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
                                │
                                ▼
            ┌───────────────────────────────────────┐
            │  Filesystem                            │
            │  • RAW file (read-only)                │
            │  • ./{raw}.xmp (current state)         │
            │  • ./.dtmcp/snapshots/*.xmp (history)  │
            │  • ./.dtmcp/preview/*.jpg              │
            │  • ./.dtmcp/compare_*.html             │
            └───────────────────────────────────────┘
```

### 4.1 Non-destructive discipline
- **Never touch the RAW.** Ever.
- The "live" XMP lives next to the RAW (standard darktable convention) so the user can open in Darktable GUI at any point and see the same state.
- Additional history — snapshots, agent-only experiments, per-step backups — lives under a `.dtmcp/` subfolder that we create and own.

### 4.2 Session model
Each call to `open_image(path)` returns a `session_id`. The session stores:
- RAW path, XMP path, current history length.
- Named snapshots (`before`, `after`, `step_1`, …).
- Cached preview files keyed by `(snapshot_id, width)`.

Sessions are held in-process (dict); on server restart, sessions are lost but the XMP on disk survives, so the user can reopen.

---

## 5. MCP Tool Surface

### 5.1 Lifecycle
- `open_image(raw_path: str, reset: bool = false) -> SessionInfo`
  Load (or create) XMP. If `reset`, start from a neutral XMP with only the auto-applied presets disabled. Return current history summary.
- `close_image(session_id: str) -> None`

### 5.2 Inspection
- `get_history(session_id) -> list[HistoryEntry]`
  Each entry: `{num, operation, enabled, modversion, params_decoded?, multi_name}`.
- `get_module_params(session_id, operation: str, instance: int = 0) -> dict`
  Decoded param dict for a specific module's current entry; `null` if module not present.
- `list_supported_modules() -> list[ModuleSchema]`
  Which operations we can decode/encode vs. pass through opaquely.

### 5.3 Editing (the core)
- `set_module(session_id, operation, params: dict, enabled: bool = true, instance: int = 0, blend: dict | None = None) -> HistoryEntry`
  Encode `params` to hex (gzipped if needed), place the entry at the correct `iop_order_list` position if new, bump `history_end`. If `history_end < len(history)` at call time, **truncate forward history** (text-editor-style undo semantics). `blend` accepts opacity, blend mode, and parametric luminance/chrominance mask bounds; encoded via `blendop_params`.
- `disable_module(session_id, operation, instance: int = 0)`
- `undo(session_id, steps: int = 1)` — decrement `history_end` (does not delete entries).
- `redo(session_id, steps: int = 1)` — re-increment, bounded by `len(history)`.
- `reset_all(session_id)` — set `history_end=0`.
- `apply_style(session_id, style_path_or_name: str, overwrite: bool = false)` — parse `.dtstyle`, append entries. Community-curated styles are a first-class agent move.

### 5.4 Snapshots (for comparison)
- `snapshot(session_id, label: str) -> SnapshotId`
  Copy current `{raw}.xmp` to `.dtmcp/snapshots/{label}.xmp`.
- `restore_snapshot(session_id, label)` — overwrite live XMP.
- `list_snapshots(session_id) -> list[str]`

### 5.5 Rendering
- `render_preview(session_id, width: int = 1280, snapshot: str = None) -> Image`
  Invoke `darktable-cli RAW XMP OUT.jpg --width W --apply-custom-presets 0 --hq 0 --core --conf plugins/imageio/format/jpeg/quality=85`.
  Cache by hash of `(xmp_contents, width)`. Return `Image` to the agent.
- `export_final(session_id, output_path: str, format: str = "jpg", width: int = 0, icc_type: str = "SRGB", icc_file: str | None = None, icc_intent: str = "perceptual", format_opts: dict | None = None) -> str`
  Full-resolution, high-quality export. `format_opts` carries format-specific knobs: `{quality}` for JPEG/WebP, `{bit_depth, compression}` for TIFF/PNG, `{effort, distance}` for JXL, etc. Invalid keys for the chosen format are rejected, not ignored.

### 5.6 Compare UX
- `compare(session_id, label_a: str, label_b: str, mode: str = "side_by_side") -> CompareResult`
  Modes: `side_by_side`, `split` (wipe-left/right composite), `stack` (top/bottom), `animated` (GIF ping-pong).
  Generates a composite JPEG + an HTML page with a slider. Returns both the composite `Image` (shown inline to the agent) and an HTML `file://` URL the user can open locally.
- `open_in_viewer(path)` — OS-native open (Windows: `explorer.exe`, falls back to `start`).

### 5.7 Reference-guided (phase 2)
- `analyze_reference(ref_path: str) -> dict`
  Compute LAB mean/std, dominant hues (k-means), rough tone histogram. Returns a human-readable summary the agent can reason about.
- `suggest_grade(session_id, ref_path: str) -> dict`
  Runs a neutral preview of the RAW, computes LAB stats, returns a **proposed** dict of `{exposure, temperature, colorbalancergb: {…}}` params. Agent applies (or modifies) and then renders.

---

## 6. Rendering Strategy & Performance

### 6.1 Preview pipeline
```
darktable-cli.exe
  "C:\path\DSC_1234.NEF"         # input RAW
  "C:\path\.dtmcp\tmp_XXX.xmp"   # current state
  "C:\path\.dtmcp\preview\xxx.jpg"
  --width 1280 --height 1280
  --hq 0                         # lower quality = faster
  --apply-custom-presets 0       # don't re-apply user's default presets
  --core
    --conf plugins/imageio/format/jpeg/quality=85
    --configdir "C:\Users\wiebe\AppData\Roaming\darktable"
```

### 6.2 Speed expectations
Target Windows 11, darktable-cli 4.6.1. Budget **realistically**, not aspirationally:
- Cold start of `darktable-cli` on Windows: ~2–4 s (library loading, DLL resolution; worse if Defender scans).
- 24 MP Nikon/Fuji RAW → 1280 px preview at `--hq 0`: **6–10 s cold is common** on Windows; OpenCL often fails to initialize silently. OpenCL-primed Linux benchmarks of "2–5 s" don't transfer.
- Design the UX around the 10 s ceiling: streaming "rendering…" status, not a silent hang.

**Optimization strategies (ordered, implement early):**
1. **Pre-warm on `open_image`** — kick off a neutral preview in a background thread immediately so the first `render_preview` call is often a cache hit.
2. **Preview cache** keyed on `sha256(xmp_contents) + width + dt_cli_version + configdir_hash`. Version/config inclusion prevents stale hits after DT upgrades.
3. **Parallel A/B rendering** for `compare` — two `darktable-cli` processes in parallel; ~2× wall-clock win on multi-core.
4. **`--cachedir` + `darktable-generate-cache`** — pre-generate the pixel-pipe cache for the RAW once, so repeated renders reuse demosaic/input-color output. Spike this before MVP; if it works, it's the biggest single win.
5. **OpenCL diagnostics** — on first `open_image`, run `darktable-cli --version` with verbose flags, log whether OpenCL initialized; surface to the user if it didn't (Windows OpenCL install is finicky).
6. Long-lived `darktable-cli` via stdin: still not supported — abandon.
7. Small-proxy TIFF base: rejected, breaks RAW-sensitive modules.

### 6.3 Full export
Run with `--hq 1`, native width, higher JPEG quality, proper ICC profile. Minutes-scale for 24+ MP is fine — user only exports once.

---

## 7. Side-by-Side Comparison Viewer

### 7.1 Composite image (inline to agent)
- Render both snapshots at ≤ 1280 px.
- Use PIL to paste into a single JPEG: labeled A | divider | B.
- Return as `Image` — the agent can "see" the diff directly.

### 7.2 HTML viewer (for the user)
- Write a self-contained HTML file with:
  - Before/after <img> layers and a CSS-clip slider (standard image-compare UI — no external JS).
  - "Side-by-side", "split", and "toggle" view modes via a dropdown.
  - Metadata panel: what changed between A and B (diff the decoded histories).
- Open with `os.startfile(html_path)` on Windows — user's default browser handles it.

### 7.3 Why HTML, not a custom GUI?
Zero install, cross-platform, the user already has a browser, and it's easy to iterate on. A Tk/Qt viewer adds deps for no real win.

---

## 8. Safety, Robustness, Correctness

### 8.1 XMP write safety
- Always write to `.xmp.tmp` then atomic-rename. Avoids corruption if the server crashes mid-write.
- Keep a **rolling backup** (`.xmp.bak.{N}`, last 10) so the user can manually recover.
- Before any write, validate: XML parses, all required namespaces present, history entries have all mandatory attributes.
- Never delete `.xmp` files.

### 8.2 Param encoding correctness
- For each module schema, ship a **roundtrip test**: decode sample XMP → re-encode → byte-identical.
- Seed test fixtures from actual Darktable GUI edits (user edits an image in GUI, commits the resulting XMP to the test suite; we verify our decoder reproduces every byte).
- Modules we don't know: pass the `params` attribute through unchanged; agent can toggle `enabled` but can't modify.

### 8.3 Version skew
- Current target: Darktable 4.6, `xmp_version=5`, and specific `modversion` per module.
- If we encounter an unknown `modversion`, treat as opaque (copy-through).
- Log module+version coverage so we can prioritize adding schemas.

### 8.4 The `history_end` footgun
Darktable keeps inactive entries beyond `history_end`. Beware: appending a new entry at the end with a stale `history_end` will cause the GUI to "compress" history next time it opens the image, discarding the orphan entries. Fix: every write sets `history_end = len(history)`.

### 8.5 Darktable open simultaneously
If the user has Darktable GUI open on the same image, our XMP writes will conflict with the DB. Plan:
- Document that the GUI must be closed on the image we're editing (or advise using a file not yet imported into Darktable).
- On `open_image`, warn if the GUI process is running (`tasklist /FI "IMAGENAME eq darktable.exe"` on Windows).
- Phase 2: support a "detach" mode that copies the RAW to a temp working dir so there's no DB entanglement at all.

### 8.6 Library DB divergence (GUI closed but image previously imported)
Even with the GUI closed, if the image was ever imported into Darktable's library, the DB holds its own `history_end` and module stack. Our XMP writes bypass the DB; next GUI open, DT detects divergence and prompts the user to pick (load from XMP vs. keep DB state). This is expected behavior, not a bug — but it needs explicit user-facing documentation ("click 'load from XMP' to see our edits in the GUI"). On `open_image`, detect whether the image is in the DB (scan `~/AppData/Local/darktable/library.db` read-only for the RAW path) and surface the reload-dialog warning proactively.

### 8.7 OneDrive / synced-folder hazard
`Documents\` on Windows 11 is OneDrive-synced by default. The sync client holds transient locks on files it's uploading, which can make `os.replace` raise `PermissionError` (WinError 32) during atomic XMP writes. Mitigations:
- On `open_image`, detect OneDrive path (look for `OneDrive` in the resolved path) and warn the user; recommend a non-synced working folder.
- Wrap atomic-rename in retry-with-exponential-backoff on WinError 32 (up to ~2 s total).
- For the `.dtmcp/` subfolder, prefer `%LOCALAPPDATA%\dt-edit-mcp\<project-hash>\` over a synced path.

### 8.8 IOP ordering on new-module insertion
`set_module` for a module **not present in the current `iop_order_list`** must place it at the correct pipeline position or the render will be visually wrong while passing every byte-level test. Strategy:
- Ship a version-pinned default `iop_order_list` per target DT version (extracted from a neutral-XMP fixture).
- When inserting a new module, look up its default position in that list and splice it in.
- Roundtrip test isn't sufficient here — add a **render-equivalence test** comparing our inserted-from-scratch history against the same module inserted via the GUI.

### 8.9 Path sandboxing
The MCP server receives `raw_path`, `output_path`, `ref_path`, and style names from the agent. Pin all filesystem inputs to a user-declared project root at server startup; reject paths that resolve outside it (after `os.path.realpath`). Reject UNC paths and symlinks unless explicitly enabled. This is cheap and closes an obvious class of prompt-injection footguns.

---

## 9. Implementation Phases

Timeline is **honest, not aggressive**. Windows-target work, introspection tooling, and render-equivalence tests all take longer than they look.

### Phase 0 — spike (3–5 days)
- Verify XMP roundtrip on a real RAW: open in GUI, edit exposure + WB, close, parse the XMP, re-encode exposure params, write back, render with `darktable-cli`, confirm visual identity (16-bit TIFF diff, max-abs ≤ 1 LSB).
- **Introspection generator spike**: can we parse `DT_MODULE_INTROSPECTION` out of pinned DT source and emit a usable schema for `exposure`? If yes, the whole long-tail problem collapses and Phase 1 uses generated codecs from day one.
- Measure real render latency on the user's Windows box; confirm the §6.2 budget.
- This *proves* the approach before any further build.

### Phase 1 — MVP (3–4 weeks)
- `open_image` (with OneDrive detection, library-DB check, path sandboxing), `get_history`, `get_module_params`, `set_module` for **exposure**, **temperature**, plus **`colorbalancergb`** (primary grading can't wait for Phase 2 — it's the whole point).
- **Multi-instance support** from day one (`instance` param honored end-to-end).
- **Minimal masks**: blend mode, opacity, and parametric luminance/chrominance masks via known-good `blendop_params` templates. No mask drawing yet.
- **IOP ordering** handled for new-module insertion with render-equivalence test.
- `snapshot`, `render_preview` (with cache keyed on xmp-hash + dt-version + configdir-hash, background pre-warm on open), `compare` (composite JPEG + HTML slider).
- `undo`/`redo`/`reset_all` with explicit semantics for "set_module when `history_end < len(history)`" (truncate-forward, like a text editor undo).
- Deterministic `session_id` from RAW-path hash so re-attach across server restarts works.
- FastMCP server wired into Claude Code.
- Acceptance: agent opens a NEF, applies a three-step grade with a luminance-masked shadow lift, compares, exports.

### Phase 2 — color grading breadth (2–3 weeks)
- Add `filmicrgb`, `toneequal`, `channelmixerrgb`, `denoiseprofile`, `colorzones` codecs (ideally all generated).
- `apply_style` (parse `.dtstyle`, append entries) — treat curated community styles as first-class agent moves.
- `export_final` with full ICC passthrough (`icc_type`, `icc_file`, `icc_intent`) and per-format options (bit depth, TIFF compression, JXL effort, JPEG quality).
- Compare viewer polish (toggle, split, metadata diff panel).
- "Detach" mode: copy RAW to a non-synced working dir, work there, optionally copy XMP back.

### Phase 3 — reference-guided grading (2 weeks)
- `analyze_reference`: quantitative summary (k-means palette, LAB means per tone region, contrast level) for LLM-heuristic path.
- Hald-CLUT generator for content-matched references + `lut3d` integration.
- Reinhard as optional hint only, clearly labeled.

### Phase 4 — long tail (ongoing)
- Drive remaining modules through the introspection generator; add hand-crafted codecs only where the generator fails.
- Mask authoring (drawn shapes, feathering) — hardest UX problem in the project.
- Library-DB read integration (tags/ratings for context). Cropping stays out of scope.

---

## 10. Repo Layout

```
Darktable CLI MCP/
├── darktable_editor_mcp.md        # this doc
├── pyproject.toml                 # fastmcp, lxml, pillow, numpy, scikit-image
├── src/
│   └── dt_edit_mcp/
│       ├── __init__.py
│       ├── server.py              # FastMCP entrypoint, tool definitions
│       ├── session.py             # SessionManager
│       ├── xmp/
│       │   ├── __init__.py
│       │   ├── parser.py          # lxml read/write of XMP
│       │   ├── history.py         # HistoryStack, add/remove/reorder
│       │   └── namespaces.py
│       ├── codecs/
│       │   ├── __init__.py        # ModuleRegistry (generated + hand-written)
│       │   ├── base.py            # ModuleCodec ABC, gzip helpers, hex utils
│       │   ├── introspection/     # generator output: JSON schema per (op, modversion)
│       │   ├── generator.py       # parses DT_MODULE_INTROSPECTION from pinned DT source
│       │   ├── blend.py           # blendop_params + parametric mask templates
│       │   ├── iop_order.py       # version-pinned default iop_order_list, insertion logic
│       │   ├── exposure.py        # hand-written fallback, v7
│       │   ├── temperature.py
│       │   └── colorbalancergb.py # hand-written fallback, v5
│       ├── renderer/
│       │   ├── __init__.py
│       │   ├── darktable_cli.py   # subprocess wrapper
│       │   └── cache.py           # hash-keyed preview cache
│       ├── compare/
│       │   ├── __init__.py
│       │   ├── composite.py       # PIL side-by-side
│       │   └── html_viewer.py     # template + slider
│       ├── colormatch/
│       │   ├── __init__.py
│       │   ├── reinhard.py        # LAB mean/std transfer
│       │   └── analyze.py         # reference palette/tone analysis
│       └── snapshots.py
├── tests/
│   ├── fixtures/                  # real RAW + XMP pairs from Darktable GUI
│   │   ├── sample_neutral.NEF.xmp
│   │   ├── sample_exposure_only.NEF.xmp
│   │   └── sample_full_edit.NEF.xmp
│   ├── test_xmp_roundtrip.py
│   ├── test_codec_exposure.py
│   ├── test_codec_colorbalancergb.py
│   ├── test_renderer.py           # smoke-test darktable-cli invocation
│   └── test_end_to_end.py         # open → edit → snapshot → compare → export
└── README.md                      # user-facing: install + MCP config snippet
```

---

## 11. Testing Strategy

### 11.1 Unit — codec roundtrip (critical)
For every module, a test matrix:
1. Load a fixture XMP exported by Darktable GUI with known params.
2. Decode → Python dict.
3. Re-encode → hex string.
4. Assert byte-identical to original.

Without this, silent corruption is guaranteed.

### 11.2 Integration — render equivalence
1. Decode fixture XMP, re-encode, write back.
2. Render both with `darktable-cli`.
3. Assert pixel-level equivalence (allow sub-1 LSB tolerance for JPEG encoding noise — actually render to 16-bit TIFF for the test and compare with `numpy`).

### 11.3 E2E — tool behavior
Script-driven MCP client test: open → set_module → snapshot → render → assert preview file exists & is non-black.

### 11.4 Manual — agent loop
Checklist of real editing scenarios ("warm up shadows", "reduce highlights", "match reference") run through Claude Code pointing at the MCP server. This is the only test that validates UX.

---

## 12. Risks & Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Param struct alignment differs from our assumption, silent corruption | **High** | Mandatory roundtrip tests per module; visual render-equivalence tests. |
| Darktable 4.7/5.0 bumps `modversion`, our hardcoded schemas break | Medium | Version-gate each codec; fall through to opaque passthrough on unknown version; log & prompt user to update. |
| `darktable-cli` render time too slow for tight iteration | Medium | Low-res `--hq 0` previews; preview cache; optional OpenCL; parallel render of A & B snapshots. |
| GUI open on same image → DB conflicts | Low | Detect running Darktable; warn; document; offer "work on a copy" mode. |
| gzipped `blendop_params` format (`gz##` + base64 + zlib) | Low | Well-understood; `##` is zlib compression level, body is base64-zlib. `darkroom-xmp-tools` is the ground-truth reference. |
| Hald-CLUT approach needs external file storage managed by darktable | Low (phase 3) | Write LUTs to known `{configdir}/lut/` subfolder; reference by filename in `lut3d` params. |
| XMP write races if agent calls multiple tools in parallel | Low | Per-session `threading.Lock` around all XMP mutations. |
| Large RAW + large preview = tool output too big for MCP channel | Low | Always resize preview to ≤ 1280 px, quality 85; JPEG is ~200–500 KB. |
| OneDrive sync locks XMP during atomic rename (WinError 32) | Medium | Detect synced path on `open_image`, warn; wrap `os.replace` in backoff retry; prefer `%LOCALAPPDATA%` for `.dtmcp/`. |
| IOP ordering wrong on new-module insertion → visually wrong render that passes roundtrip | **High** | Version-pinned default `iop_order_list`; render-equivalence test vs. GUI-inserted reference. |
| Path traversal from agent-supplied paths | Medium | Pin to user-declared project root at startup; reject paths outside after `realpath`. |
| Hand-written codec schemas rot on DT version bumps | Medium | Introspection-driven generator is the primary path; hand-written codecs are fallback fixtures. |

---

## 13. Open Questions

1. **Multi-instance modules (`multi_priority`).** Supported in MVP (`instance` param honored end-to-end). Open question: what's the right UX for the agent to enumerate existing instances and pick "add new" vs. "modify existing 1"?
2. **Mask authoring.** MVP supports blend mode, opacity, and parametric (luminance/chrominance) masks via templates. Drawn-shape masks (brush, ellipse, path) deferred to Phase 4 — their UX is the hardest part of the project.
3. **ICC handling for the reference image.** If the reference is in Display P3 or Adobe RGB, any LAB math on sRGB-assumed pixels drifts. Use `colour-science` or `skimage` with embedded-profile detection; work in linear before converting.
4. **Where does `darktable-cli.exe` live on the user's PATH?** Ship a config probe — look at `C:\Program Files\darktable\bin\darktable-cli.exe`, then `DARKTABLE_CLI` env var, then `which`. Cache the resolved path and DT version string for use in cache keys.
5. **Library DB integration.** Read-only in Phase 4 (tags/ratings for context). Write integration stays out of scope — bypass the DB, use sidecars.
6. **Fonts / localization.** `darktable-cli` respects `LANG`; params are locale-invariant binary so we're fine. Noted for completeness.
7. **Introspection generator: build-time or runtime?** Build-time parse produces clean JSON schemas in source control; runtime `libdarktable` binding is heavier but handles user-installed DT version natively. Spike both in Phase 0.

---

## 14. Concrete First-Session Build Checklist

Order matters — each step unblocks the next.

1. **Spike XMP roundtrip** on user's machine:
   - User opens a NEF in Darktable, sets exposure to +0.50, saves, closes.
   - We read the `.xmp`, parse, decode `exposure.params`, assert we see `exposure=0.50`.
   - Write back unchanged; diff the file — must be semantically identical.
2. **Render parity check**:
   - Render the original XMP and our rewritten XMP with `darktable-cli` → 16-bit TIFF at 1024 px → numpy-compare. Tolerance: `max abs diff <= 1` (LSB).
3. **Introspection generator spike**:
   - Pin the DT 4.6.1 source, parse `DT_MODULE_INTROSPECTION` for `exposure` + `colorbalancergb`, emit JSON schemas, consume them via a generic codec.
   - If this works, everything downstream uses generated codecs; hand-written ones become test fixtures.
4. **Measure Windows render latency** on user's box — confirm §6.2 budget; decide whether `darktable-generate-cache` pre-warming is worth it.
5. **Stand up minimal FastMCP server** with `open_image` + `render_preview` (with background pre-warm, cache, OneDrive detection, library-DB check, path sandboxing). Wire into Claude Code. Prove the agent can "see" the preview.
6. **Implement `set_module` / `snapshot` / `compare`** with multi-instance support, iop-order insertion, truncate-forward undo semantics.
7. **Add `colorbalancergb` + minimal mask support** (opacity, parametric luminance). Agent demo: "lift shadows with luminance mask, compare".
8. **Phase 2 color breadth** (`filmicrgb`, `toneequal`, `channelmixerrgb`, `denoiseprofile`, `colorzones`) via generator.
9. **Export** with full ICC + format-options passthrough.
10. **Reference analysis** (`analyze_reference`) for LLM-heuristic color-match loop.

---

## 15. Appendix A — Sample history entry (decoded exposure)

```python
# XMP attribute (hex string):
# darktable:params="000000000000000040a0093b0000484200000000010000000100000000"
# modversion=7, struct = <I f f f f i i>  (little-endian)

import struct, binascii
raw = binascii.unhexlify("000000000000000040a0093b0000484200000000010000000100000000")
mode, black, exposure, pct, tgt, comp_bias, comp_hp = struct.unpack("<Ifffiii", raw[:28])
# -> (0, 0.0, 0.502, 50.0, -4.0, 1, 1)  # mode=MANUAL, exposure=+0.5 EV
```

## 16. Appendix B — FastMCP server skeleton

```python
# src/dt_edit_mcp/server.py
from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from .session import SessionManager

mcp = FastMCP("dt-edit-mcp")
sessions = SessionManager()

@mcp.tool
def open_image(raw_path: str, reset: bool = False) -> dict:
    """Open a RAW file for editing. Creates/loads its XMP sidecar."""
    return sessions.open(raw_path, reset=reset).info()

@mcp.tool
def set_module(session_id: str, operation: str, params: dict,
               enabled: bool = True, instance: int = 0) -> dict:
    """Set parameters for a Darktable module, appending to history."""
    return sessions[session_id].set_module(operation, params, enabled, instance)

@mcp.tool
def render_preview(session_id: str, width: int = 1280,
                   snapshot: str | None = None) -> Image:
    """Render a JPEG preview via darktable-cli. Returns inline image."""
    path = sessions[session_id].render(width=width, snapshot=snapshot)
    return Image(path=path)

# ... etc
if __name__ == "__main__":
    mcp.run()
```

## 17. Appendix C — Key references

- darktable-cli docs: https://docs.darktable.org/usermanual/4.6/en/special-topics/program-invocation/darktable-cli/
- Sidecar files overview: https://docs.darktable.org/usermanual/4.6/en/overview/sidecar-files/sidecar/
- darktable source (XMP I/O): `src/common/exif.cc` on github.com/darktable-org/darktable
- Module source (param structs): `src/iop/*.c` — look for `DT_MODULE_INTROSPECTION(version, struct_t)` macros
- `dtstyle_to_xmp.py` (official, shows XMP structure): `tools/dtstyle_to_xmp.py` in the darktable repo
- `darkroom-xmp-tools` (JS reference decoders): https://github.com/wmakeev/darkroom-xmp-tools
- FastMCP docs: https://gofastmcp.com/servers/tools
- Reinhard color transfer (the classic paper): https://www.cs.tau.ac.il/~turkel/imagepapers/ColorTransfer.pdf
- Pixls forum XMP format discussion: https://discuss.pixls.us/t/xmp-files-darktable/36667
