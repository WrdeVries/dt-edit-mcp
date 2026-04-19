"""PIL-based side-by-side / split composite image generation."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def side_by_side(
    img_a: Path,
    img_b: Path,
    label_a: str = "A",
    label_b: str = "B",
    output: Path | None = None,
) -> Path:
    """Stitch two images horizontally with a divider and labels."""
    a = Image.open(img_a).convert("RGB")
    b = Image.open(img_b).convert("RGB")

    # Normalize to same height
    h = min(a.height, b.height)
    if a.height != h:
        a = a.resize((int(a.width * h / a.height), h), Image.LANCZOS)
    if b.height != h:
        b = b.resize((int(b.width * h / b.height), h), Image.LANCZOS)

    divider_w = 4
    total_w = a.width + divider_w + b.width
    out = Image.new("RGB", (total_w, h), (30, 30, 30))
    out.paste(a, (0, 0))
    out.paste(b, (a.width + divider_w, 0))

    # Labels
    draw = ImageDraw.Draw(out)
    _draw_label(draw, label_a, 10, 10)
    _draw_label(draw, label_b, a.width + divider_w + 10, 10)

    out_path = output or img_a.parent / f"compare_{img_a.stem}_vs_{img_b.stem}.jpg"
    out.save(str(out_path), "JPEG", quality=90)
    return out_path


def split_wipe(
    img_a: Path,
    img_b: Path,
    split: float = 0.5,
    label_a: str = "A",
    label_b: str = "B",
    output: Path | None = None,
) -> Path:
    """Split wipe: left portion of A, right portion of B."""
    a = Image.open(img_a).convert("RGB")
    b = Image.open(img_b).convert("RGB")
    w, h = a.width, a.height
    if b.size != (w, h):
        b = b.resize((w, h), Image.LANCZOS)

    cut = int(w * split)
    out = Image.new("RGB", (w, h))
    out.paste(a.crop((0, 0, cut, h)), (0, 0))
    out.paste(b.crop((cut, 0, w, h)), (cut, 0))

    draw = ImageDraw.Draw(out)
    draw.line([(cut, 0), (cut, h)], fill=(200, 200, 200), width=2)
    _draw_label(draw, label_a, 10, 10)
    _draw_label(draw, label_b, cut + 10, 10)

    out_path = output or img_a.parent / f"split_{img_a.stem}_vs_{img_b.stem}.jpg"
    out.save(str(out_path), "JPEG", quality=90)
    return out_path


def _draw_label(draw: ImageDraw.Draw, text: str, x: int, y: int) -> None:
    # Shadow + white text
    for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
        draw.text((x + dx, y + dy), text, fill=(0, 0, 0))
    draw.text((x, y), text, fill=(255, 255, 255))
