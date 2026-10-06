"""Kumiko insert geometry: procedural patterns (original) and Kumiko Studio silhouettes (optional).

All patterns are defined in the NORMALISED apex-up triangle A=(0,0), B=(1,0), C=(0.5, sqrt3/2).
A pattern is a list of centre-line polylines; strips are made by buffering each polyline by
half the strip width, unioning, and clipping to the triangle. Strips that end on a triangle
edge end flush with it, which is what holds the insert in the frame.

`shape_polygon(pattern_id, side_mm, strip_mm)` returns a shapely (Multi)Polygon in mm with the
triangle's base centred on the origin... see `canonical_polygon` for the pose used by meshes.
"""
from __future__ import annotations

import json
import math
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union

SQRT3 = math.sqrt(3.0)
A, B, C = (0.0, 0.0), (1.0, 0.0), (0.5, SQRT3 / 2)
V = [A, B, C]
O = (0.5, SQRT3 / 6)                     # centroid
MID = [((B[0] + C[0]) / 2, (B[1] + C[1]) / 2),  # midpoint opposite A
       ((C[0] + A[0]) / 2, (C[1] + A[1]) / 2),  # opposite B
       ((A[0] + B[0]) / 2, (A[1] + B[1]) / 2)]  # opposite C
TRIANGLE = Polygon([A, B, C])

Pt = Tuple[float, float]


def mix(a: Pt, b: Pt, t: float) -> Pt:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def ring(pts: Sequence[Pt]) -> List[Pt]:
    return list(pts) + [pts[0]]


# ---- procedural catalogue ---------------------------------------------------------------------
# Each entry: name, description, function(detail) -> list of polylines (normalised coords).

def _spokes_vertices():
    return [[O, v] for v in V]


def _spokes_mids():
    return [[O, m] for m in MID]


def _asanoha(n):              # hemp leaf: 6 spokes; n>1 adds leaf veins
    lines = _spokes_vertices() + _spokes_mids()
    for k in range(1, n):
        t = 0.3 + 0.2 * k
        for i, v in enumerate(V):
            a, b = V[(i + 1) % 3], V[(i + 2) % 3]
            lines.append([v, mix(O, a, t), MID[i], mix(O, b, t), v])
    return lines


def _tri_rings(n):            # concentric triangles held by spokes to the vertices
    return _spokes_vertices() + [ring([mix(O, v, 0.25 + 0.7 * k / n) for v in V]) for k in range(1, n + 1)]


def _hexagram(n):             # inner rotated triangle(s) + spokes to mids -> stars when tiled
    out = _spokes_mids()
    for k in range(1, n + 1):
        t = 0.55 * k / n
        out.append(ring([mix(O, m, t * 1.6) for m in MID]))
    return out


def _mesh(n):                 # lines parallel to each edge (tsumiishi style)
    out = []
    for i, v in enumerate(V):
        a, b = V[(i + 1) % 3], V[(i + 2) % 3]
        for k in range(1, n + 1):
            t = k / (n + 1)
            out.append([mix(v, a, t), mix(v, b, t)])
    return out


def _fan(n):                  # rays from each vertex to the opposite edge
    out = []
    for i, v in enumerate(V):
        a, b = V[(i + 1) % 3], V[(i + 2) % 3]
        for k in range(1, n + 1):
            out.append([v, mix(a, b, k / (n + 1))])
    return out


def _pinwheel(n):             # chiral: each vertex to a point past the opposite midpoint
    out = []
    for i, v in enumerate(V):
        a = V[(i + 1) % 3]
        out.append([v, mix(O, a, 0.5), a])
    if n > 1:
        out += _tri_rings(1)
    return out


def _step(n):                 # stepped hemp
    out = _spokes_vertices()
    for k in range(1, n + 1):
        for i, v in enumerate(V):
            a, b = mix(v, V[(i + 1) % 3], .5), mix(v, V[(i + 2) % 3], .5)
            q = mix(v, O, 0.22 + 0.19 * k)
            out.append([a, mix(a, q, .5), q, mix(b, q, .5), b])
    return out


