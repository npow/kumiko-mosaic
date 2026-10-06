"""Mesh generation, insert library loading, plate assembly and 3MF/STL export.

Own geometry: flat background inserts (full and half triangles), inset from the lattice cell by
mitsuke/2 + clearance. Pattern inserts cannot be generated here (Paper View's SCAD is not
public); they are loaded from a folder of STL/3MF files the user exported from his Insert
Generator or the "All inserts and triangle" print profile.

Library file naming (case-insensitive, anywhere in the file name):
    pattern_07.stl / p07.stl / 07.stl      -> full insert for pattern 7
    pattern_07_half*.stl                   -> half insert for pattern 7 (chirality auto-detected)
    background.stl, background_half*.stl   -> replaces the generated background inserts
Canonical pose after loading: footprint centred (full: centroid at origin, apex +Y;
half: right angle at origin, short leg +X, long leg +Y for kind L / -Y for kind R), Z min = 0.
"""
from __future__ import annotations

import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

import numpy as np

from . import inserts
from .bom import PartKey, Plate
from .grid import Grid, SQRT3

try:
    import trimesh  # optional: only needed for loading user meshes / STL export
except Exception:  # pragma: no cover
    trimesh = None


# ---- polygon helpers ---------------------------------------------------------------------------

def inset_convex_polygon(poly: List[Tuple[float, float]], d: float) -> List[Tuple[float, float]]:
    """Offset every edge of a convex polygon inward by d and intersect neighbouring edges."""
    n = len(poly)
    pts = [np.array(p, dtype=float) for p in poly]
    # orientation
    area2 = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    sign = 1.0 if area2 > 0 else -1.0
    lines = []
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        e = b - a
        nrm = np.array([e[1], -e[0]]) * sign  # inward normal for CCW when sign=+1
        nrm = -nrm
        nrm /= np.linalg.norm(nrm)
        lines.append((a + nrm * d, e))
    out = []
    for i in range(n):
        p1, e1 = lines[(i - 1) % n]
        p2, e2 = lines[i]
        A = np.array([[e1[0], -e2[0]], [e1[1], -e2[1]]])
        t = np.linalg.solve(A, p2 - p1)
        out.append(tuple((p1 + e1 * t[0]).tolist()))
    return out


def polygon_area(poly) -> float:
    n = len(poly)
    return abs(sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))) / 2


def extrude_polygon(poly: List[Tuple[float, float]], thickness: float) -> Tuple[np.ndarray, np.ndarray]:
    """Convex polygon -> (vertices (2n,3), faces (m,3)) prism from z=0 to z=thickness."""
    n = len(poly)
    # ensure CCW
    area2 = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))
    if area2 < 0:
        poly = poly[::-1]
    V = np.array([[x, y, 0.0] for x, y in poly] + [[x, y, thickness] for x, y in poly])
    F = []
    for i in range(1, n - 1):            # bottom (facing -z): reverse winding
        F.append([0, i + 1, i])
    for i in range(1, n - 1):            # top
        F.append([n, n + i, n + i + 1])
    for i in range(n):                   # sides
        j = (i + 1) % n
        F.append([i, j, n + j])
        F.append([i, n + j, n + i])
    return V, np.array(F, dtype=np.int64)


@dataclass
class PartMesh:
    vertices: np.ndarray  # (k,3) canonical pose
    faces: np.ndarray     # (m,3)
    source: str           # "generated" or file name

    def transformed(self, x: float, y: float, rot_deg: float) -> np.ndarray:
        th = math.radians(rot_deg)
        R = np.array([[math.cos(th), -math.sin(th), 0], [math.sin(th), math.cos(th), 0], [0, 0, 1]])
        return self.vertices @ R.T + np.array([x, y, 0.0])

    def transform_3mf(self, x: float, y: float, rot_deg: float) -> str:
        th = math.radians(rot_deg)
        c, s = math.cos(th), math.sin(th)
        # 3MF: m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32 (row vectors, translation last)
        return f"{c:.6f} {s:.6f} 0 {-s:.6f} {c:.6f} 0 0 0 1 {x:.4f} {y:.4f} 0"


# ---- generated background inserts --------------------------------------------------------------

