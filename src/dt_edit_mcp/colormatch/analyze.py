"""Reference image analysis for LLM-guided color matching."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def analyze_reference(ref_path: Path, n_clusters: int = 5) -> dict[str, Any]:
    """
    Compute a compact quantitative description of the reference image's color grade:
      - LAB mean/std per tone region (shadows/midtones/highlights)
      - Dominant palette (k-means in LAB)
      - Contrast and saturation estimates

    Returns a dict the agent can reason about directly.
    """
    img = Image.open(ref_path).convert("RGB")
    # Resize for speed
    img.thumbnail((512, 512), Image.LANCZOS)
    arr = np.asarray(img, dtype=np.float32) / 255.0

    lab = _rgb_to_lab(arr)

    L = lab[:, :, 0]  # 0..100
    a = lab[:, :, 1]  # -128..127
    b = lab[:, :, 2]

    # Tone regions by luminance
    shadow_mask = L < 33
    mid_mask = (L >= 33) & (L < 67)
    high_mask = L >= 67

    result: dict[str, Any] = {
        "overall": _zone_stats(L, a, b, np.ones_like(L, dtype=bool)),
        "shadows": _zone_stats(L, a, b, shadow_mask),
        "midtones": _zone_stats(L, a, b, mid_mask),
        "highlights": _zone_stats(L, a, b, high_mask),
        "contrast": float(np.std(L)),
        "saturation_estimate": float(np.mean(np.sqrt(a**2 + b**2))),
        "dominant_hues": _dominant_hues(a, b, n_clusters),
        "image_size": list(img.size),
    }
    return result


def _zone_stats(L, a, b, mask) -> dict:
    if not mask.any():
        return {}
    return {
        "L_mean": round(float(L[mask].mean()), 1),
        "L_std": round(float(L[mask].std()), 1),
        "a_mean": round(float(a[mask].mean()), 2),
        "b_mean": round(float(b[mask].mean()), 2),
        "chroma_mean": round(float(np.sqrt(a[mask]**2 + b[mask]**2).mean()), 2),
    }


def _dominant_hues(a, b, k: int) -> list[dict]:
    """Simple k-means on (a,b) to find dominant chroma clusters."""
    flat_a = a.flatten()
    flat_b = b.flatten()
    chroma = np.sqrt(flat_a**2 + flat_b**2)
    # Only use pixels with significant chroma
    mask = chroma > 10
    if mask.sum() < k:
        return []
    pts = np.stack([flat_a[mask], flat_b[mask]], axis=1)

    # Mini k-means (3 iterations, good enough for summary)
    rng = np.random.default_rng(42)
    centers = pts[rng.choice(len(pts), k, replace=False)]
    for _ in range(6):
        dists = np.linalg.norm(pts[:, None] - centers[None, :], axis=2)
        labels = dists.argmin(axis=1)
        centers = np.array([pts[labels == i].mean(axis=0) if (labels == i).any() else centers[i]
                            for i in range(k)])

    hues = []
    for i, c in enumerate(centers):
        hue_deg = float(np.degrees(np.arctan2(c[1], c[0])) % 360)
        chroma_val = float(np.sqrt(c[0]**2 + c[1]**2))
        count = int((labels == i).sum())
        hues.append({
            "hue_deg": round(hue_deg, 1),
            "chroma": round(chroma_val, 1),
            "pixel_count": count,
            "label": _hue_name(hue_deg),
        })
    return sorted(hues, key=lambda x: -x["pixel_count"])


def _hue_name(deg: float) -> str:
    names = [
        (15, "red"), (45, "orange"), (75, "yellow"), (105, "yellow-green"),
        (135, "green"), (165, "cyan-green"), (195, "cyan"), (225, "blue-cyan"),
        (255, "blue"), (285, "blue-magenta"), (315, "magenta"), (345, "red-magenta"),
    ]
    for threshold, name in names:
        if deg < threshold:
            return name
    return "red"


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB [0,1] → CIE LAB (approximate, no ICC profile)."""
    # Linearize sRGB
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)

    # sRGB → XYZ (D65)
    M = np.array([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ])
    xyz = lin @ M.T

    # Normalize to D65 white
    xyz /= np.array([0.95047, 1.00000, 1.08883])

    # XYZ → LAB
    eps = 0.008856
    kappa = 903.3

    def f(t):
        return np.where(t > eps, np.cbrt(t), (kappa * t + 16.0) / 116.0)

    fx, fy, fz = f(xyz[..., 0]), f(xyz[..., 1]), f(xyz[..., 2])
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    b = 200 * (fy - fz)
    return np.stack([L, a, b], axis=-1)