def _hex(n):                  # hexagon when tiled: spokes + ring(s) through edge points
    out = [[v, O] for v in V] if n > 1 else [[v, mix(v, O, 0.42)] for v in V]
    for k in range(1, n + 1):
        pts = []
        for i, v in enumerate(V):
            pts.append(mix(O, v, 0.58 / k))
            pts.append(mix(O, MID[(i + 2) % 3], 0.95 / k))
        out.append(ring(pts))
    return out


def _cross(n):
    out = []
    for i, v in enumerate(V):
        a, b = V[(i + 1) % 3], V[(i + 2) % 3]
        for k in range(1, n + 1):
            out.append([mix(v, a, 0.2 * k), mix(a, b, 0.8 - 0.2 * (k - 1))])
    return out


def _y(n):                    # plain Y (spokes to vertices); n=2 adds crossbars near the tips
    out = _spokes_vertices()
    if n >= 2:
        for v in V:
            q = mix(O, v, 0.6)
            d = (v[0] - O[0], v[1] - O[1])
            perp = (-d[1] * 0.18, d[0] * 0.18)
            out.append([(q[0] - perp[0], q[1] - perp[1]), (q[0] + perp[0], q[1] + perp[1])])
    return out


def _sunburst(n):             # rays from the centroid to edge points
    out = []
    for i, v in enumerate(V):
        a = V[(i + 1) % 3]
        for k in range(0, n + 1):
            out.append([O, mix(v, a, k / n)])
    return out


def _weave(n):                # nested triangles alternating orientation
    out = _spokes_mids()
    for k in range(1, n + 1):
        t = 0.12 + 0.14 * k
        out.append(ring([mix(v, V[(i + 1) % 3], t) for i, v in enumerate(V)]))
        out.append(ring([mix(v, V[(i + 2) % 3], t) for i, v in enumerate(V)]))
    return out


def _stripes(n):              # n lines parallel to the base, tied together by the altitude spine
    out = [[C, MID[2]]]        # spine from apex to base midpoint
    for k in range(1, n + 1):
        t = k / (n + 1)
        out.append([mix(A, C, t), mix(B, C, t)])
    return out


def _kagome(n):               # trihexagonal weave: every other line of a mesh, tied by spokes
    out = _spokes_vertices()
    for i, v in enumerate(V):
        a, b = V[(i + 1) % 3], V[(i + 2) % 3]
        for k in range(1, 2 * n + 1, 2):
            t = k / (2 * n + 1)
            out.append([mix(v, a, t), mix(v, b, t)])
    return out


def _honeycomb(n):            # hexagon cells: a triangular mesh with the centre third of each line removed
    out = []
    for i, v in enumerate(V):
        a, b = V[(i + 1) % 3], V[(i + 2) % 3]
        for k in range(1, n + 1):
            t = k / (n + 1)
            p, q = mix(v, a, t), mix(v, b, t)
            m = n + 1
            # dashes of length 1/(m) with gaps 1/(m) along the line, offset so dashes form hexagons
            segs = []
            L = 1.0
            j = 0
            while j * (2.0 / (3 * m)) < 1.0:
                s0 = j * (2.0 / (3 * m)) + (k % 2) * (1.0 / (3 * m))
                s1 = min(1.0, s0 + 1.0 / (3 * m))
                if s0 < 1.0:
                    segs.append([mix(p, q, s0), mix(p, q, s1)])
                j += 1
            out += segs
    out += _spokes_vertices()  # keep it one piece
    return out