def canonical_cell_polys(grid: Grid, clearance: float) -> Dict[str, List[Tuple[float, float]]]:
    """Canonical footprints (mm, y up) of the triangular opening, inset by `clearance`:
    'full' apex up with centroid at the origin; 'half_L' / 'half_R' with the right angle at the
    origin, short leg (side/2) along +x and long leg (height) along +y (L) or -y (R)."""
    s = grid.spec.inner_side
    h = s * SQRT3 / 2
    full = [(-s / 2, -h / 3), (s / 2, -h / 3), (0.0, 2 * h / 3)]
    half_L = [(0.0, 0.0), (s / 2, 0.0), (0.0, h)]
    half_R = [(0.0, 0.0), (s / 2, 0.0), (0.0, -h)]
    return {"full": inset_convex_polygon(full, clearance),
            "half_L": inset_convex_polygon(half_L, clearance),
            "half_R": inset_convex_polygon(half_R, clearance)}


def generated_pattern_part(grid: Grid, pattern: str, shape: str, depth: float, strip_mm: float,
                           clearance: float) -> PartMesh:
    """Pattern insert mesh in canonical pose. Half inserts are the apex-up insert cut along its
    altitude: the RIGHT half is kind L (right angle at the base midpoint, legs +x/+y), the LEFT
    half is kind R after a 180 degree turn."""
    s = grid.spec.inner_side
    h = s * SQRT3 / 2            # OPENING height: the pose is relative to the opening, not the inset part
    if shape == "full":
        poly = inserts.canonical_polygon(pattern, s, strip_mm, clearance)
        V, F = inserts.extrude_shapely(poly, depth)
    else:
        from shapely.affinity import rotate, translate
        poly = inserts.canonical_polygon(pattern, s, strip_mm, clearance,
                                         half="right" if shape == "half_L" else "left")
        poly = translate(poly, 0.0, h / 3)                 # right angle (base midpoint) -> origin
        if shape == "half_R":
            poly = rotate(poly, 180, origin=(0, 0))
        V, F = inserts.extrude_shapely(poly, depth)
    return PartMesh(np.asarray(V, dtype=float), np.asarray(F, dtype=np.int64), f"generated:{pattern}")


def generated_background_parts(grid: Grid, thickness: float, clearance: float) -> Dict[str, PartMesh]:
    out = {}
    for shape, poly in canonical_cell_polys(grid, clearance).items():
        V, F = extrude_polygon(poly, thickness)
        out[shape] = PartMesh(V, F, "generated")
    return out


# ---- user insert library ------------------------------------------------------------------------

_PAT_RE = re.compile(r"(?:pattern|insert|ks|\bp)[_ -]?(\d{1,2})(?![0-9])", re.I)
_NUM_RE = re.compile(r"(\d{1,2})(?![0-9])")


def _load_mesh_vertices_faces(path: Path) -> Tuple[np.ndarray, np.ndarray]:
    if trimesh is None:
        raise RuntimeError("trimesh is required to load insert meshes: pip install trimesh")
    m = trimesh.load(str(path), force="mesh")
    if isinstance(m, trimesh.Scene):  # pragma: no cover
        m = trimesh.util.concatenate(list(m.geometry.values()))
    return np.asarray(m.vertices, dtype=float), np.asarray(m.faces, dtype=np.int64)


