"""Subprocess wrapper for darktable-cli.exe."""
from __future__ import annotations

import os
import subprocess
import shutil
from pathlib import Path

_CLI_CANDIDATES = [
    r"C:\Program Files\darktable\bin\darktable-cli.exe",
    r"C:\Program Files (x86)\darktable\bin\darktable-cli.exe",
]

_cached_cli: Path | None = None
_cached_version: str | None = None


def find_cli() -> Path:
    global _cached_cli
    if _cached_cli:
        return _cached_cli

    env_path = os.environ.get("DARKTABLE_CLI")
    if env_path and Path(env_path).exists():
        _cached_cli = Path(env_path)
        return _cached_cli

    for candidate in _CLI_CANDIDATES:
        p = Path(candidate)
        if p.exists():
            _cached_cli = p
            return _cached_cli

    found = shutil.which("darktable-cli") or shutil.which("darktable-cli.exe")
    if found:
        _cached_cli = Path(found)
        return _cached_cli

    raise FileNotFoundError(
        "darktable-cli not found. Install darktable or set DARKTABLE_CLI env var."
    )


def dt_version() -> str:
    global _cached_version
    if _cached_version:
        return _cached_version
    cli = find_cli()
    result = subprocess.run(
        [str(cli), "--version"],
        capture_output=True, stdin=subprocess.DEVNULL, text=True, timeout=15
    )
    _cached_version = result.stdout.strip().splitlines()[0] if result.stdout else "unknown"
    return _cached_version


def render(
    raw_path: Path,
    xmp_path: Path,
    output_path: Path,
    width: int = 1280,
    height: int = 0,
    hq: bool = False,
    jpeg_quality: int = 85,
    configdir: str | None = None,
) -> Path:
    """
    Invoke darktable-cli to render raw_path + xmp_path → output_path.
    Returns output_path on success; raises RuntimeError on failure.
    """
    cli = find_cli()

    if configdir is None:
        configdir = str(Path.home() / "AppData" / "Roaming" / "darktable")

    cmd = [
        str(cli),
        raw_path.as_posix(),
        xmp_path.as_posix(),
        output_path.as_posix(),
        "--width", str(width),
        "--apply-custom-presets", "0",
        "--hq", "1" if hq else "0",
        "--core",
        "--conf", f"plugins/imageio/format/jpeg/quality={jpeg_quality}",
        "--conf", "plugins/opencl/opencl=FALSE",
        "--configdir", configdir,
    ]

    if height > 0:
        cmd.extend(["--height", str(height)])

    result = subprocess.run(
        cmd,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=120,
    )

    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(
            f"darktable-cli failed (exit {result.returncode}):\n"
            f"STDOUT: {result.stdout[-2000:]}\n"
            f"STDERR: {result.stderr[-2000:]}"
        )

    return output_path
