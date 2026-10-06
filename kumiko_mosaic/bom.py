"""Bill of materials, Insert Generator checklist and plate planning."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .grid import Grid, SQRT3
from .inserts import pattern_name
from .patterns import pattern_label

Shape = str  # "full" | "half_L" | "half_R"


@dataclass(frozen=True)
class PartKey:
    layer: str            # "pattern" | "background"
    color: str            # hex
    color_name: str
    pattern: Optional[str]  # None for background parts
    shape: Shape

    @property
    def part_id(self) -> str:
        p = self.pattern if self.layer == "pattern" else "BG"
        return f"{p}_{self.shape}"

    def describe(self) -> str:
        what = pattern_name(self.pattern) if self.layer == "pattern" else "background insert"
        shp = {"full": "full triangle", "half_L": "half triangle (kind L)", "half_R": "half triangle (kind R)"}[self.shape]
        return f"{what}, {shp}, {self.color_name} ({self.color})"


@dataclass
class ColorPlan:
    """How colour is carried: which layer(s) get the image colour."""
    color_layer: str = "pattern"          # "pattern" | "background" | "both"
    background_color: str = "#000000"     # used for all backgrounds when color_layer == "pattern"
    background_name: str = "black"
    pattern_color: str = "#D2AC86"        # used for pattern inserts when color_layer == "background"
    pattern_name: str = "latte"


def cell_shape(c) -> Shape:
    return "full" if not c.is_half else f"half_{c.half}"


def build_bom(grid: Grid, plan: ColorPlan) -> Dict[PartKey, int]:
    bom: Dict[PartKey, int] = {}
    for c in grid.cells:
        shp = cell_shape(c)
        # background insert: every cell has one
        if plan.color_layer in ("background", "both"):
            bg = PartKey("background", c.color, c.color_name, None, shp)
        else:
            bg = PartKey("background", plan.background_color, plan.background_name, None, shp)
        bom[bg] = bom.get(bg, 0) + 1
        if c.pattern:
            if plan.color_layer in ("pattern", "both"):
                pk = PartKey("pattern", c.color, c.color_name, c.pattern, shp)
            else:
                pk = PartKey("pattern", plan.pattern_color, plan.pattern_name, c.pattern, shp)
            bom[pk] = bom.get(pk, 0) + 1
    return bom


def bom_rows(bom: Dict[PartKey, int]) -> List[dict]:
    rows = []
    for k, q in sorted(bom.items(), key=lambda kv: (kv[0].layer != "pattern", kv[0].color_name, kv[0].pattern or 0, kv[0].shape)):
        rows.append({"layer": k.layer, "color_name": k.color_name, "color": k.color,
                     "pattern": k.pattern or "", "shape": k.shape, "qty": q, "part_id": k.part_id,
                     "description": k.describe()})
    return rows


def insert_generator_checklist(bom: Dict[PartKey, int]) -> List[dict]:
    """Pattern insert counts per colour and pattern (full, half L, half R)."""
    agg: Dict[Tuple[str, str, Optional[int]], dict] = {}
    for k, q in bom.items():
        if k.layer != "pattern":
            continue
        key = (k.color_name, k.color, k.pattern)
        a = agg.setdefault(key, {"color_name": k.color_name, "color": k.color, "pattern": k.pattern,
                                 "pattern_name": pattern_name(k.pattern), "full": 0, "half_L": 0, "half_R": 0})
        a[k.shape] += q
    rows = sorted(agg.values(), key=lambda a: (a["color_name"], a["pattern"]))
    for a in rows:
        a["instructions"] = (f"{a['pattern_name']}: {a['full']} full"
                             + (f", {a['half_L']} half-L" if a['half_L'] else "")
                             + (f", {a['half_R']} half-R" if a['half_R'] else ""))
    return rows


# ---- plate planning ----------------------------------------------------------------------------

@dataclass
class Placement:
    part: PartKey
    x: float          # mm, position of the part's reference point on the bed
    y: float
    rot_deg: float    # rotation about Z applied to the canonical (apex-up / right-angle-at-origin) part


@dataclass
class Plate:
    index: int
    color: str
    color_name: str
    layer: str
    placements: List[Placement] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.placements)

    def contents(self) -> Dict[str, int]:
        d: Dict[str, int] = {}
        for p in self.placements:
            d[p.part.part_id] = d.get(p.part.part_id, 0) + 1
        return d

    def name(self) -> str:
        pats = sorted({p.part.pattern for p in self.placements if p.part.pattern})
        pat_s = "+".join(pats) if pats else "BG"
        return f"plate{self.index:02d}_{self.layer}_{self.color_name}_{pat_s}_x{self.count}"


@dataclass
class BedSpec:
    size_x: float = 256.0
    size_y: float = 256.0
    margin: float = 8.0      # keep inserts away from the plate edge (Paper View's advice)
    gap: float = 3.0         # clearance between parts

    @property
    def usable_x(self) -> float:
        return self.size_x - 2 * self.margin

    @property
    def usable_y(self) -> float:
        return self.size_y - 2 * self.margin


def insert_dimensions(grid: Grid, clearance: float) -> dict:
    """Footprint of a full insert: the triangular opening (pitch - sqrt3*mitsuke tip to tip)
    inset by `clearance` on each edge."""
    s = grid.spec
    side = s.inner_side - 2 * SQRT3 * clearance
    return {"opening_mm": round(s.inner_side, 3), "clearance_mm": clearance, "side_mm": side,
            "height_mm": side * SQRT3 / 2}


def plan_plates(bom: Dict[PartKey, int], grid: Grid, bed: BedSpec, clearance: float = 0.2,
                sort_by_pattern: bool = True, footprint: Optional[Tuple[float, float]] = None) -> List[Plate]:
    """Greedy strip packing. Full triangles alternate up/down along a strip; half triangles are
    packed in pairs (two of the same chirality form a rectangle). One colour and one layer per
    plate (background inserts use a different layer height than pattern inserts)."""
    dims = insert_dimensions(grid, clearance)
    side, trih = dims["side_mm"], dims["height_mm"]
    if footprint is not None:  # measured from loaded insert meshes (side, height)
        side, trih = max(side, footprint[0]), max(trih, footprint[1])
    g = bed.gap
    advance = side / 2.0 + g * 2 / SQRT3          # x advance between alternating triangles
    strip_h = trih + g
    n_strips = max(1, int(math.floor((bed.usable_y + g) / strip_h)))
    per_strip_full = max(1, int(math.floor((bed.usable_x - side) / advance)) + 1)
    rect_w = max(grid.spec.pitch / 2.0, side / 2.0 + g)  # half-pair rectangle footprint (generous)

    groups: Dict[Tuple[str, str, str], List[Tuple[PartKey, int]]] = {}
    for k, q in bom.items():
        groups.setdefault((k.layer, k.color, k.color_name), []).append((k, q))

    plates: List[Plate] = []
    idx = 1
    for (layer, color, cname), items in sorted(groups.items(), key=lambda kv: (kv[0][0] != "pattern", kv[0][2])):
        fulls: List[PartKey] = []
        halves: List[PartKey] = []
        for k, q in sorted(items, key=lambda kv: (kv[0].pattern or 0, kv[0].shape)):
            (fulls if k.shape == "full" else halves).extend([k] * q)
        if not sort_by_pattern:
            pass
        # pair halves of equal part (same pattern + chirality)
        half_pairs: List[List[PartKey]] = []
        pending: Dict[PartKey, PartKey] = {}
        for k in halves:
            if k in pending:
                half_pairs.append([pending.pop(k), k])
            else:
                pending[k] = k
        half_pairs.extend([[k] for k in pending.values()])

        fi, hi = 0, 0
        while fi < len(fulls) or hi < len(half_pairs):
            plate = Plate(idx, color, cname, layer)
            for s_i in range(n_strips):
                y_base = bed.margin + s_i * strip_h
                x = bed.margin
                n_in_strip = 0
                # full triangles
                while fi < len(fulls) and n_in_strip < per_strip_full:
                    up = (n_in_strip % 2 == 0)
                    # reference point = triangle centroid
                    cx = x + side / 2.0
                    cy = y_base + (trih / 3.0 if up else 2 * trih / 3.0)
                    plate.placements.append(Placement(fulls[fi], cx, cy, 0.0 if up else 180.0))
                    fi += 1
                    n_in_strip += 1
                    x += advance
                # fill remaining width with half pairs
                if n_in_strip:
                    x += side - advance + g  # right edge of the last triangle + gap
                while hi < len(half_pairs) and x + rect_w <= bed.margin + bed.usable_x + 1e-6:
                    pair = half_pairs[hi]
                    # rectangle [x, x+rect_w] x [y_base, y_base+trih]; part A right angle at
                    # bottom-left (rot 0), part B rotated 180 with right angle at top-right.
                    # Canonical half: right angle at origin, legs +x (short) and +/-y (long).
                    kA = pair[0]
                    yA = y_base if kA.shape == "half_L" else y_base + trih
                    plate.placements.append(Placement(kA, x, yA, 0.0))
                    if len(pair) == 2:
                        kB = pair[1]
                        yB = y_base + trih if kB.shape == "half_L" else y_base
                        plate.placements.append(Placement(kB, x + rect_w, yB, 180.0))
                    hi += 1
                    x += rect_w + g
                if fi >= len(fulls) and hi >= len(half_pairs):
                    break
            plates.append(plate)
            idx += 1
    return plates


def plate_summary(plates: List[Plate]) -> List[dict]:
    rows = []
    for p in plates:
        rows.append({"plate": p.index, "name": p.name(), "layer": p.layer, "color_name": p.color_name,
                     "color": p.color, "parts": p.count, "contents": p.contents()})
    return rows


def estimate_material(bom: Dict[PartKey, int], volumes_mm3: Dict[str, float],
                      density_g_cm3: float = 1.24, fill_factor: float = 0.85) -> Dict[str, dict]:
    """Filament estimate per colour from part volumes (solid volume x fill factor x density)."""
    out: Dict[str, dict] = {}
    for k, q in bom.items():
        e = out.setdefault(k.color_name, {"color": k.color, "pattern_inserts": 0, "background_inserts": 0, "grams": 0.0})
        vol = volumes_mm3.get(k.part_id, 0.0)
        if k.layer == "pattern":
            e["pattern_inserts"] += q
        else:
            e["background_inserts"] += q
        e["grams"] += q * vol / 1000.0 * density_g_cm3 * fill_factor
    for e in out.values():
        e["grams"] = round(e["grams"], 1)
    return out
