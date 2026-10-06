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
                  "asanoha2", "mesh6", "rings3", "asanoha3",
                  "mesh3w", "stripes6w", "mesh4w", "mesh3ww"]
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


# ---- region voting (pixel-art style) --------------------------------------------------------------

def _cell_neighbours(grid: Grid) -> Dict[Tuple[int, int], List[Tuple[int, int]]]:
    """Cells that share an edge (two vertices)."""
    key = lambda p: (round(p[0], 3), round(p[1], 3))  # noqa: E731
    edges: Dict[tuple, List[Tuple[int, int]]] = {}
    for c in grid.cells:
        pts = [key(p) for p in c.poly]
        for i in range(len(pts)):
            e = tuple(sorted((pts[i], pts[(i + 1) % len(pts)])))
            edges.setdefault(e, []).append((c.col, c.row))
    nb: Dict[Tuple[int, int], List[Tuple[int, int]]] = {(c.col, c.row): [] for c in grid.cells}
    for cells in edges.values():
        if len(cells) == 2:
            a, b = cells
            nb[a].append(b)
            nb[b].append(a)
    return nb


def assign_strips_vote(grid: Grid, palette: Sequence[Filament], bg_hex: str, ladder: Sequence[str],
                       strip_mm: float, fitted, px_per_mm: float, clearance: float = 0.0,
                       allow_empty: bool = True, max_patterns: Optional[int] = None,
                       min_hole_mm: float = MIN_HOLE_MM, smooth_mm: float = 10.0,
                       line_boost: float = 4.0, line_min_delta: float = 22.0,
                       cleanup: bool = True) -> Dict[str, float]:
    """Pixel-art style assignment. 1) edge-preserving (median) smoothing of the image at
    1 px/mm removes texture, 2) every pixel is quantised to the nearest achievable mix
    (filament x pattern density over the background), 3) each cell takes the majority label,
    so region boundaries stay crisp instead of averaging into in-between colours, 4) a thin
    coherent feature (cable, mast) may still take a cell (line boost), 5) isolated cells that
    disagree with all their neighbours are flipped to the neighbours' label when they have
    some support for it."""
    from PIL import Image, ImageDraw, ImageFilter
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
    opt_rgb = np.array([o[2] for o in options])
    opt_lab = rgb_to_lab(opt_rgb)

    # 1) smoothed working image at 1 px/mm
    W = max(1, int(round(grid.lattice_width)))
    H = max(1, int(round(grid.lattice_height)))
    base = fitted.convert("RGB").resize((W, H), Image.LANCZOS)
    k = max(3, int(round(smooth_mm)) | 1)
    img = base.filter(ImageFilter.MedianFilter(k))
    arr = np.asarray(img, dtype=np.float64)
    lab = rgb_to_lab(arr.reshape(-1, 3)).reshape(H, W, 3)          # smoothed: for the vote
    raw = rgb_to_lab(np.asarray(base, dtype=np.float64).reshape(-1, 3)).reshape(H, W, 3)  # for thin features

    # 2+3) per-cell quantise and vote
    labels: Dict[Tuple[int, int], int] = {}
    hist: Dict[Tuple[int, int], np.ndarray] = {}
    boosted: set = set()
    for c in grid.cells:
        xs = [p[0] for p in c.poly]
        ys = [p[1] for p in c.poly]
        x0, x1 = max(0, int(min(xs))), min(W, int(np.ceil(max(xs))) + 1)
        y0, y1 = max(0, int(min(ys))), min(H, int(np.ceil(max(ys))) + 1)
        mask = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(mask).polygon([(x - x0, y - y0) for x, y in c.poly], fill=255)
        m = np.asarray(mask) > 0
        pl = lab[y0:y1, x0:x1][m]
        if len(pl) == 0:
            cx, cy = c.centroid
            pl = lab[min(H - 1, int(cy)), min(W - 1, int(cx))][None, :]
        d = ((pl[:, None, :] - opt_lab[None, :, :]) ** 2).sum(-1)
        li = d.argmin(1)
        h = np.bincount(li, minlength=len(options)).astype(float)
        mode = int(h.argmax())
        # 4) line boost on the UNSMOOTHED pixels: a coherent minority far from the chosen
        #    colour takes the cell (cables, masts, outlines thinner than the smoothing radius)
        pr = raw[y0:y1, x0:x1][m]
        if line_boost > 0 and len(pr) >= 16:
            med = np.median(pr, axis=0)                 # the cell's own typical colour
            far = np.sqrt(((pr - med) ** 2).sum(1)) > line_min_delta
            f = far.mean()
            if 0.03 < f < 0.45:
                fl = pr[far]
                spread = float(np.sqrt(((fl - fl.mean(0)) ** 2).sum(1)).mean())
                if spread < 34.0:
                    # push the cell colour from its median towards the feature in proportion
                    # to the feature's area, then pick the nearest achievable mix
                    w = min(1.0, f * line_boost)
                    target = med + (fl.mean(0) - med) * w
                    new = int(((opt_lab - target) ** 2).sum(1).argmin())
                    if new != mode:
                        boosted.add((c.col, c.row))   # intentional: exempt from speckle cleanup
                        mode = new
        labels[(c.col, c.row)] = mode
        hist[(c.col, c.row)] = h / h.sum()

    # 5) speckle cleanup
    if cleanup:
        nb = _cell_neighbours(grid)
        for key_, lbl in list(labels.items()):
            if key_ in boosted:
                continue
            ns = [labels[n] for n in nb[key_]]
            if not ns or lbl in ns:
                continue
            vals, counts = np.unique(ns, return_counts=True)
            best = int(vals[counts.argmax()])
            if counts.max() >= 2 and hist[key_][best] >= 0.2:
                labels[key_] = best

    for c in grid.cells:
        f, pid, _ = options[labels[(c.col, c.row)]]
        if f is None:
            c.color, c.color_name, c.pattern = bg_hex, "background", None
        else:
            c.color, c.color_name, c.pattern = f.hex, f.name, pid
    return cov


