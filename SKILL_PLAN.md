# Plan: Claude Code Skill for `dt-edit-mcp`

**Working title:** `darktable-editor`
**Purpose:** Package the empirical and architectural knowledge of `dt-edit-mcp` into a Claude Code skill so that any agent session with the MCP server attached can drive interactive RAW edits competently on the first shot, without re-discovering footguns.

---

## 1. Why a skill (vs. just the MCP server)

The MCP server exposes **what** is callable. The skill encodes **how** to use it well. Specifically:

- **Workflow shape** — open → snapshot baseline → edit → preview → snapshot/compare → export. Without guidance, agents tend to either over-edit in one step or forget to snapshot before experimenting.
- **Empirical footguns** — discovered across the current editing session (see §4), none of which are visible from the tool schemas alone.
- **Parameter intuition** — safe ranges for `colorbalancergb`, when `exposure` module is unsafe, which fields are real vs. silently-ignored, what "bloom" / "cinematic" / "moody" translate to in numeric params.
- **Interactive discipline** — this is a human-in-the-loop tool. The skill should make the agent stop after each visible change and ask, rather than stacking five modules then asking "how is it?".

The skill is also the natural vehicle for preloaded references: the agent shouldn't have to rediscover that `colorbalancergb.vibrance > ~0.5` combined with the exposure module currently breaks the pipeline.

---

## 2. Invocation triggers

The skill should fire (either via explicit `/darktable-editor` or via the skill description matching user intent) when the user:

- Points at a RAW file (`.ARW`, `.NEF`, `.CR2`, `.DNG`, `.RAF`, `.RW2`) and asks to edit, grade, tone, develop, or process it.
- Asks to "make this photo look like [reference]" and provides a RAW + reference image.
- References prior edits ("continue where we left off", "go back to the baseline version") when the project has a `.dtmcp/` directory.
- Asks to export a RAW at a specific resolution / format.

The skill description should be keyword-loaded for: *RAW, develop, edit photo, color grade, darktable, XMP, ARW/NEF/CR2/DNG, film look, cinematic, bloom, lift shadows, export JPEG*.

Explicitly NOT a trigger: requests to edit JPEG/PNG output files directly — the skill is about the parametric pipeline, not pixel edits.

---

## 3. Skill location and packaging

Two viable placements; pick one based on how this ships:

| Option | Path | Use when |
|---|---|---|
| **Project-local** | `C:/Agents/Darktable_MCP_tool/.claude/skills/darktable-editor/SKILL.md` | This repo is the canonical working location and the skill travels with the server. Preferred. |
| **User-global** | `C:/Users/wiebe/.claude/skills/darktable-editor/SKILL.md` | The user wants the skill available in any project that has the MCP server wired up. |

Project-local is preferred because the skill's value is tightly coupled to the MCP server's exact tool surface — if the server evolves, the skill in the same repo evolves with it atomically.

### Files in the skill directory

```
darktable-editor/
├── SKILL.md              # frontmatter + main body (always loaded when skill invoked)
├── reference/
│   ├── modules.md        # per-module param reference (colorbalancergb field map, safe ranges)
│   ├── workflows.md      # canonical edit patterns ("bloom", "cinematic", "bw", "clean-up")
│   └── pitfalls.md       # empirical failure modes (this session's lessons)
└── examples/
    └── cherry_blossom_bloom.md   # worked example from the current session
```

`SKILL.md` stays lean — it is always-loaded context. The `reference/` and `examples/` files are linked from `SKILL.md` so the agent reads them on-demand (via Read) only when the task calls for it. This keeps per-invocation token cost low.

---

## 4. Empirical knowledge to encode (learned this session)

These are the non-obvious findings from the current editing work. They belong in `reference/pitfalls.md` with the rationale.

### 4.1 `exposure` module interacts badly with `colorbalancergb` at high vibrance
- **Symptom:** preview renders pure white or pure black.
- **Threshold:** roughly `colorbalancergb.vibrance > 0.4–0.5` combined with *any* `exposure` history entry (positive or negative EV).
- **Workaround:** for moderate brightness changes when pushing saturated color, use `colorbalancergb.global_Y` (range ~−0.05…+0.05) instead of the `exposure` module. For large brightness changes, cap vibrance at ~0.35.
- **Not yet root-caused.** Possibly a headless-pipeline difference vs. GUI, possibly a codec issue. Flag it to the user rather than silently accepting a broken render.