def _shippo(n):               # overlapping circles (seven treasures) approximated by 24-gons
    import math as _m
    out = _spokes_vertices()
    r = 0.5 / n
    centres = []
    for i in range(n + 1):
        for j in range(n + 1 - i):
            x = (i + j / 2.0) / n
            y = j * SQRT3 / 2 / n
            centres.append((x, y))
    for cx, cy in centres:
        pts = [(cx + r * _m.cos(2 * _m.pi * k / 24), cy + r * _m.sin(2 * _m.pi * k / 24)) for k in range(24)]
        out.append(ring(pts))
    return out


PROCEDURAL: Dict[str, dict] = {
    "y":         {"name": "Y (jigumi core)",      "fn": _y, "detail": 1, "symmetric": True},
    "y2":        {"name": "Y with tips",          "fn": _y, "detail": 2, "symmetric": True},
    "asanoha":   {"name": "Asa-no-ha (hemp leaf)", "fn": _asanoha, "detail": 1, "symmetric": True},
    "asanoha2":  {"name": "Asa-no-ha veined",     "fn": _asanoha, "detail": 2, "symmetric": True},
    "asanoha3":  {"name": "Asa-no-ha fine",       "fn": _asanoha, "detail": 3, "symmetric": True},
    "rings2":    {"name": "Double triangle",      "fn": _tri_rings, "detail": 2, "symmetric": True},
    "rings3":    {"name": "Triple triangle",      "fn": _tri_rings, "detail": 3, "symmetric": True},
    "hexagram":  {"name": "Star (hexagram when tiled)", "fn": _hexagram, "detail": 1, "symmetric": True},
    "hexagram2": {"name": "Double star",          "fn": _hexagram, "detail": 2, "symmetric": True},
    "mesh2":     {"name": "Mesh 2 (tsumi-ishi)",  "fn": _mesh, "detail": 2, "symmetric": True},
    "mesh3":     {"name": "Mesh 3",               "fn": _mesh, "detail": 3, "symmetric": True},
    "mesh4":     {"name": "Mesh 4",               "fn": _mesh, "detail": 4, "symmetric": True},
    "mesh5":     {"name": "Mesh 5",               "fn": _mesh, "detail": 5, "symmetric": True},
    "mesh6":     {"name": "Mesh 6",               "fn": _mesh, "detail": 6, "symmetric": True},
    "fan3":      {"name": "Fan 3",                "fn": _fan, "detail": 3, "symmetric": True},
    "fan5":      {"name": "Fan 5",                "fn": _fan, "detail": 5, "symmetric": True},
    "sunburst4": {"name": "Sunburst",             "fn": _sunburst, "detail": 4, "symmetric": True},
    "pinwheel":  {"name": "Pinwheel (chiral)",    "fn": _pinwheel, "detail": 1, "symmetric": False},
    "step2":     {"name": "Stepped hemp",         "fn": _step, "detail": 2, "symmetric": True},
    "hex":       {"name": "Hexagon (kikko)",      "fn": _hex, "detail": 1, "symmetric": True},
    "hex2":      {"name": "Double hexagon",       "fn": _hex, "detail": 2, "symmetric": True},
    "cross2":    {"name": "Crossed",              "fn": _cross, "detail": 2, "symmetric": True},
    "weave2":    {"name": "Weave",                "fn": _weave, "detail": 2, "symmetric": True},
    "stripes3":  {"name": "Stripes 3 (with spine)", "fn": _stripes, "detail": 3, "symmetric": False},
    "stripes4":  {"name": "Stripes 4",            "fn": _stripes, "detail": 4, "symmetric": False},
    "stripes5":  {"name": "Stripes 5",            "fn": _stripes, "detail": 5, "symmetric": False},
    "stripes6":  {"name": "Stripes 6",            "fn": _stripes, "detail": 6, "symmetric": False},
    "kagome2":   {"name": "Kagome 2",             "fn": _kagome, "detail": 2, "symmetric": True},
    "kagome3":   {"name": "Kagome 3",             "fn": _kagome, "detail": 3, "symmetric": True},
}


def procedural_polylines(pid: str) -> List[List[Pt]]:
    spec = PROCEDURAL[pid]
    return spec["fn"](spec["detail"])


