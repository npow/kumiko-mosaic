"""Joint filament + pattern-density matching for coloured kumiko strips over a background.

At viewing distance the eye averages a cell: colour = coverage * strip filament + (1 - coverage)
* background, mixed in linear light. Given the filaments you own and a ladder of patterns of
increasing strip coverage, every cell gets the (filament, pattern) pair whose mix is closest
(CIELAB) to the image colour. Dark areas end up with sparse patterns, bright and saturated
areas with dense ones, so tone comes from density and hue from the filament.
"""
from __future__ import annotations

from itertools import combinations
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import inserts
from .grid import Grid
from .imagemap import Filament, _srgb_to_linear, rgb_to_lab, rgb_to_hex, kmeans_palette

# sparse -> dense candidates; filtered per pitch/strip so every opening stays >= MIN_HOLE_MM
DEFAULT_LADDER = ["y", "y2", "hexagram", "hexagram2", "asanoha", "hex", "mesh2", "pinwheel", "kagome2", "kagome3",
                  "cross2", "mesh3", "hex2", "sunburst4", "step2",
                  "stripes3", "stripes4", "stripes5", "stripes6", "mesh4", "rings2", "weave2", "mesh5", "fan3",
                  "asanoha2", "mesh6", "rings3", "asanoha3"]
MIN_HOLE_MM = 2.5
MAX_COVERAGE = 0.65   # above this a pattern is a plate with holes, not a line pattern


def usable_ladder(grid: Grid, ladder: Sequence[str], strip_mm: float, min_hole_mm: float = MIN_HOLE_MM,
                  max_coverage: float = MAX_COVERAGE) -> List[str]:
    """Drop patterns whose openings close up at this pitch or that are mostly solid, then sort
    sparse -> dense."""
    side = grid.spec.inner_side
    ok = [p for p in ladder if inserts.printable(p, side, strip_mm, min_hole_mm)]
    cov = coverage_table(grid, ok, strip_mm)
    ok = [p for p in ok if cov[p] <= max_coverage]
    return sorted(ok, key=lambda p: cov[p])


def _linear_to_srgb(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(np.clip(c, 0, 1), 1 / 2.4) - 0.055) * 255.0


def mixed_rgb(strip_rgb: Sequence[float], bg_rgb: Sequence[float], coverage: float) -> np.ndarray:
    a = _srgb_to_linear(np.asarray(strip_rgb, dtype=float))
    b = _srgb_to_linear(np.asarray(bg_rgb, dtype=float))
    return _linear_to_srgb(coverage * a + (1 - coverage) * b)


def coverage_table(grid: Grid, ladder: Sequence[str], strip_mm: float, clearance: float = 0.0) -> Dict[str, float]:
    side = grid.spec.inner_side
    tri = side * side * np.sqrt(3) / 4
    out = {}
    for pid in ladder:
        out[pid] = float(inserts.canonical_polygon(pid, side, strip_mm, clearance).area / tri)
    return out


def auto_palette_for_strips(grid: Grid, bg_rgb: Sequence[float], k: int, seed: int = 0) -> List[Filament]:
    """Pick k strip filaments. Since dark cells are made with sparse patterns, cluster on the
    cell colours *brightened* towards their most saturated full-coverage equivalent: what
    filament would reproduce this cell at 100% coverage? Clamp to the gamut."""
    means = np.array([c.rgb_mean for c in grid.cells], dtype=float)
    lin = _srgb_to_linear(means)
    bg = _srgb_to_linear(np.asarray(bg_rgb, dtype=float))
    # scale each colour away from the background so its brightest channel hits ~0.9
    d = lin - bg
    mx = np.maximum(d.max(axis=1, keepdims=True), 1e-4)
    scale = np.clip(0.85 / mx, 1.0, 6.0)
    boosted = np.clip(bg + d * scale, 0, 1)
    rgb = _linear_to_srgb(boosted)
    areas = np.array([0.5 if c.is_half else 1.0 for c in grid.cells])
    # weight bright/saturated cells a bit more: they define the visible colours
    w = areas * (0.3 + means.mean(1) / 255.0)
    centres = kmeans_palette(rgb, k, weights=w, seed=seed)
    return [Filament(f"color{i+1}", rgb_to_hex(c)) for i, c in enumerate(centres)]


def choose_pattern_subset(grid: Grid, palette: Sequence[Filament], bg_rgb, ladder: Sequence[str],
                          cov: Dict[str, float], max_patterns: int, allow_empty: bool = True) -> List[str]:
    """Pick the subset of <= max_patterns ladder patterns that minimises the total CIELAB error
    over all cells (brute force over subsets; the ladder is short)."""
    if max_patterns >= len(ladder):
        return list(ladder)
    targets = rgb_to_lab(np.array([c.rgb_mean for c in grid.cells], dtype=float))
    areas = np.array([0.5 if c.is_half else 1.0 for c in grid.cells])
    # distance matrix: cells x (filament, pattern)
    cols = []
    keys = []
    for f in palette:
        for pid in ladder:
            cols.append(rgb_to_lab(mixed_rgb(f.rgb, bg_rgb, cov[pid])))
            keys.append(pid)
    D = np.sqrt(((targets[:, None, :] - np.array(cols)[None, :, :]) ** 2).sum(-1))
    empty = np.sqrt(((targets - rgb_to_lab(np.asarray(bg_rgb, dtype=float))) ** 2).sum(-1)) if allow_empty else None
    keys = np.array(keys)

    def total(sub):
        d = D[:, np.isin(keys, sub)].min(1)
        if empty is not None:
            d = np.minimum(d, empty)
        return float((d * areas).sum())

    if len(ladder) <= 12:
        best, best_err = list(ladder[:max_patterns]), float("inf")
        for sub in combinations(ladder, max_patterns):
            err = total(sub)
            if err < best_err:
                best, best_err = list(sub), err
        return best
    # greedy forward selection with one swap-refinement pass (ladder is long)
    chosen: List[str] = []
    while len(chosen) < max_patterns:
        cand = min((p for p in ladder if p not in chosen), key=lambda p: total(chosen + [p]))
        chosen.append(cand)
    improved = True
    while improved:
        improved = False
        for i in range(len(chosen)):
            for p in ladder:
                if p in chosen:
                    continue
                trial = chosen[:i] + [p] + chosen[i + 1:]
                if total(trial) < total(chosen) - 1e-9:
                    chosen, improved = trial, True
    return sorted(chosen, key=lambda p: cov[p])