# ---- multi-background options -------------------------------------------------------------------

def build_options(bgs: Sequence[Filament], strips: Sequence[Filament], ladder: Sequence[str],
                  cov: Dict[str, float]) -> List[tuple]:
    """Every achievable cell colour: (bg, strip or None, pattern or None, rgb)."""
    out = []
    for b in bgs:
        out.append((b, None, None, np.asarray(b.rgb, dtype=float)))
        for f in strips:
            if f.hex == b.hex:
                continue
            for pid in ladder:
                out.append((b, f, pid, mixed_rgb(f.rgb, b.rgb, cov[pid])))
    return out


def _total_error(targets_lab, areas, opts):
    lab = rgb_to_lab(np.array([o[3] for o in opts]))
    d = np.sqrt(((targets_lab[:, None, :] - lab[None, :, :]) ** 2).sum(-1)).min(1)
    return float((d * areas).sum())


def _min_dist(targets_lab, labs):
    return np.sqrt(((targets_lab[:, None, :] - labs[None, :, :]) ** 2).sum(-1)).min(1)


def choose_backgrounds(grid: Grid, candidates: Sequence[Filament], strips: Sequence[Filament],
                       ladder: Sequence[str], cov: Dict[str, float], k: int) -> List[Filament]:
    """Best subset of k background filaments given the strip filaments: exhaustive for a short
    candidate list, greedy forward selection plus a swap pass for a long one. Each candidate's best
    per-cell distance is computed once, so a trial subset is just an array minimum."""
    targets = rgb_to_lab(np.array([c.rgb_mean for c in grid.cells], dtype=float))
    areas = np.array([0.5 if c.is_half else 1.0 for c in grid.cells])
    seen, pool = set(), []
    for f in candidates:
        if f.hex not in seen:
            seen.add(f.hex)
            pool.append(f)
    k = min(k, len(pool))
    D = []
    for bg in pool:
        rgbs = [np.asarray(bg.rgb, dtype=float)] + [mixed_rgb(f.rgb, bg.rgb, cov[p])
                                                    for f in strips if f.hex != bg.hex for p in ladder]
        D.append(_min_dist(targets, rgb_to_lab(np.array(rgbs))))
    D = np.array(D)                                   # (candidates, cells)

    def total(idx):
        return float((D[list(idx)].min(0) * areas).sum())

    if len(pool) <= 12:
        best = min(combinations(range(len(pool)), k), key=total)
        return [pool[i] for i in best]
    chosen: List[int] = []
    while len(chosen) < k:
        chosen.append(min((i for i in range(len(pool)) if i not in chosen), key=lambda i: total(chosen + [i])))
    improved = True
    while improved:
        improved = False
        cur = total(chosen)
        for j in range(len(chosen)):
            for i in range(len(pool)):
                if i in chosen:
                    continue
                trial = chosen[:j] + [i] + chosen[j + 1:]
                t = total(trial)
                if t < cur - 1e-9:
                    chosen, cur, improved = trial, t, True
    return [pool[i] for i in chosen]


