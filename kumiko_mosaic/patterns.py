"""Pattern assignment rules (which insert goes in which cell).

Pattern ids are strings from `inserts.catalogue()`: procedural ids such as "asanoha", "y",
"mesh2", or "ks<N>" for Paper View's insert N (via Kumiko Studio silhouettes).

Assignment modes:
  none              -> background insert only (flat coloured triangle mosaic)
  single:<id>       -> every cell gets pattern <id>
  luminance:<ids>   -> comma-separated ids ordered DARK -> LIGHT ("-" = no insert);
                       each cell picks by the luminance of its ORIGINAL image colour
  color             -> pattern per filament colour, from a {hex_or_name: id} map
"""
from __future__ import annotations

from typing import Dict, Optional

from .grid import Grid
from .imagemap import luminance
from .inserts import catalogue, pattern_name  # noqa: F401  (re-exported)


def pattern_label(pid: Optional[str]) -> str:
    return "BG" if not pid else pid


def parse_mode(mode: str):
    mode = (mode or "none").strip()
    if mode == "none":
        return ("none", None)
    if mode.startswith("single:"):
        return ("single", _check(mode.split(":", 1)[1].strip()))
    if mode.startswith("luminance:"):
        ids = [x.strip() for x in mode.split(":", 1)[1].split(",") if x.strip()]
        if not ids:
            raise ValueError("luminance mode needs at least one id")
        return ("luminance", [None if x in ("-", "0", "none") else _check(x) for x in ids])
    if mode == "color":
        return ("color", None)
    raise ValueError(f"unknown pattern mode: {mode}")


def _check(pid: str) -> str:
    if pid.isdigit():                      # bare number -> Paper View insert number
        pid = f"ks{int(pid)}"
    if pid not in catalogue():
        raise ValueError(f"unknown pattern id '{pid}'. Known: {', '.join(catalogue())}")
    return pid


def assign_patterns(grid: Grid, mode: str, color_map: Optional[Dict[str, str]] = None,
                    edge_halves: str = "pattern") -> None:
    kind, arg = parse_mode(mode)
    cmap = {}
    for k, v in (color_map or {}).items():
        cmap[k.upper() if k.startswith("#") else k] = _check(str(v)) if v not in (None, "", "-", 0) else None
    for c in grid.cells:
        if kind == "none":
            pid = None
        elif kind == "single":
            pid = arg
        elif kind == "luminance":
            L = luminance(c.rgb_mean or (0, 0, 0))
            pid = arg[min(len(arg) - 1, int(L * len(arg)))]
        else:
            pid = cmap.get(c.color.upper()) or cmap.get(c.color_name or "") or None
        if c.is_half and edge_halves == "background":
            pid = None
        c.pattern = pid