# ---- Kumiko Studio silhouettes (ids "ks1".."ks40") -------------------------------------------
# Top-view outlines of Paper View's 40 inserts, extracted by the Kumiko Studio project
# (github.com/wangdrew/kumiko-designer, js/stl-patterns.js). Not bundled: downloaded on demand
# for personal use; Paper View's license does not allow redistributing derivatives.

KS_URL = "https://raw.githubusercontent.com/wangdrew/kumiko-designer/main/js/stl-patterns.js"
KS_CACHE = Path.home() / ".cache" / "kumiko_mosaic" / "stl-patterns.js"
KS_NAMES = {1: 'Asanoha', 2: 'Sprouting hemp', 3: 'Double hemp', 4: 'Layered hemp', 5: 'Double sprout',
            6: 'Hemp blossom', 7: 'Fine hemp', 8: 'Soft petals', 9: 'Layered petals', 10: 'Sprout',
            11: 'Star grid', 12: 'Small sunburst', 13: 'Open hexagon', 14: 'Woven hemp', 15: 'Petal fan',
            16: 'Open pinwheel', 17: 'Open hemp', 18: 'Crossed stars', 19: 'Open stars', 20: 'Sunburst',
            21: 'Folding fan', 22: 'Stepped hemp', 23: 'Fine stepped hemp', 24: 'Star lattice',
            25: 'Hemp & stars', 26: 'Triangle weave', 27: 'Woven cross', 28: 'Hemp weave',
            29: 'Fine woven cross', 30: 'Layered weave', 31: 'Fine triangles', 32: 'Pinwheel',
            33: 'Double outline', 34: 'Open hexagons', 35: 'Star & hexagon', 36: 'Hexagon blossom',
            37: 'Triple outline', 38: 'Honeycomb hemp', 39: 'Angular blossom', 40: 'Honeycomb'}
KS_GLUE = {10, 11, 12, 13, 16, 40}   # Kumiko Studio flags these as needing glue (loose parts)
_KS: Optional[Dict[int, dict]] = None


def kumiko_studio_data(path: Optional[str] = None, download: bool = True) -> Dict[int, dict]:
    global _KS
    if _KS is not None:
        return _KS
    p = Path(path) if path else KS_CACHE
    if not p.exists():
        if not download:
            raise FileNotFoundError(f"{p} missing; run with download=True")
        p.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(KS_URL, timeout=60) as r:
            p.write_bytes(r.read())
    txt = p.read_text()
    i = txt.find("KumikoStlPatterns")
    j = txt.find("{", i)
    if i < 0 or j < 0:
        raise ValueError("unexpected stl-patterns.js format")
    data, _ = json.JSONDecoder().raw_decode(txt[j:])
    _KS = {int(k): v for k, v in data.items()}
    return _KS


def kumiko_studio_polygon(pid: int, strip_mm: float, side_mm: float) -> Polygon:
    """Normalised polygon (holes via even-odd) of Paper View insert `pid`, with strips offset
    so that the strip width is `strip_mm` at the given tip spacing (nominal 2 mm at 44.8)."""
    d = kumiko_studio_data()[pid]
    rings = [Polygon(c) for c in d["contours"] if len(c) >= 3]
    # even-odd assembly: outer rings have negative winding in the source
    shape = None
    for r in rings:
        shape = r if shape is None else shape.symmetric_difference(r)
    shape = shape.buffer(0)
    delta = (strip_mm / side_mm - 2.0 / d["sourceSideMm"]) / 2.0
    if abs(delta) > 1e-9:
        shape = shape.buffer(delta, join_style="mitre", cap_style="flat").intersection(TRIANGLE)
    return shape


# ---- public API -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class PatternInfo:
    id: str
    name: str
    source: str          # "procedural" | "kumiko-studio"
    symmetric: Optional[bool]
    note: str = ""