def _footprint_corners(V: np.ndarray) -> np.ndarray:
    """Approximate the 3 corners of a triangular footprint from the XY convex hull."""
    from scipy.spatial import ConvexHull  # scipy is in the venv; keep import local
    xy = V[:, :2]
    hull = xy[ConvexHull(xy).vertices]
    # pick 3 hull points maximising triangle area (hull is small, brute force is fine)
    best, bestA = None, -1
    n = len(hull)
    step = max(1, n // 60)
    idx = list(range(0, n, step))
    for i in idx:
        for j in idx:
            if j <= i:
                continue
            for k in idx:
                if k <= j:
                    continue
                A = polygon_area([hull[i], hull[j], hull[k]])
                if A > bestA:
                    bestA, best = A, (hull[i], hull[j], hull[k])
    return np.array(best)


def canonicalize(V: np.ndarray, shape_hint: str) -> Tuple[np.ndarray, str]:
    """Move a loaded insert into canonical pose. Returns (vertices, shape) where shape is
    'full', 'half_L' or 'half_R'."""
    V = V.copy()
    V[:, 2] -= V[:, 2].min()
    corners = _footprint_corners(V)
    sides = [np.linalg.norm(corners[(i + 1) % 3] - corners[i]) for i in range(3)]
    ratio = max(sides) / min(sides)
    if shape_hint == "full" or (shape_hint == "auto" and ratio < 1.25):
        c = corners.mean(0)
        V[:, :2] -= c
        corners = corners - c
        # rotate so one corner points +Y
        ang = math.atan2(corners[0][1], corners[0][0])
        rot = (math.pi / 2 - ang) % (2 * math.pi / 3)
        R = np.array([[math.cos(rot), -math.sin(rot)], [math.sin(rot), math.cos(rot)]])
        V[:, :2] = V[:, :2] @ R.T
        return V, "full"
    # half: right angle is opposite the longest side
    i_long = int(np.argmax(sides))          # side from corner i_long to i_long+1
    i_right = (i_long + 2) % 3
    Vr = corners[i_right]
    a, b = corners[(i_right + 1) % 3], corners[(i_right + 2) % 3]
    la, lb = np.linalg.norm(a - Vr), np.linalg.norm(b - Vr)
    short, long_ = (a, b) if la < lb else (b, a)
    V[:, :2] -= Vr
    ang = math.atan2(*(short - Vr)[::-1])
    R = np.array([[math.cos(-ang), -math.sin(-ang)], [math.sin(-ang), math.cos(-ang)]])
    V[:, :2] = V[:, :2] @ R.T
    long_r = R @ (long_ - Vr)
    kind = "half_L" if long_r[1] > 0 else "half_R"
    return V, kind


def load_insert_library(folder: Optional[str]) -> Dict[str, PartMesh]:
    """Returns {part_id: PartMesh} with part ids like 'P07_full', 'P07_half_L', 'BG_full'."""
    lib: Dict[str, PartMesh] = {}
    if not folder:
        return lib
    for path in sorted(Path(folder).glob("*")):
        if path.suffix.lower() not in (".stl", ".3mf", ".obj", ".ply"):
            continue
        name = path.stem.lower()
        is_half = "half" in name or "split" in name
        if "background" in name or name.startswith("bg"):
            label = "BG"
        else:
            m = _PAT_RE.search(name)
            if m:
                label = f"ks{int(m.group(1))}"
            else:
                base = re.sub(r"[_ -]?(half|split).*$", "", name)
                if base in inserts.catalogue():
                    label = base
                else:
                    ms = _NUM_RE.findall(name)
                    if not ms:
                        continue
                    label = f"ks{int(ms[-1])}"
        V, F = _load_mesh_vertices_faces(path)
        V, shape = canonicalize(V, "half" if is_half else "full")
        lib[f"{label}_{shape}"] = PartMesh(V, F, path.name)
    return lib


def library_footprint(library: Dict[str, PartMesh]) -> Optional[Tuple[float, float]]:
    """(max side, max height) of the full-insert footprints in a library, for plate spacing."""
    sides, heights = [], []
    for pid, m in library.items():
        if pid.endswith("_full"):
            xy = m.vertices[:, :2]
            sides.append(float(xy[:, 0].max() - xy[:, 0].min()))
            heights.append(float(xy[:, 1].max() - xy[:, 1].min()))
    if not sides:
        return None
    return max(sides), max(heights)


# ---- plate assembly and export ---------------------------------------------------------------

def resolve_parts(plates: List[Plate], grid: Grid, bg_thickness: float, clearance: float,
                  library: Dict[str, PartMesh], insert_depth: Optional[float] = None,
                  strip_mm: float = 2.0) -> Tuple[Dict[str, PartMesh], List[str]]:
    """Map every part_id used on the plates to a mesh: user library first, then generated
    background triangles and generated pattern inserts. Returns (meshes, failed_ids)."""
    gen = generated_background_parts(grid, bg_thickness, clearance)
    depth = insert_depth if insert_depth is not None else grid.spec.insert_depth
    meshes: Dict[str, PartMesh] = {}
    missing: List[str] = []
    for p in plates:
        for pl in p.placements:
            pid = pl.part.part_id
            if pid in meshes or pid in missing:
                continue
            if pid in library:
                meshes[pid] = library[pid]
            elif pl.part.layer == "background":
                meshes[pid] = gen[pl.part.shape]
            else:
                try:
                    meshes[pid] = generated_pattern_part(grid, pl.part.pattern, pl.part.shape, depth,
                                                         strip_mm, clearance)
                except Exception as e:  # noqa: BLE001
                    missing.append(pid)
                    print(f"could not generate {pid}: {e}")
    return meshes, missing


def mesh_volume(m: PartMesh) -> float:
    V, F = m.vertices, m.faces
    t = V[F]
    return float(abs(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum()) / 6.0)


def plate_to_3mf(plate: Plate, meshes: Dict[str, PartMesh], path: Path, unit: str = "millimeter") -> None:
    """Write a vanilla 3MF: one <object> per distinct part, one build <item> per placement."""
    objs = []
    ids: Dict[str, int] = {}
    for pl in plate.placements:
        pid = pl.part.part_id
        if pid in ids or pid not in meshes:
            continue
        ids[pid] = len(ids) + 1
        m = meshes[pid]
        vs = "".join(f'<vertex x="{v[0]:.4f}" y="{v[1]:.4f}" z="{v[2]:.4f}"/>' for v in m.vertices)
        ts = "".join(f'<triangle v1="{f[0]}" v2="{f[1]}" v3="{f[2]}"/>' for f in m.faces)
        objs.append(f'<object id="{ids[pid]}" name="{escape(pid)}" type="model"><mesh><vertices>{vs}</vertices>'
                    f'<triangles>{ts}</triangles></mesh></object>')
    items = []
    for i, pl in enumerate(plate.placements):
        pid = pl.part.part_id
        if pid not in ids:
            continue
        items.append(f'<item objectid="{ids[pid]}" transform="{meshes[pid].transform_3mf(pl.x, pl.y, pl.rot_deg)}" '
                     f'partnumber="{escape(pid)}_{i+1}"/>')
    model = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
             f'<model unit="{unit}" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<metadata name="Title">{escape(plate.name())}</metadata>'
             f'<metadata name="Description">colour {escape(plate.color_name)} {plate.color}; layer {plate.layer}</metadata>'
             f'<resources>{"".join(objs)}</resources><build>{"".join(items)}</build></model>')
    ctypes = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ctypes)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)