### 4.2 `compensate_exposure_bias` must usually be `1`
- Setting `compensate_exposure_bias=0` combined with negative EV + `colorbalancergb` overexposes dramatically.
- The codec now defaults to `1`, matching darktable GUI default. Do not pass `0` unless you know why.

### 4.3 `brilliance_global` adds a haze, not real exposure
- User feedback in this session: `colorbalancergb.brilliance_global=0.08` produced "a layer on top of the image", not a brightness increase.
- Treat `brilliance_global` as a *highlight-bloom* effect, not an exposure substitute.

### 4.4 `highlights_Y` pulls down bright subjects
- Pulling `highlights_Y` negative to "add contrast" greyed out cherry blossoms (which *are* the highlights in that composition).
- Before adjusting zone luma, identify whether the subject sits in shadows / midtones / highlights and avoid pulling the zone the subject lives in.

### 4.5 Field-name typos are silently ignored
- `contrast`, `global_saturation`, `global_vibrance` are **not** valid field names on `colorbalancergb`. They pack to 0 and produce no visible effect.
- Correct names: `vibrance`, `saturation`, `chroma` (globals); `shadows_Y/C/H`, `midtones_Y/C/H`, `highlights_Y/C/H` (zones); `shadows_weight`, `midtones_weight`, `highlights_weight`.

### 4.6 Codec modversion must match the DT version
- Darktable 4.6.1 writes `exposure` at **modversion 7** (28 bytes, includes `compensate_hilite_pres`). Writing modversion 6 while the installed DT is 4.6.1 produced black renders.
- This is now fixed in the codec, but if the DT version ever changes, retest.

### 4.7 The MCP `compare` tool returns an `Image`, not a dict
- Prior schema bug wrapped `Image` in a dict and failed validation. The tool now returns `Image` directly and `os.startfile`s the HTML viewer. If the agent wants just the composite it gets it inline; if the user wants the slider, it's already open in the browser.

### 4.8 OneDrive / synced paths can block atomic XMP writes
- Already documented in `darktable_editor_mcp.md` §8.7. The skill should check the project path at session start and warn if `OneDrive` appears in it.

### 4.9 Session state is in-memory only
- After an MCP server reload (e.g. after `/mcp` reconnect), the agent must call `open_image` again. Old `session_id`s become invalid.

---

## 5. Workflow guidance (the `SKILL.md` body)

The skill body should prescribe a tight loop:

1. **Open and baseline.** Call `open_image`. Call `render_preview` once. Call `snapshot("baseline")` immediately. Show the baseline to the user before proposing any edit.
2. **One module at a time.** Never stack multiple modules before previewing. Small, reversible steps — the user can say "keep this" or "undo this" cleanly.
3. **Snapshot before experimenting.** Any time the agent is about to try a parameter it's unsure about, snapshot first with a descriptive label (`pre_vibrance_push`).
4. **Compare, don't describe.** Use the `compare` tool rather than narrating what changed. The composite shows up inline; the HTML viewer gives the user the slider.
5. **Ask after every visible change.** The human-in-the-loop is the point. Don't chain five edits hoping the user likes all of them.
6. **Export only on explicit approval.** `export_final` is the last step, and only after the user says "this is the one".

The body should also state a default preview width (1280) and default export format (JPEG, quality 95) so the agent doesn't pick values inconsistently across sessions.

---

## 6. Reference material to include

### 6.1 `reference/modules.md` — module parameter reference

For each module currently supported (`exposure`, `temperature`, `colorbalancergb`):

- The decoded parameter schema (copy from the codec class, not the C struct — the agent uses the Python dict form).
- **Safe range per parameter** — not the underlying valid range, but the range at which visible artifacts don't appear. For example `colorbalancergb.vibrance`: 0.0–0.35 safe, 0.35–0.5 use with care, >0.5 currently breaks with `exposure`.
- **Semantic meaning** per zone (shadows ≈ bottom 25% luminance, midtones ≈ 25–75%, highlights ≈ top 25%) because `_Y/_C/_H` naming isn't self-explanatory.
- **Worked examples** — "warm highlights by 5°": `highlights_H=50, highlights_C=0.04`.

### 6.2 `reference/workflows.md` — canonical patterns

Named edit recipes, each a short sequence of `set_module` calls with rationale:

