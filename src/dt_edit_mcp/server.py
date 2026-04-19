"""dt-edit-mcp FastMCP server — main entrypoint."""
from __future__ import annotations

import os
import subprocess
import warnings
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

from .session import SessionManager, set_project_root
from .codecs import registry as codec_registry
from .compare import composite, html_viewer
from .colormatch.analyze import analyze_reference as _analyze_ref

mcp = FastMCP(
    "dt-edit-mcp",
    instructions=(
        "Darktable headless RAW editor. Use open_image first, then set_module to apply "
        "edits, render_preview to see results, snapshot/compare to compare states, "
        "and export_final for the finished file."
    ),
)
sessions = SessionManager()

# Set project root to DTMCP_ROOT env var if provided, otherwise no restriction
_root_env = os.environ.get("DTMCP_ROOT")
if _root_env:
    set_project_root(Path(_root_env))


# ── Lifecycle ──────────────────────────────────────────────────────────────────

@mcp.tool
def open_image(raw_path: str, reset: bool = False) -> dict:
    """
    Open a RAW file for editing. Creates or loads its XMP sidecar.
    Returns session info including session_id (needed for all other calls).

    reset=True clears the edit history so you start fresh. Darktable will
    re-apply its default pipeline (denoise, filmic, etc.) on next render.
    Avoid reset=True unless you genuinely need a blank slate — it discards
    any prior edits. Prefer reset_all(session_id) to undo user edits while
    keeping the session open.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        session = sessions.open(raw_path, reset=reset)

    result = session.info()
    if caught:
        result["warnings"] = [str(w.message) for w in caught]
    return result


@mcp.tool
def close_image(session_id: str) -> dict:
    """Close a session. The XMP on disk is preserved."""
    sessions.close(session_id)
    return {"closed": session_id}


# ── Inspection ────────────────────────────────────────────────────────────────

@mcp.tool
def get_history(session_id: str) -> list[dict]:
    """Return the full history stack with decoded params for known modules."""
    return sessions.get(session_id).get_history()


@mcp.tool
def get_module_params(session_id: str, operation: str, instance: int = 0) -> Optional[dict]:
    """Return decoded params for a specific module, or null if not present."""
    return sessions.get(session_id).get_module_params(operation, instance)


@mcp.tool
def list_supported_modules() -> list[dict]:
    """List modules with full encode/decode support vs. opaque passthrough."""
    return codec_registry.supported_operations()


# ── Editing ───────────────────────────────────────────────────────────────────

@mcp.tool
def set_module(
    session_id: str,
    operation: str,
    params: dict,
    enabled: bool = True,
    instance: int = 0,
    blend: Optional[dict] = None,
) -> dict:
    """
    Set parameters for a darktable module.

    params: dict of module-specific parameters. Use get_module_params or
            list_supported_modules to see what fields are available.

    blend (optional): {
        "opacity": 0.0–1.0,
        "luma_low": 0.0,      # parametric luminance mask lower bound
        "luma_high": 1.0,     # parametric luminance mask upper bound
        "luma_low_feather": 0.0,
        "luma_high_feather": 0.0
    }

    If history_end < history length (i.e. you undid steps), forward history
    is truncated before inserting (like a text editor).
    """
    return sessions.get(session_id).set_module(operation, params, enabled, instance, blend)


@mcp.tool
def disable_module(session_id: str, operation: str, instance: int = 0) -> dict:
    """Disable (but do not remove) a module from the history."""
    ok = sessions.get(session_id).disable_module_op(operation, instance)
    return {"success": ok, "operation": operation}


@mcp.tool
def undo(session_id: str, steps: int = 1) -> dict:
    """Undo the last N edits by moving the history cursor back."""
    end = sessions.get(session_id).undo(steps)
    return {"history_end": end}


@mcp.tool
def redo(session_id: str, steps: int = 1) -> dict:
    """Redo N undone edits."""
    end = sessions.get(session_id).redo(steps)
    return {"history_end": end}


@mcp.tool
def reset_all(session_id: str) -> dict:
    """Reset the edit history to zero (neutral/unedited state)."""
    sessions.get(session_id).reset_all()
    return {"history_end": 0}


# ── Snapshots ─────────────────────────────────────────────────────────────────

@mcp.tool
def snapshot(session_id: str, label: str) -> dict:
    """Save the current XMP state under a named label for later comparison."""
    sessions.get(session_id).snapshot(label)
    return {"saved": label}


@mcp.tool
def restore_snapshot(session_id: str, label: str) -> dict:
    """Restore a previously saved snapshot as the current edit state."""
    sessions.get(session_id).restore_snapshot(label)
    return {"restored": label}


@mcp.tool
def list_snapshots(session_id: str) -> list[str]:
    """List all saved snapshot labels for this session."""
    return sessions.get(session_id).snapshots.list()


# ── Rendering ─────────────────────────────────────────────────────────────────

@mcp.tool
def render_preview(
    session_id: str,
    width: int = 1280,
    snapshot_label: Optional[str] = None,
) -> Image:
    """
    Render a JPEG preview via darktable-cli and return it inline.
    Uses a snapshot if snapshot_label is provided.
    Results are cached; repeated calls with identical state are instant.
    """
    path = sessions.get(session_id).render(width=width, snapshot=snapshot_label)
    return Image(path=str(path))


@mcp.tool
def export_final(
    session_id: str,
    output_path: str,
    format: str = "jpg",
    width: int = 0,
    icc_type: str = "SRGB",
    icc_file: Optional[str] = None,
    icc_intent: str = "perceptual",
    format_opts: Optional[dict] = None,
) -> dict:
    """
    Export the final image at full resolution via darktable-cli.

    format: "jpg", "png", "tiff", "webp", "exr", "jxl"
    width: 0 = native resolution
    format_opts examples:
      JPEG/WebP: {"quality": 95}
      TIFF:      {"bit_depth": 16, "compression": "deflate"}
      JXL:       {"effort": 7, "distance": 1.0}
    """
    from .renderer import darktable_cli as dt_cli
    from . import session as session_mod

    sess = sessions.get(session_id)
    out = session_mod._check_path(Path(output_path))
    out.parent.mkdir(parents=True, exist_ok=True)

    opts = format_opts or {}
    quality = int(opts.get("quality", 95))

    dt_cli.render(
        raw_path=sess.raw_path,
        xmp_path=sess.xmp_path,
        output_path=out,
        width=width,
        hq=True,
        jpeg_quality=quality,
    )
    return {"output": str(out), "size_bytes": out.stat().st_size}


# ── Comparison ────────────────────────────────────────────────────────────────

@mcp.tool
def compare(
    session_id: str,
    label_a: str,
    label_b: str,
    mode: str = "split",
    width: int = 1280,
) -> Image:
    """
    Compare two snapshots side by side.

    mode: "split" (wipe), "side_by_side"
    Returns an inline composite image and opens the HTML viewer.
    """
    sess = sessions.get(session_id)

    # Render both snapshots (cached)
    path_a = sess.render(width=width, snapshot=label_a)
    path_b = sess.render(width=width, snapshot=label_b)

    compare_dir = sess.work_dir / "compare"
    compare_dir.mkdir(exist_ok=True)

    if mode == "side_by_side":
        composite_path = composite.side_by_side(
            path_a, path_b, label_a, label_b,
            output=compare_dir / f"{label_a}_vs_{label_b}_sbs.jpg"
        )
    else:
        composite_path = composite.split_wipe(
            path_a, path_b, label_a=label_a, label_b=label_b,
            output=compare_dir / f"{label_a}_vs_{label_b}_split.jpg"
        )

    html_path = compare_dir / f"{label_a}_vs_{label_b}.html"
    html_viewer.write(path_a, path_b, label_a, label_b, html_path)

    import webbrowser
    webbrowser.open(html_path.as_uri())

    return Image(path=str(composite_path))


@mcp.tool
def open_in_viewer(path: str) -> dict:
    """Open a file in the OS default application (browser for HTML, viewer for images)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    subprocess.Popen(["explorer", str(p)])
    return {"opened": str(p)}


# ── Reference analysis ────────────────────────────────────────────────────────

@mcp.tool
def analyze_reference(ref_path: str) -> dict:
    """
    Analyze a reference image's color grade.
    Returns LAB stats per tone zone, dominant hues, contrast, and saturation —
    giving the agent concrete numbers to guide colorbalancergb / temperature settings.
    """
    p = Path(ref_path)
    if not p.exists():
        raise FileNotFoundError(f"Reference not found: {ref_path}")
    return _analyze_ref(p)


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main():
    mcp.run()


if __name__ == "__main__":
    main()