def catalogue(include_ks: bool = True) -> Dict[str, PatternInfo]:
    out = {k: PatternInfo(k, v["name"], "procedural", v["symmetric"]) for k, v in PROCEDURAL.items()}
    if include_ks:
        for i, n in KS_NAMES.items():
            out[f"ks{i}"] = PatternInfo(f"ks{i}", f"Paper View #{i} ({n})", "kumiko-studio", None,
                                        "needs glue" if i in KS_GLUE else "")
    return out


def pattern_name(pid: Optional[str]) -> str:
    if not pid:
        return "background only"
    c = catalogue().get(pid)
    return f"{pid} {c.name}" if c else pid


def normalised_polygon(pid: str, strip_mm: float, side_mm: float) -> Polygon:
    """Insert silhouette in normalised triangle coordinates for strip width strip_mm at tip
    spacing side_mm."""
    if pid.startswith("ks"):
        return kumiko_studio_polygon(int(pid[2:]), strip_mm, side_mm)
    if pid not in PROCEDURAL:
        raise KeyError(f"unknown pattern {pid}")
    w = strip_mm / side_mm
    strips = [LineString(pl).buffer(w / 2.0, cap_style="flat", join_style="mitre", mitre_limit=4.0)
              for pl in procedural_polylines(pid) if len(pl) >= 2]
    shape = unary_union(strips).intersection(TRIANGLE)
    return shape


def canonical_polygon(pid: str, side_mm: float, strip_mm: float, clearance_mm: float = 0.0,
                      half: Optional[str] = None) -> Polygon:
    """Silhouette in mm, apex up, centroid at the origin, optionally cut to a half.

    The outer triangle is inset by `clearance_mm` on every edge (printed parts need a little
    play). half='left' keeps x <= -clearance (the apex-left half), 'right' keeps x >= clearance,
    so the cut edge also has clearance from the top/bottom bar it rests against.
    """
    s = side_mm - 2 * SQRT3 * clearance_mm
    shape = normalised_polygon(pid, strip_mm, s)
    from shapely.affinity import scale, translate
    shape = scale(shape, s, s, origin=(0, 0))
    shape = translate(shape, -0.5 * s, -SQRT3 / 6 * s)
    if half:
        h = SQRT3 / 2 * s
        c = clearance_mm
        box = Polygon([(-s, -h), (-c, -h), (-c, h), (-s, h)]) if half == "left" else \
            Polygon([(c, -h), (s, -h), (s, h), (c, h)])
        shape = shape.intersection(box)
    return shape


def polygon_to_svg_paths(shape, transform=None, nd: int = 3) -> str:
    """Polygon/MultiPolygon -> SVG path data (even-odd fill)."""
    geoms = list(shape.geoms) if isinstance(shape, MultiPolygon) else [shape]
    parts = []
    for g in geoms:
        if g.is_empty:
            continue
        for ring_coords in [g.exterior.coords] + [i.coords for i in g.interiors]:
            pts = [transform(x, y) if transform else (x, y) for x, y in ring_coords]
            parts.append("M" + " L".join(f"{x:.{nd}f},{y:.{nd}f}" for x, y in pts) + " Z")
    return " ".join(parts)


# ---- extrusion and validation -------------------------------------------------------------------

