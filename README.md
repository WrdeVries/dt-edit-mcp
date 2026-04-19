# dt-edit-mcp

Darktable headless RAW editor via MCP. Enables an LLM agent to apply parametric edits to RAW files by reading/writing Darktable XMP sidecars and rendering JPEG previews via `darktable-cli`.

## Requirements

- Python 3.12+
- darktable 4.6.x installed at `C:\Program Files\darktable\`
- Windows 11 (primary target; Linux works with minor path changes)

## Install

```bash
py -3.12 -m pip install -e .
```

## Wire into Claude Code

The `.mcp.json` in this directory registers the server. Open this project in Claude Code and it will pick up the MCP server automatically.

**Edit `DTMCP_ROOT`** in `.mcp.json` to the folder containing your RAW files (default: `C:\Users\wiebe\Pictures`).

If darktable is not in the default path, set `DARKTABLE_CLI` in the env block:

```json
"env": {
  "DARKTABLE_CLI": "C:\\path\\to\\darktable-cli.exe",
  "DTMCP_ROOT": "C:\\Users\\wiebe\\Pictures"
}
```

## Available tools

| Tool | Description |
|---|---|
| `open_image` | Open a RAW file, load/create its XMP sidecar |
| `get_history` | Show the full edit history with decoded params |
| `get_module_params` | Get current params for a specific module |
| `list_supported_modules` | List modules with full codec support |
| `set_module` | Apply/update a module with params and optional mask |
| `disable_module` | Disable a module without removing it |
| `undo` / `redo` | Move the history cursor |
| `reset_all` | Clear all edits |
| `snapshot` | Save current state under a label |
| `restore_snapshot` | Restore a saved state |
| `list_snapshots` | List saved snapshots |
| `render_preview` | Render a JPEG preview (cached) |
| `export_final` | Full-resolution export |
| `compare` | Side-by-side or split-wipe comparison of two snapshots |
| `open_in_viewer` | Open HTML comparison in browser |
| `analyze_reference` | LAB + palette analysis of a reference image |
| `close_image` | Close a session |

## Supported modules (MVP)

- `exposure` (v7) — EV and black level
- `temperature` (v3) — white balance, tint, illuminant
- `colorbalancergb` (v5) — shadows/midtones/highlights color wheels, saturation, vibrance

All other modules load as opaque passthrough (can toggle enabled/disabled).

## Example agent workflow

```
open_image("C:/Pictures/DSC_1234.NEF")
→ snapshot("before")
→ set_module("exposure", {"exposure": 0.5})
→ set_module("colorbalancergb", {"shadows": {"Y": 0.03, "C": 0.02, "H": 200}})
→ snapshot("lifted_shadows")
→ compare("before", "lifted_shadows", mode="split")
→ render_preview()          # agent sees the result inline
→ export_final("C:/output/DSC_1234.jpg")
```

## Phase 0 validation spike

Before editing your real images, run the roundtrip spike to confirm codec correctness:

1. Open a RAW in Darktable, set exposure to +0.50, save and close.
2. Point `get_module_params` at the XMP and verify `exposure ≈ 0.50`.
3. Run `render_preview` and compare visually to the Darktable-rendered version.

## Tests

```bash
py -3.12 -m pytest tests/ -v
```