def plate_to_stl(plate: Plate, meshes: Dict[str, PartMesh], path: Path) -> None:
    """Binary STL with all placements merged (positions preserved)."""
    tris = []
    for pl in plate.placements:
        m = meshes.get(pl.part.part_id)
        if m is None:
            continue
        V = m.transformed(pl.x, pl.y, pl.rot_deg)
        tris.append(V[m.faces])
    if not tris:
        return
    T = np.concatenate(tris)  # (n,3,3)
    n = len(T)
    e1 = T[:, 1] - T[:, 0]
    e2 = T[:, 2] - T[:, 0]
    N = np.cross(e1, e2)
    N /= np.maximum(np.linalg.norm(N, axis=1)[:, None], 1e-12)
    rec = np.zeros(n, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    rec["n"] = N
    rec["v"] = T
    with open(path, "wb") as f:
        f.write(plate.name().encode()[:80].ljust(80, b"\0"))
        f.write(np.uint32(n).tobytes())
        f.write(rec.tobytes())


def plate_svg(plate: Plate, meshes: Dict[str, PartMesh], bed_x: float, bed_y: float, scale: float = 2.0) -> str:
    """2D layout preview of a plate (y up, like a slicer top view)."""
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{bed_x*scale:.0f}" height="{bed_y*scale:.0f}" '
           f'viewBox="0 0 {bed_x} {bed_y}" font-family="sans-serif">',
           f'<rect width="{bed_x}" height="{bed_y}" fill="#222"/>']
    for pl in plate.placements:
        m = meshes.get(pl.part.part_id)
        if m is None:
            continue
        V = m.transformed(pl.x, pl.y, pl.rot_deg)
        xy = V[:, :2]
        # footprint outline via hull of the lowest layer
        from scipy.spatial import ConvexHull
        hull = xy[ConvexHull(xy).vertices]
        pts = " ".join(f"{x:.2f},{bed_y - y:.2f}" for x, y in hull)
        out.append(f'<polygon points="{pts}" fill="{plate.color}" stroke="#fff" stroke-width="0.4"/>')
        cx, cy = xy.mean(0)
        out.append(f'<text x="{cx:.1f}" y="{bed_y - cy:.1f}" font-size="4" text-anchor="middle" fill="#fff">'
                   f'{escape(pl.part.part_id.replace("_full",""))}</text>')
    out.append("</svg>")
    return "\n".join(out)