def extrude_shapely(shape, depth: float):
    """Extrude a shapely Polygon/MultiPolygon to a watertight prism (z from 0 to depth).

    Caps are triangulated with a constrained Delaunay triangulation of the polygon, which uses
    exactly the ring vertices, so side walls share vertices with the caps and the result is
    closed by construction. Returns (vertices (n,3), faces (m,3))."""
    import numpy as np
    import shapely
    from shapely.geometry.polygon import orient
    geoms = list(shape.geoms) if isinstance(shape, MultiPolygon) else [shape]
    V_all, F_all, base = [], [], 0
    for g in geoms:
        if g.is_empty or g.area < 1e-9:
            continue
        g = orient(g.simplify(1e-5), sign=1.0)            # CCW exterior, CW holes
        rings = [list(g.exterior.coords)[:-1]] + [list(i.coords)[:-1] for i in g.interiors]
        # unique vertices (rings that touch share a vertex) so the mesh is closed without merging
        index: dict = {}
        pts_list = []
        ring_idx = []
        for r in rings:
            ri = []
            for x, y in r:
                key = (round(x, 7), round(y, 7))
                if key not in index:
                    index[key] = len(pts_list)
                    pts_list.append((x, y))
                ri.append(index[key])
            ring_idx.append(ri)
        pts = np.array(pts_list, dtype=np.float64)
        n = len(pts)
        tri = []
        for t in shapely.constrained_delaunay_triangles(g).geoms:
            c = list(t.exterior.coords)[:3]
            idx = [index[(round(x, 7), round(y, 7))] for x, y in c]
            a, b, cc = pts[idx[0]], pts[idx[1]], pts[idx[2]]
            if (b[0] - a[0]) * (cc[1] - a[1]) - (b[1] - a[1]) * (cc[0] - a[0]) < 0:
                idx = idx[::-1]
            tri.append(idx)
        tri = np.array(tri, dtype=np.int64)
        bottom = np.c_[pts, np.zeros(n)]
        top = np.c_[pts, np.full(n, depth)]
        faces = [tri + n + base, tri[:, ::-1] + base]
        for ri in ring_idx:
            i0 = np.array(ri)
            i1 = np.roll(i0, -1)
            faces.append(np.c_[i0, i1, i1 + n] + base)
            faces.append(np.c_[i0, i1 + n, i0 + n] + base)
        V_all.append(np.vstack([bottom, top]))
        F_all.append(np.vstack(faces))
        base += 2 * n
    if not V_all:
        raise ValueError("empty shape")
    return np.vstack(V_all), np.vstack(F_all)


def validate_pattern(pid: str, side_mm: float = 44.8038, strip_mm: float = 2.0) -> dict:
    """Is the insert one connected piece, and does it touch all three frame edges?"""
    shape = canonical_polygon(pid, side_mm, strip_mm)
    parts = len(shape.geoms) if isinstance(shape, MultiPolygon) else 1
    s = side_mm
    h = SQRT3 / 2 * s
    tri = Polygon([(-s / 2, -h / 3), (s / 2, -h / 3), (0, 2 * h / 3)])
    edges = [LineString([tri.exterior.coords[i], tri.exterior.coords[i + 1]]) for i in range(3)]
    touches = sum(1 for e in edges if shape.buffer(1e-6).intersects(e))
    return {"id": pid, "parts": parts, "edges_touched": touches, "area_mm2": round(shape.area, 1),
            "ok": parts == 1 and touches == 3}


def min_hole_width(shape) -> Optional[float]:
    """Smallest inscribed width of any hole in the silhouette (None if there are no holes)."""
    from shapely.geometry import Polygon as _P
    geoms = list(shape.geoms) if isinstance(shape, MultiPolygon) else [shape]
    best = None
    for g in geoms:
        for h in g.interiors:
            hp = _P(h)
            lo, hi = 0.0, 20.0
            for _ in range(18):
                mid = (lo + hi) / 2
                if hp.buffer(-mid).is_empty:
                    hi = mid
                else:
                    lo = mid
            w = 2 * lo
            best = w if best is None else min(best, w)
    return best


def printable(pid: str, side_mm: float, strip_mm: float, min_hole_mm: float) -> bool:
    """A pattern is usable at this size if it is one connected piece touching all edges and
    none of its openings is narrower than min_hole_mm (so lines stay lines)."""
    v = validate_pattern(pid, side_mm, strip_mm)
    if not v["ok"]:
        return False
    w = min_hole_width(canonical_polygon(pid, side_mm, strip_mm))
    return w is None or w >= min_hole_mm