def choose_filaments_multi(grid: Grid, candidates: Sequence[Filament], bgs: Sequence[Filament],
                           ladder: Sequence[str], cov: Dict[str, float], k: int) -> List[Filament]:
    """Greedy strip-filament selection given a set of backgrounds."""
    targets = rgb_to_lab(np.array([c.rgb_mean for c in grid.cells], dtype=float))
    areas = np.array([0.5 if c.is_half else 1.0 for c in grid.cells])
    seen, pool = set(), []
    for f in candidates:
        if f.hex not in seen:
            seen.add(f.hex)
            pool.append(f)
    # per-candidate best distance per cell (min over bgs x ladder), computed once
    B = []
    for f in pool:
        lab = rgb_to_lab(np.array([mixed_rgb(f.rgb, b.rgb, cov[p]) for b in bgs for p in ladder if b.hex != f.hex] or [f.rgb]))
        B.append(np.sqrt(((targets[:, None, :] - lab[None, :, :]) ** 2).sum(-1)).min(1))
    B = np.array(B)
    base = np.sqrt(((targets[:, None, :] - rgb_to_lab(np.array([b.rgb for b in bgs]))[None, :, :]) ** 2).sum(-1)).min(1)

    def total(idx):
        d = B[list(idx)].min(0) if idx else np.full(len(targets), np.inf)
        return float((np.minimum(d, base) * areas).sum())

    chosen: List[int] = []
    while len(chosen) < min(k, len(pool)):
        chosen.append(min((i for i in range(len(pool)) if i not in chosen), key=lambda i: total(chosen + [i])))
    improved = True
    while improved:
        improved = False
        for j in range(len(chosen)):
            for i in range(len(pool)):
                if i in chosen:
                    continue
                trial = chosen[:j] + [i] + chosen[j + 1:]
                if total(trial) < total(chosen) - 1e-9:
                    chosen, improved = trial, True
    return [pool[i] for i in chosen]


def choose_pattern_subset_multi(grid: Grid, bgs, strips, ladder, cov, max_patterns: int) -> List[str]:
    if max_patterns >= len(ladder):
        return list(ladder)
    targets = rgb_to_lab(np.array([c.rgb_mean for c in grid.cells], dtype=float))
    areas = np.array([0.5 if c.is_half else 1.0 for c in grid.cells])
    base = _min_dist(targets, rgb_to_lab(np.array([b.rgb for b in bgs], dtype=float)))   # background only
    D = []
    for pid in ladder:
        rgbs = [mixed_rgb(f.rgb, b.rgb, cov[pid]) for b in bgs for f in strips if f.hex != b.hex]
        D.append(_min_dist(targets, rgb_to_lab(np.array(rgbs))) if rgbs else np.full(len(targets), np.inf))
    D = np.array(D)                                   # (patterns, cells)

    def total(idx):
        d = np.minimum(base, D[list(idx)].min(0)) if idx else base
        return float((d * areas).sum())

    chosen: List[int] = []
    while len(chosen) < max_patterns:
        chosen.append(min((i for i in range(len(ladder)) if i not in chosen), key=lambda i: total(chosen + [i])))
    improved = True
    while improved:
        improved = False
        cur = total(chosen)
        for j in range(len(chosen)):
            for i in range(len(ladder)):
                if i in chosen:
                    continue
                trial = chosen[:j] + [i] + chosen[j + 1:]
                t = total(trial)
                if t < cur - 1e-9:
                    chosen, cur, improved = trial, t, True
    return sorted((ladder[i] for i in chosen), key=lambda p: cov[p])


def _cell_index_map(grid: Grid, w: int, h: int, scale: float) -> np.ndarray:
    """Flat (w*h) array with the index of the cell covering each pixel, -1 outside the lattice."""
    from PIL import Image, ImageDraw
    im = Image.new("I", (w, h), 0)
    d = ImageDraw.Draw(im)
    for i, c in enumerate(grid.cells):
        d.polygon([(x * scale, y * scale) for x, y in c.poly], fill=i + 1)
    return np.asarray(im).reshape(-1).astype(np.int64) - 1