- **Bloom** (saturated color pop, e.g. cherry blossoms): `colorbalancergb` with `vibrance=0.25, saturation=0.1, chroma=0.08`. No `exposure` module.
- **Cinematic** (teal/orange): split-tone via `shadows_H=200, shadows_C=0.04, highlights_H=50, highlights_C=0.04`.
- **Clean-up** (neutral baseline): reset, then gentle `temperature` correction if the WB is visibly off.
- **Moody / desaturated**: `saturation=-0.15`, small negative `global_Y` contrast lift.
- **BW**: currently not supported — flag to the user that `monochrome` module isn't in the codec registry yet.

Each recipe should also state its **interaction notes** (what *not* to combine it with).

### 6.3 `reference/pitfalls.md`

Everything from §4, one short section per item, each with a clear "when you hit X, do Y" shape.

### 6.4 `examples/cherry_blossom_bloom.md`

A fully worked example using the current `DSC09315.ARW` as the case study: the intent ("cherry blossoms should bloom"), the iterations that failed (dull/hazy/blown-out), and the final settled parameters. Future sessions can read this as concrete proof of what the workflow actually looks like.

---

## 7. Tool reference block

A compact table in `SKILL.md`, not full docstrings, that an agent can skim in ~100 tokens:

```
open_image(raw_path, reset=False) → session_id          # always first
snapshot(session_id, label)                              # before risky edits
set_module(session_id, op, params, enabled=True,         # edits
           instance=0, blend=None)
render_preview(session_id, width=1280,                   # see result (cached)
               snapshot_label=None) → Image
compare(session_id, label_a, label_b,                    # inline + HTML slider
        mode="split"|"side_by_side", width=1280) → Image
undo / redo / reset_all                                  # history cursor
get_history / get_module_params / list_supported_modules # inspection
export_final(session_id, output_path, format="jpg",      # last step
             width=0, format_opts={"quality": 95})
analyze_reference(ref_path) → dict                       # for match-this-look
```

The skill body links this table to the full docstrings in `server.py` for when the agent needs them.

---

## 8. What the skill explicitly does NOT do

- **Does not bypass the human loop.** Even if the user says "just make it look good", the skill should still show the user preview → ask → iterate.
- **Does not invent parameter values outside documented safe ranges** without calling that out (`"I'm going to try vibrance=0.6, which is outside the tested range — expect possibly white/black output"`).
- **Does not silently accept broken renders.** If a preview comes back obviously black / white / corrupted, flag it and revert via `undo` or `restore_snapshot`.
- **Does not touch the RAW file.** Ever. Only the XMP sidecar and files under `.dtmcp/`.
- **Does not run in parallel sessions on the same RAW.** XMP write race.

---

## 9. Testing the skill

Three validation scenarios before calling it done:

1. **Cold start on the cherry blossom image** — new session, `/darktable-editor DSC09315.ARW make blossoms bloom`. The agent should follow the documented workflow and produce something close to the known-good bloom params within 3–4 iterations, not re-discover the exposure-module footgun.
2. **Reference-guided edit** — provide a reference JPEG, confirm the agent uses `analyze_reference` and proposes params in documented ranges.
3. **Failure handling** — deliberately ask for `vibrance=0.8` and confirm the agent (a) warns, (b) snapshots before trying, (c) undoes cleanly when the render comes back broken.

Each scenario has a short transcript fixture committed alongside the skill so future refactors can diff against them.

---

## 10. Maintenance

When the MCP server gains a new module codec:
- Add a row to `reference/modules.md` with safe ranges.
- Add a recipe to `reference/workflows.md` if the module enables a new kind of edit.

When a new empirical pitfall is discovered in a session:
- Add an item to `reference/pitfalls.md` with symptom / threshold / workaround.
- The skill is a living document — the cost of encoding a lesson once is far less than re-discovering it every session.

The skill's description line (frontmatter) should be kept tight — ~200 characters, keyword-dense — so the skill-matcher picks it up reliably on photo-editing intent.

---

## 11. First-session build checklist

1. Create `C:/Agents/Darktable_MCP_tool/.claude/skills/darktable-editor/` directory.
2. Write `SKILL.md` with frontmatter (name, description, trigger keywords) + workflow body from §5 + tool table from §7.
3. Extract the empirical findings from §4 into `reference/pitfalls.md`.
4. Seed `reference/modules.md` with the three supported modules; document the safe ranges (use the table in §4.5 for field names).
5. Write the cherry blossom worked example to `examples/` as the first recipe.
6. Run validation scenario 1 from §9. Iterate on the skill body until the agent takes the documented path first-try.
7. Commit. The skill now travels with the repo and loads automatically when the MCP server is active in this project.
