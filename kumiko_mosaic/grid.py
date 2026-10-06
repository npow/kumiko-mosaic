"""Triangular lattice model for Paper View's rectangular kumiko frame.

Conventions (verified against his wall-panel photos and catalogue panel sizes):

* Bars run VERTICALLY. The panel is `cols` columns wide; each column is one triangle altitude
  wide (pitch * sqrt(3)/2). The panel is `rows` pitches tall; each full triangle spans one
  pitch along a column. Triangles in a column alternate pointing left / right.
* `pitch` is the triangle side length (bar spacing along a bar), default 50 mm.
* Adjacent columns are offset by half a pitch, so every column has a half triangle at the top
  and at the bottom edge. Column area = 2*rows triangle areas = (2*rows - 1) full + 2 halves.
* Inner size (outer faces of the border bars): width = mitsuke + cols*pitch*sqrt(3)/2,
  height = 2*mitsuke + rows*pitch. Outer size = inner + 2*border (his border is 6 mm).
  Check: his 218 x 81.8 cm panel = 50 cols x 16 rows at 50 mm; 79.4 x 121.8 cm = 18 x 24.
* Phase: a column with phase 0 has lattice vertices on its edges at y = 0, pitch, 2*pitch ...
  ("vertices at corners"); phase 1 is shifted by pitch/2 ("sides at corners": the half cell at
  the top has its right angle in the corner). Paper View's frame has phase 0 in the RIGHTMOST
  column (derived from his "even width -> flip half triangles" kumikodesigner rule plus a photo
  of an even-width panel whose top-left corner is a side-at-corner half cell).

Coordinates: mm, origin at the top-left lattice corner (centreline of the left border bar,
INNER face of the top bar: the top and bottom bars lie fully outside the lattice, the left and
right bars half), x to the right, y DOWN (screen orientation of the hung panel).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Iterator, List, Literal, Optional

SQRT3 = math.sqrt(3.0)
ALT = SQRT3 / 2.0  # triangle altitude in units of pitch

Orientation = Literal["left", "right", "half"]   # apex direction for full cells
HalfKind = Literal["L", "R"]


@dataclass(frozen=True)
class FrameSpec:
    pitch: float = 50.0        # Grid_Pitch
    mitsuke: float = 3.0       # Grid_Thickness (bar width)
    border: float = 6.0        # Border_Thickness (his default 2 x mitsuke)
    frame_depth: float = 12.0  # Frame_Depth
    insert_depth: float = 11.0 # his inserts are 11 mm deep (12 mm frame)

    @property
    def col_width(self) -> float:
        return self.pitch * ALT

    @property
    def inner_side(self) -> float:
        """Tip-to-tip size of the triangular opening = pitch - sqrt(3)*mitsuke (44.80 at 50/3)."""
        return self.pitch - SQRT3 * self.mitsuke


@dataclass
class Cell:
    """A lattice face clipped to the frame rectangle.

    orientation: "left" (apex on the column's left edge), "right", or "half".
    half: for half cells, handedness of the 30-60-90 triangle seen from the front of the panel:
      "L" = the long leg (one altitude) lies 90 degrees COUNTER-clockwise from the short leg
      (half a pitch), both measured from the right-angle corner; "R" = clockwise. Two halves of
      the same kind are the same printed part (180 degree turn); L and R are mirror images
      (Paper View's "Alternative Cut"). Matches geometry.canonical_cell_polys.
    """
    col: int
    row: int                       # index along the column, 0 = top-most cell
    orientation: Orientation
    poly: List[tuple]
    half: Optional[HalfKind] = None
    side: Optional[str] = None     # "top" / "bottom" for half cells
    color: Optional[str] = None       # strip (pattern insert) filament
    color_name: Optional[str] = None
    bg_color: Optional[str] = None    # background insert filament for this cell (None = panel default)
    bg_name: Optional[str] = None
    pattern: Optional[str] = None  # insert pattern id, None = background only
    rotation: int = 0              # extra pattern rotation in the cell (0/120/240), reserved
    rgb_mean: Optional[tuple] = None

    @property
    def is_half(self) -> bool:
        return self.orientation == "half"

    @property
    def centroid(self) -> tuple:
        xs = [p[0] for p in self.poly]
        ys = [p[1] for p in self.poly]
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    @property
    def apex_angle_deg(self) -> float:
        """Rotation (CCW, degrees, y-up maths) that takes an apex-up canonical triangle onto
        this full cell. right-pointing = -90, left-pointing = +90."""
        return 90.0 if self.orientation == "left" else -90.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["centroid"] = self.centroid
        return d


@dataclass
class Grid:
    spec: FrameSpec
    cols: int
    rows: int
    phase_left: int = 1          # phase of column 0
    cells: List[Cell] = field(default_factory=list)

    @property
    def lattice_width(self) -> float:
        return self.cols * self.spec.col_width

    @property
    def lattice_height(self) -> float:
        return self.rows * self.spec.pitch

    @property
    def inner_width(self) -> float:
        return self.spec.mitsuke + self.lattice_width

    @property
    def inner_height(self) -> float:
        return 2 * self.spec.mitsuke + self.lattice_height

    @property
    def outer_width(self) -> float:
        return self.inner_width + 2 * self.spec.border

    @property
    def outer_height(self) -> float:
        return self.inner_height + 2 * self.spec.border

    @property
    def width_is_even(self) -> bool:
        return self.cols % 2 == 0

    @property
    def n_full(self) -> int:
        return self.cols * (2 * self.rows - 1)

    @property
    def n_half(self) -> int:
        return 2 * self.cols

    def column_phase(self, col: int) -> int:
        return (self.phase_left + col) % 2

    def full_cells(self) -> Iterator[Cell]:
        return (c for c in self.cells if not c.is_half)

    def half_cells(self) -> Iterator[Cell]:
        return (c for c in self.cells if c.is_half)

    def summary(self) -> dict:
        return {
            "triangle_generator": [self.cols, self.rows],
            "cols_triangle_altitudes_wide": self.cols,
            "rows_pitches_tall": self.rows,
            "kumikodesigner_size_a_height": self.rows,
            "kumikodesigner_size_b_width": self.cols,
            "kumikodesigner_flip_half_triangles": self.width_is_even,
            "width_parity": "even" if self.width_is_even else "odd",
            "tr_wall_hanger_plate": ("Even Width - TR Wall Hanger" if self.width_is_even
                                     else "Odd Width - TR Wall Hanger"),
            "left_column_phase": self.phase_left,
            "top_left_corner": "side at corner" if self.phase_left == 1 else "vertex at corner",
            "pitch_mm": self.spec.pitch,
            "grid_thickness_mm": self.spec.mitsuke,
            "border_mm": self.spec.border,
            "insert_tip_spacing_mm": round(self.spec.inner_side, 3),
            "lattice_mm": [round(self.lattice_width, 1), round(self.lattice_height, 1)],
            "inner_mm": [round(self.inner_width, 1), round(self.inner_height, 1)],
            "outer_mm": [round(self.outer_width, 1), round(self.outer_height, 1)],
            "full_cells": self.n_full,
            "half_cells": self.n_half,
        }


def paperview_phase_left(cols: int) -> int:
    """Rightmost column has phase 0 (vertices at the TR/BR corners)."""
    return (cols - 1) % 2


def build_grid(spec: FrameSpec, cols: int, rows: int, orientation: str = "auto") -> Grid:
    """orientation: 'auto' (Paper View rule), 'side-corners' (phase 1 at the left column) or
    'vertex-corners' (phase 0 at the left column), matching Kumiko Studio's two options."""
    if cols < 1 or rows < 1:
        raise ValueError("cols and rows must be >= 1")
    if orientation == "auto":
        phase_left = paperview_phase_left(cols)
    elif orientation == "side-corners":
        phase_left = 1
    elif orientation == "vertex-corners":
        phase_left = 0
    else:
        raise ValueError(f"unknown orientation {orientation}")
    p = spec.pitch
    w = spec.col_width
    H = rows * p
    g = Grid(spec=spec, cols=cols, rows=rows, phase_left=phase_left)
    for c in range(cols):
        x0, x1 = c * w, (c + 1) * w
        off = g.column_phase(c) * p / 2.0   # y of the first lattice vertex on the LEFT edge
        # Vertices on the left edge: off + k*p ; on the right edge: off + p/2 + k*p.
        # Right-pointing face: base on the left edge [a, a+p], apex at (x1, a+p/2).
        # Left-pointing face: base on the right edge [b, b+p], apex at (x0, b+p/2).
        faces = []
        k = -1
        while True:
            a = off + k * p
            if a >= H:
                break
            if a + p > 0:
                faces.append(("right", a, a + p))
            k += 1
        k = -1
        while True:
            b = off + p / 2.0 + k * p
            if b >= H:
                break
            if b + p > 0:
                faces.append(("left", b, b + p))
            k += 1
        faces.sort(key=lambda f: (f[1] + f[2]) / 2.0)
        idx = 0
        for orient, a, b in faces:
            if a < -1e-9 or b > H + 1e-9:
                if a < 0:
                    side = "top"
                    if orient == "right":   # base on left edge, clipped at y=0
                        poly = [(x0, 0.0), (x0, b), (x1, 0.0)]
                        # right angle top-left; short leg down, long leg right: CCW -> "L"
                        kind = "L"
                    else:                   # base on right edge
                        poly = [(x1, 0.0), (x1, b), (x0, 0.0)]
                        # right angle top-right; short leg down, long leg left: CW -> "R"
                        kind = "R"
                else:
                    side = "bottom"
                    if orient == "right":
                        poly = [(x0, a), (x0, H), (x1, H)]
                        # right angle bottom-left; short leg up, long leg right: CW -> "R"
                        kind = "R"
                    else:
                        poly = [(x1, a), (x1, H), (x0, H)]
                        # right angle bottom-right; short leg up, long leg left: CCW -> "L"
                        kind = "L"
                cell = Cell(col=c, row=idx, orientation="half", poly=poly, half=kind, side=side)
            else:
                if orient == "right":
                    poly = [(x0, a), (x0, b), (x1, (a + b) / 2.0)]
                else:
                    poly = [(x1, a), (x1, b), (x0, (a + b) / 2.0)]
                cell = Cell(col=c, row=idx, orientation=orient, poly=poly)
            g.cells.append(cell)
            idx += 1
        assert idx == 2 * rows + 1, (c, idx, rows)
    return g


# ---- sizing -------------------------------------------------------------------------------------

DEFAULT_COLS = 40   # enough cells for a photo to read; ~1.75 m at 50 mm pitch, 1.05 m at 30 mm

def cols_for_width(spec: FrameSpec, width_mm: float, measure: str = "outer") -> int:
    avail = width_mm - (2 * spec.border + spec.mitsuke if measure == "outer" else 0.0)
    return max(1, int(math.floor(avail / spec.col_width + 1e-9)))


def rows_for_height(spec: FrameSpec, height_mm: float, measure: str = "outer") -> int:
    avail = height_mm - (2 * spec.border + 2 * spec.mitsuke if measure == "outer" else 0.0)
    return max(1, int(math.floor(avail / spec.pitch + 1e-9)))


def size_grid(spec: FrameSpec, aspect: float, *, width_mm: Optional[float] = None,
              height_mm: Optional[float] = None, cols: Optional[int] = None,
              rows: Optional[int] = None, max_cells: Optional[int] = None,
              measure: str = "outer") -> tuple:
    """Pick (cols, rows) from one constraint plus the image aspect (width/height).
    With no constraint at all, default to DEFAULT_COLS columns; the physical width then follows
    the pitch (lower the pitch for a smaller panel with the same detail)."""
    if not any(v is not None for v in (width_mm, height_mm, cols, rows, max_cells)):
        cols = DEFAULT_COLS
    w, p = spec.col_width, spec.pitch

    def rows_from_cols(c: int) -> int:
        return max(1, int(round(c * w / aspect / p)))

    def cols_from_rows(r: int) -> int:
        return max(1, int(round(r * p * aspect / w)))

    if cols is not None and rows is not None:
        return cols, rows
    if cols is not None:
        return cols, rows_from_cols(cols)
    if rows is not None:
        return cols_from_rows(rows), rows
    if width_mm is not None and height_mm is not None:
        c = cols_for_width(spec, width_mm, measure)
        r = rows_for_height(spec, height_mm, measure)
        if rows_from_cols(c) <= r:
            return c, rows_from_cols(c)
        return cols_from_rows(r), r
    if width_mm is not None:
        c = cols_for_width(spec, width_mm, measure)
        return c, rows_from_cols(c)
    if height_mm is not None:
        r = rows_for_height(spec, height_mm, measure)
        return cols_from_rows(r), r
    best = (1, 1)
    c = 1
    while True:
        r = rows_from_cols(c)
        if c * 2 * r > max_cells:
            break
        best = (c, r)
        c += 1
    return best