def assign_options_vote(grid: Grid, options: List[tuple], fitted, smooth_mm: float = 10.0,
                        line_boost: float = 4.0, line_min_delta: float = 22.0, line_coherence: float = 34.0,
                        cleanup: bool = True, sharpen: float = 0.0) -> None:
    """Region voting over an arbitrary option list (see assign_strips_vote for the method).
    sharpen > 0 applies an unsharp mask (percent) before smoothing.

    Vectorised: every cell is drawn once into an index image, pixels are labelled with their nearest
    option via a KD-tree, and the per-cell majority comes from one bincount. The median filter runs at
    half resolution; thin-feature detection uses the full-resolution pixels."""
    from PIL import Image, ImageFilter
    from scipy.spatial import cKDTree
    n_opt = len(options)
    opt_lab = rgb_to_lab(np.array([o[3] for o in options]))
    tree = cKDTree(opt_lab)
    n = len(grid.cells)
    W = max(1, int(round(grid.lattice_width)))
    H = max(1, int(round(grid.lattice_height)))
    base = fitted.convert("RGB").resize((W, H), Image.LANCZOS)
    if sharpen > 0:
        base = base.filter(ImageFilter.UnsharpMask(radius=max(2, int(smooth_mm)), percent=int(sharpen), threshold=3))

    # --- majority label per cell on the smoothed half-resolution image
    hw, hh = max(1, W // 2), max(1, H // 2)
    half = base.resize((hw, hh), Image.BOX).filter(ImageFilter.MedianFilter(max(3, int(round(smooth_mm / 2.0)) | 1)))
    _, pix_label = tree.query(rgb_to_lab(np.asarray(half, dtype=np.float64).reshape(-1, 3)))
    cm_half = _cell_index_map(grid, hw, hh, hw / grid.lattice_width)
    ok = cm_half >= 0
    counts = np.bincount(cm_half[ok] * n_opt + pix_label[ok], minlength=n * n_opt).reshape(n, n_opt).astype(float)
    total = counts.sum(1)
    mode = counts.argmax(1)
    for i in np.where(total == 0)[0]:                       # cell too small for the half-res map: use its centre pixel
        cx, cy = grid.cells[i].centroid
        mode[i] = pix_label[min(hh - 1, int(cy * hh / grid.lattice_height)) * hw + min(hw - 1, int(cx * hw / grid.lattice_width))]
    hist_all = counts / np.maximum(total, 1)[:, None]

    # --- thin features on the full-resolution pixels (cables, masts, outlines)
    boosted: set = set()
    if line_boost > 0:
        raw = rgb_to_lab(np.asarray(base, dtype=np.float64).reshape(-1, 3))
        cm = _cell_index_map(grid, W, H, 1.0)
        order = np.argsort(cm, kind="stable")
        sorted_ids = cm[order]
        starts = np.searchsorted(sorted_ids, np.arange(n))
        ends = np.searchsorted(sorted_ids, np.arange(n), side="right")
        for i in range(n):
            if ends[i] - starts[i] < 16:
                continue
            pr = raw[order[starts[i]:ends[i]]]
            med = np.median(pr, axis=0)
            far = np.sqrt(((pr - med) ** 2).sum(1)) > line_min_delta
            f = far.mean()
            if 0.03 < f < 0.45:
                fl = pr[far]
                spread = float(np.sqrt(((fl - fl.mean(0)) ** 2).sum(1)).mean())
                if spread < line_coherence:
                    w = min(1.0, f * line_boost)
                    new = int(tree.query(med + (fl.mean(0) - med) * w)[1])
                    if new != mode[i]:
                        boosted.add((grid.cells[i].col, grid.cells[i].row))
                        mode[i] = new
    labels: Dict[Tuple[int, int], int] = {(c.col, c.row): int(mode[i]) for i, c in enumerate(grid.cells)}
    hist: Dict[Tuple[int, int], np.ndarray] = {(c.col, c.row): hist_all[i] for i, c in enumerate(grid.cells)}
    if cleanup:
        nb = _cell_neighbours(grid)
        for key_, lbl in list(labels.items()):
            if key_ in boosted:
                continue
            ns = [labels[n] for n in nb[key_]]
            if not ns or lbl in ns:
                continue
            vals, counts = np.unique(ns, return_counts=True)
            best = int(vals[counts.argmax()])
            if counts.max() >= 2 and hist[key_][best] >= 0.2:
                labels[key_] = best
    for c in grid.cells:
        b, f, pid, _ = options[labels[(c.col, c.row)]]
        c.bg_color, c.bg_name = b.hex, b.name
        if f is None:
            c.color, c.color_name, c.pattern = b.hex, "background", None
        else:
            c.color, c.color_name, c.pattern = f.hex, f.name, pid


def fidelity_score(grid: Grid, fitted, cov: Dict[str, float], view_blur_mm: Optional[float] = None) -> dict:
    """How close the planned panel is to the image, as seen from a distance: render each
    cell as its achieved average colour (bg + strips), blur both to ~one pitch, mean CIELAB
    distance. Lower is better; ~10 is 'clearly the picture', >20 is muddy."""
    from PIL import Image, ImageDraw, ImageFilter
    W = max(1, int(round(grid.lattice_width)))
    H = max(1, int(round(grid.lattice_height)))
    plan = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(plan)
    for c in grid.cells:
        bg = np.array([int(c.bg_color[i:i + 2], 16) for i in (1, 3, 5)], dtype=float) if c.bg_color else np.zeros(3)
        if c.pattern and c.color:
            st = np.array([int(c.color[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)
            rgb = mixed_rgb(st, bg, cov.get(c.pattern, 0.3))
        else:
            rgb = bg
        d.polygon([(x, y) for x, y in c.poly], fill=tuple(int(v) for v in rgb))
    src = fitted.convert("RGB").resize((W, H), Image.LANCZOS)
    r = (view_blur_mm or grid.spec.pitch * 0.5) / 2.0
    a = np.asarray(plan.filter(ImageFilter.GaussianBlur(r)), dtype=float)
    b = np.asarray(src.filter(ImageFilter.GaussianBlur(r)), dtype=float)
    dE = np.sqrt(((rgb_to_lab(a.reshape(-1, 3)) - rgb_to_lab(b.reshape(-1, 3))) ** 2).sum(1))
    # contrast: std of L in the plan vs the source
    La = rgb_to_lab(a.reshape(-1, 3))[:, 0]
    Lb = rgb_to_lab(b.reshape(-1, 3))[:, 0]
    # structure: SSIM of lightness at a finer blur than the colour error, so edges and contrast count
    from scipy.ndimage import uniform_filter
    r2 = max(1.0, (view_blur_mm or grid.spec.pitch * 0.5) / 8.0)
    pa = rgb_to_lab(np.asarray(plan.filter(ImageFilter.GaussianBlur(r2)), dtype=float).reshape(-1, 3))[:, 0].reshape(H, W)
    sb = rgb_to_lab(np.asarray(src.filter(ImageFilter.GaussianBlur(r2)), dtype=float).reshape(-1, 3))[:, 0].reshape(H, W)
    k = max(5, int(grid.spec.pitch * 0.5))
    ma, mb = uniform_filter(pa, k), uniform_filter(sb, k)
    va, vb = uniform_filter(pa * pa, k) - ma * ma, uniform_filter(sb * sb, k) - mb * mb
    cov_ab = uniform_filter(pa * sb, k) - ma * mb
    C1, C2 = 1.0, 9.0
    ssim = ((2 * ma * mb + C1) * (2 * cov_ab + C2)) / ((ma * ma + mb * mb + C1) * (va + vb + C2))
    return {"mean_dE": round(float(dE.mean()), 2), "p90_dE": round(float(np.percentile(dE, 90)), 2),
            "ssim": round(float(ssim.mean()), 3),
            "L_std_plan": round(float(La.std()), 1), "L_std_source": round(float(Lb.std()), 1)}


def assign_options_mean(grid: Grid, options: List[tuple], dither: float = 0.0) -> None:
    """Give every cell the achievable option nearest to its mean colour (CIELAB).

    dither > 0 diffuses a fraction of each cell's colour error onto the neighbouring cells that have
    not been assigned yet (cells share an edge), so smooth gradients alternate between two nearby
    options instead of banding. 0.5 to 0.7 keeps noise low; 1.0 is full error diffusion."""
    from scipy.spatial import cKDTree
    opt_lab = rgb_to_lab(np.array([o[3] for o in options]))
    tree = cKDTree(opt_lab)
    targets = rgb_to_lab(np.array([c.rgb_mean for c in grid.cells], dtype=float))
    if dither <= 0:
        picks = tree.query(targets)[1]
    else:
        nb = _cell_neighbours(grid)
        index = {(c.col, c.row): i for i, c in enumerate(grid.cells)}
        order = sorted(range(len(grid.cells)), key=lambda i: (grid.cells[i].col, grid.cells[i].row))
        done = np.zeros(len(grid.cells), dtype=bool)
        err = np.zeros_like(targets)
        picks = np.zeros(len(grid.cells), dtype=int)
        for i in order:
            want = targets[i] + err[i]
            j = int(tree.query(want)[1])
            picks[i] = j
            done[i] = True
            e = (want - opt_lab[j]) * dither
            todo = [index[k] for k in nb[(grid.cells[i].col, grid.cells[i].row)] if not done[index[k]]]
            for t in todo:
                err[t] += e / len(todo)
    for c, j in zip(grid.cells, picks):
        b, f, pid, _ = options[int(j)]
        c.bg_color, c.bg_name = b.hex, b.name
        if f is None:
            c.color, c.color_name, c.pattern = b.hex, "background", None
        else:
            c.color, c.color_name, c.pattern = f.hex, f.name, pid
