"""Image -> per-cell colour.

Steps: fit the image to the lattice rectangle (cover / contain / stretch), rasterise every cell
polygon as a mask, average the pixels inside it, then quantise to a filament palette (either a
fixed list of filament colours or k-means with a maximum number of colours).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from .grid import Grid

RGB = Tuple[int, int, int]


# ---- colour helpers --------------------------------------------------------------------------

def hex_to_rgb(h: str) -> RGB:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_to_hex(rgb: Sequence[float]) -> str:
    return "#%02X%02X%02X" % tuple(int(round(max(0, min(255, c)))) for c in rgb[:3])


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """rgb: (..., 3) 0..255 -> CIELAB (D65). Perceptual distance for palette matching."""
    rgb = np.asarray(rgb, dtype=np.float64)
    lin = _srgb_to_linear(rgb)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    eps, kappa = 216 / 24389, 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def luminance(rgb: Sequence[float]) -> float:
    """Relative luminance 0..1 (linear light)."""
    lin = _srgb_to_linear(np.asarray(rgb, dtype=np.float64))
    return float(0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2])


# ---- palette -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Filament:
    name: str
    hex: str

    @property
    def rgb(self) -> RGB:
        return hex_to_rgb(self.hex)


def parse_palette(items: Sequence[str]) -> List[Filament]:
    """Accepts 'Name=#RRGGBB' or '#RRGGBB' strings."""
    out = []
    for it in items:
        if "=" in it:
            name, hx = it.split("=", 1)
        else:
            name, hx = it, it
        hx = rgb_to_hex(hex_to_rgb(hx))
        out.append(Filament(name.strip(), hx))
    return out


def kmeans_palette(colors: np.ndarray, k: int, weights: Optional[np.ndarray] = None,
                   iters: int = 40, seed: int = 0) -> np.ndarray:
    """Weighted k-means in Lab space. colors: (n,3) sRGB. Returns (k,3) sRGB centres."""
    rng = np.random.default_rng(seed)
    lab = rgb_to_lab(colors)
    n = len(lab)
    k = max(1, min(k, n))
    if weights is None:
        weights = np.ones(n)
    # k-means++ init
    centres = [lab[rng.integers(n)]]
    for _ in range(1, k):
        d2 = np.min(((lab[:, None, :] - np.array(centres)[None, :, :]) ** 2).sum(-1), axis=1)
        probs = d2 * weights
        if probs.sum() <= 0:
            centres.append(lab[rng.integers(n)])
            continue
        centres.append(lab[rng.choice(n, p=probs / probs.sum())])
    C = np.array(centres)
    for _ in range(iters):
        d = ((lab[:, None, :] - C[None, :, :]) ** 2).sum(-1)
        lbl = d.argmin(1)
        newC = C.copy()
        for j in range(k):
            m = lbl == j
            if m.any():
                w = weights[m][:, None]
                newC[j] = (lab[m] * w).sum(0) / w.sum()
        if np.allclose(newC, C):
            break
        C = newC
    # map centres back to sRGB: weighted mean of the members closest to the centre (inner 60%),
    # which keeps clusters vivid instead of averaging in their outliers
    d = ((lab[:, None, :] - C[None, :, :]) ** 2).sum(-1)
    lbl = d.argmin(1)
    out = []
    for j in range(k):
        m = np.where(lbl == j)[0]
        if len(m) == 0:
            continue
        dj = d[m, j]
        keep = m[dj <= np.quantile(dj, 0.6)] if len(m) > 3 else m
        w = weights[keep][:, None]
        out.append((colors[keep] * w).sum(0) / w.sum())
    return np.array(out)


def nearest_palette_index(rgb: Sequence[float], palette_lab: np.ndarray) -> int:
    lab = rgb_to_lab(np.asarray(rgb, dtype=np.float64))
    return int(((palette_lab - lab) ** 2).sum(1).argmin())


# ---- image fitting and sampling --------------------------------------------------------------

def enhance_image(img: Image.Image, contrast_cutoff: float = 1.0, saturation: float = 1.25) -> Image.Image:
    """Stretch contrast and boost saturation a little: with only a handful of filament colours
    a flat image collapses into mid-tones, so push highlights and shadows apart first."""
    img = ImageOps.autocontrast(img, cutoff=contrast_cutoff, preserve_tone=True)
    if saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(saturation)
    return img


def fit_image(img: Image.Image, target_w: float, target_h: float, mode: str = "cover",
              px_per_mm: float = 2.0, background: str = "#000000", enhance: bool = True) -> Tuple[Image.Image, float]:
    """Return an RGB image covering exactly the lattice rectangle at px_per_mm resolution."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    if enhance:
        img = enhance_image(img)
    W = max(1, int(round(target_w * px_per_mm)))
    H = max(1, int(round(target_h * px_per_mm)))
    if mode == "stretch":
        out = img.resize((W, H), Image.LANCZOS)
    elif mode == "cover":
        out = ImageOps.fit(img, (W, H), Image.LANCZOS, centering=(0.5, 0.5))
    elif mode == "contain":
        canvas = Image.new("RGB", (W, H), hex_to_rgb(background))
        im2 = ImageOps.contain(img, (W, H), Image.LANCZOS)
        canvas.paste(im2, ((W - im2.width) // 2, (H - im2.height) // 2))
        out = canvas
    else:
        raise ValueError(f"unknown fit mode {mode}")
    return out, px_per_mm


def sample_cells(grid: Grid, fitted: Image.Image, px_per_mm: float, shrink: float = 0.85,
                 line_boost: float = 0.0, line_min_delta: float = 22.0, line_coherence: float = 34.0) -> None:
    """Set cell.rgb_mean = mean colour inside each (slightly shrunken) cell polygon.

    line_boost > 0 keeps thin, strongly contrasting features (cables, masts, outlines) that
    plain averaging would erase: pixels in the cell that differ from the cell's median colour by
    more than line_min_delta (CIELAB) are treated as a feature; if they cover a fraction f of
    the cell, the cell colour is pushed from the median towards the feature colour by
    min(1, f * line_boost). With line_boost = 6 a feature covering 1/6 of the cell takes it over."""
    arr = np.asarray(fitted, dtype=np.float64)
    H, W = arr.shape[:2]
    for c in grid.cells:
        cx, cy = c.centroid
        pts = [((cx + (x - cx) * shrink) * px_per_mm, (cy + (y - cy) * shrink) * px_per_mm)
               for x, y in c.poly]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = max(0, int(math.floor(min(xs)))), min(W, int(math.ceil(max(xs))) + 1)
        y0, y1 = max(0, int(math.floor(min(ys)))), min(H, int(math.ceil(max(ys))) + 1)
        if x1 <= x0 or y1 <= y0:
            c.rgb_mean = (0, 0, 0)
            continue
        mask = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(mask).polygon([(x - x0, y - y0) for x, y in pts], fill=255)
        m = np.asarray(mask) > 0
        if not m.any():
            px = arr[min(H - 1, int(cy * px_per_mm)), min(W - 1, int(cx * px_per_mm))]
            c.rgb_mean = tuple(float(v) for v in px)
            continue
        pix = arr[y0:y1, x0:x1][m]
        mean = pix.mean(0)
        if line_boost > 0 and len(pix) >= 16:
            med = np.median(pix, axis=0)
            lab = rgb_to_lab(pix)
            d = np.sqrt(((lab - rgb_to_lab(med)) ** 2).sum(1))
            feat = d > line_min_delta
            f = feat.mean()
            if 0.03 < f < 0.45:
                # a real thin feature has one coherent colour; texture noise does not
                fl = lab[feat]
                spread = float(np.sqrt(((fl - fl.mean(0)) ** 2).sum(1)).mean())
                if spread < line_coherence:
                    feat_col = pix[feat].mean(0)
                    w = min(1.0, f * line_boost)
                    mean = med + (feat_col - med) * w
        c.rgb_mean = tuple(float(v) for v in mean)


def assign_colors(grid: Grid, palette: Optional[List[Filament]] = None, max_colors: int = 4,
                  dither: bool = False, seed: int = 0) -> List[Filament]:
    """Quantise cell colours. Returns the palette actually used (ordered by usage)."""
    cells = grid.cells
    means = np.array([c.rgb_mean for c in cells], dtype=np.float64)
    areas = np.array([0.5 if c.is_half else 1.0 for c in cells])
    if palette is None or len(palette) == 0:
        centres = kmeans_palette(means, max_colors, weights=areas, seed=seed)
        palette = [Filament(f"color{i+1}", rgb_to_hex(c)) for i, c in enumerate(centres)]
    pal_lab = rgb_to_lab(np.array([f.rgb for f in palette], dtype=np.float64))

    if not dither:
        for c in cells:
            i = nearest_palette_index(c.rgb_mean, pal_lab)
            c.color, c.color_name = palette[i].hex, palette[i].name
    else:
        # error diffusion along each row (left->right), half of the error pushed to the
        # neighbouring cell in the next row. Simple, works acceptably on a triangular lattice.
        err: Dict[Tuple[int, int], np.ndarray] = {}
        rows: Dict[int, List] = {}
        for c in cells:
            rows.setdefault(c.row, []).append(c)
        for r in sorted(rows):
            row = sorted(rows[r], key=lambda c: c.col)
            for c in row:
                target = np.array(c.rgb_mean) + err.pop((c.row, c.col), 0.0)
                i = nearest_palette_index(np.clip(target, 0, 255), pal_lab)
                c.color, c.color_name = palette[i].hex, palette[i].name
                e = target - np.array(palette[i].rgb, dtype=np.float64)
                err[(c.row, c.col + 1)] = err.get((c.row, c.col + 1), 0.0) + e * 0.5
                err[(c.row + 1, c.col)] = err.get((c.row + 1, c.col), 0.0) + e * 0.25
                err[(c.row + 1, c.col + 1)] = err.get((c.row + 1, c.col + 1), 0.0) + e * 0.25

    usage: Dict[str, float] = {}
    for c in cells:
        usage[c.color] = usage.get(c.color, 0) + (0.5 if c.is_half else 1.0)
    used = [f for f in palette if f.hex in usage]
    used.sort(key=lambda f: -usage[f.hex])
    return used