def choose_filaments(grid: Grid, candidates: Sequence[Filament], bg_rgb, ladder: Sequence[str],
                     cov: Dict[str, float], k: int, allow_empty: bool = True) -> List[Filament]:
    """Pick k filaments from a purchasable catalogue that minimise the total CIELAB error of the
    best achievable (filament, density) mix per cell. Greedy forward selection + swap pass."""
    targets = rgb_to_lab(np.array([c.rgb_mean for c in grid.cells], dtype=float))
    areas = np.array([0.5 if c.is_half else 1.0 for c in grid.cells])
    # best distance per cell for each candidate filament (min over the ladder)
    best_per_f = []
    for f in candidates:
        labs = np.array([rgb_to_lab(mixed_rgb(f.rgb, bg_rgb, cov[p])) for p in ladder])
        d = np.sqrt(((targets[:, None, :] - labs[None, :, :]) ** 2).sum(-1)).min(1)
        best_per_f.append(d)
    B = np.array(best_per_f)                      # (n_candidates, cells)
    empty = np.sqrt(((targets - rgb_to_lab(np.asarray(bg_rgb, dtype=float))) ** 2).sum(-1)) if allow_empty else np.full(len(targets), np.inf)

    def total(idx):
        d = B[list(idx)].min(0) if idx else np.full(len(targets), np.inf)
        return float((np.minimum(d, empty) * areas).sum())

    # drop duplicate hex codes (Matte Charcoal / Basic Black etc.)
    seen, pool = set(), []
    for i, f in enumerate(candidates):
        if f.hex not in seen:
            seen.add(f.hex)
            pool.append(i)
    chosen: List[int] = []
    while len(chosen) < min(k, len(pool)):
        chosen.append(min((i for i in pool if i not in chosen), key=lambda i: total(chosen + [i])))
    improved = True
    while improved:
        improved = False
        for j in range(len(chosen)):
            for i in pool:
                if i in chosen:
                    continue
                trial = chosen[:j] + [i] + chosen[j + 1:]
                if total(trial) < total(chosen) - 1e-9:
                    chosen, improved = trial, True
    return [candidates[i] for i in chosen]


def assign_strips(grid: Grid, palette: Sequence[Filament], bg_hex: str, ladder: Sequence[str],
                  strip_mm: float, clearance: float = 0.0, allow_empty: bool = True,
                  dither: bool = False, max_patterns: Optional[int] = None,
                  min_hole_mm: float = MIN_HOLE_MM) -> Dict[str, float]:
    """Set cell.color / cell.color_name / cell.pattern by nearest mixed colour.
    allow_empty: a cell may get no insert at all (pure background) when that is closest.
    max_patterns: limit the number of distinct patterns (fewer parts to sort and assemble)."""
    from .imagemap import hex_to_rgb
    bg = hex_to_rgb(bg_hex)
    ladder = usable_ladder(grid, ladder, strip_mm, min_hole_mm)
    if not ladder:
        raise ValueError("no pattern in the ladder is printable at this pitch/strip width")
    cov = coverage_table(grid, ladder, strip_mm, clearance)
    if max_patterns:
        ladder = choose_pattern_subset(grid, palette, bg, ladder, cov, max_patterns, allow_empty)
        cov = {k: cov[k] for k in ladder}
    options: List[Tuple[Optional[Filament], Optional[str], np.ndarray]] = []
    for f in palette:
        for pid in ladder:
            options.append((f, pid, mixed_rgb(f.rgb, bg, cov[pid])))
    if allow_empty:
        options.append((None, None, np.asarray(bg, dtype=float)))
    opt_lab = rgb_to_lab(np.array([o[2] for o in options]))

    def pick(target_rgb):
        lab = rgb_to_lab(np.asarray(target_rgb, dtype=float))
        return int(((opt_lab - lab) ** 2).sum(1).argmin())

    err: Dict[Tuple[int, int], np.ndarray] = {}
    by_col: Dict[int, list] = {}
    for c in grid.cells:
        by_col.setdefault(c.col, []).append(c)
    for col in sorted(by_col):
        for c in sorted(by_col[col], key=lambda c: c.row):
            target = np.array(c.rgb_mean, dtype=float)
            if dither:
                target = np.clip(target + err.pop((c.col, c.row), 0.0), 0, 255)
            i = pick(target)
            f, pid, mix = options[i]
            if f is None:
                c.color, c.color_name, c.pattern = bg_hex, "background", None
            else:
                c.color, c.color_name, c.pattern = f.hex, f.name, pid
            if dither:
                e = (target - mix) * 0.5
                err[(c.col, c.row + 1)] = err.get((c.col, c.row + 1), 0.0) + e * 0.5
                err[(c.col + 1, c.row)] = err.get((c.col + 1, c.row), 0.0) + e * 0.5
    return cov
