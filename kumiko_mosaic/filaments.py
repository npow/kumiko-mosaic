"""Purchasable filament catalogue (Bambu Lab PLA Matte and PLA Basic, official hex codes from
Bambu's colour tables, as collected by the Kumiko Studio project). Add your own below or pass
--palette with the filaments you already have."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .imagemap import Filament, hex_to_rgb

BAMBU_MATTE = [
    ("Ivory White", "#FFFFFF"), ("Bone White", "#CBC6B8"), ("Desert Tan", "#E8DBB7"), ("Latte Brown", "#D3B7A7"),
    ("Caramel", "#AE835B"), ("Terracotta", "#B15533"), ("Dark Brown", "#7D6556"), ("Dark Chocolate", "#4D3324"),
    ("Lilac Purple", "#AE96D4"), ("Sakura Pink", "#E8AFCF"), ("Mandarin Orange", "#F99963"), ("Lemon Yellow", "#F7D959"),
    ("Plum", "#950051"), ("Scarlet Red", "#DE4343"), ("Dark Red", "#BB3D43"), ("Dark Green", "#68724D"),
    ("Grass Green", "#61C680"), ("Apple Green", "#C2E189"), ("Ice Blue", "#A3D8E1"), ("Sky Blue", "#56B7E6"),
    ("Marine Blue", "#0078BF"), ("Dark Blue", "#042F56"), ("Ash Gray", "#9B9EA0"), ("Nardo Gray", "#757575"),
    ("Charcoal", "#000000"),
]
BAMBU_BASIC = [
    ("Jade White", "#FFFFFF"), ("Magenta", "#EC008C"), ("Gold", "#E4BD68"), ("Mistletoe Green", "#3F8E43"),
    ("Red", "#C12E1F"), ("Purple", "#5E43B7"), ("Beige", "#F7E6DE"), ("Pink", "#F55A74"), ("Sunflower Yellow", "#FEC600"),
    ("Bronze", "#847D48"), ("Turquoise", "#00B1B7"), ("Indigo Purple", "#482960"), ("Light Gray", "#D1D3D5"),
    ("Hot Pink", "#F5547C"), ("Yellow", "#F4EE2A"), ("Cocoa Brown", "#6F5034"), ("Cyan", "#0086D6"), ("Blue Grey", "#5B6579"),
    ("Silver", "#A6A9AA"), ("Orange", "#FF6A13"), ("Bright Green", "#BECF00"), ("Brown", "#9D432C"), ("Blue", "#0A2989"),
    ("Dark Gray", "#545454"), ("Gray", "#8E9089"), ("Pumpkin Orange", "#FF9016"), ("Bambu Green", "#00AE42"),
    ("Maroon Red", "#9D2235"), ("Cobalt Blue", "#0056B8"), ("Black", "#000000"),
]

SETS: Dict[str, List[Filament]] = {
    "bambu-matte": [Filament(f"Matte {n}", h) for n, h in BAMBU_MATTE],
    "bambu-basic": [Filament(f"Basic {n}", h) for n, h in BAMBU_BASIC],
}
SETS["bambu"] = SETS["bambu-matte"] + SETS["bambu-basic"]

# ---- open database (SpoolmanDB, MIT): PLA and PLA+ from many brands ------------------------------------
DATA = Path(__file__).parent / "data" / "filaments_pla.json"
BRAND_PRIORITY = ["Bambu Lab", "Polymaker", "Prusament", "Prusa", "eSun", "Sunlu", "ELEGOO", "Overture", "Hatchbox",
                  "Creality", "ANYCUBIC", "Inland", "AmazonBasics", "Protopasta", "Formfutura", "Fillamentum"]
_DB = None


def _db() -> dict:
    global _DB
    if _DB is None:
        _DB = json.loads(DATA.read_text())
    return _DB


def brands() -> List[dict]:
    """Brands in the open database with their number of colours, best known first."""
    counts: Dict[str, int] = {}
    for r in _db()["rows"]:
        counts[r[0]] = counts.get(r[0], 0) + 1
    order = {b.lower(): i for i, b in enumerate(BRAND_PRIORITY)}
    return [{"name": b, "colours": counts[b]} for b in
            sorted(counts, key=lambda b: (order.get(b.lower(), 99), b.lower()))]


def _lab(hexes):
    import numpy as np
    from .imagemap import rgb_to_lab
    return rgb_to_lab(np.array([hex_to_rgb(h) for h in hexes], dtype=float))


def db_catalogue(brand_list: Optional[Sequence[str]] = None, materials: Sequence[str] = ("PLA", "PLA+"),
                 min_delta_e: float = 2.5) -> List[Filament]:
    """Filaments of the chosen brands (default all), named 'Brand Colour'. Colours that look the same
    (CIELAB distance below min_delta_e) are listed once, the better-known brand first, so a plan does not
    choose among hundreds of near-identical blacks."""
    import numpy as np
    want = {b.lower() for b in brand_list} if brand_list else None
    known = {b["name"].lower() for b in brands()}
    if want and not (want & known):
        raise ValueError(f"unknown brand(s) {sorted(want)}; known: {', '.join(b['name'] for b in brands())}")
    order = {b.lower(): i for i, b in enumerate(BRAND_PRIORITY)}
    rows = [r for r in _db()["rows"] if r[2] in materials and (want is None or r[0].lower() in want)]
    rows.sort(key=lambda r: (order.get(r[0].lower(), 99), r[0].lower(), r[1].lower()))
    if not rows:
        return []
    lab = _lab([r[3] for r in rows])
    keep: List[int] = []
    kept_lab = np.empty((0, 3))
    for i in range(len(rows)):
        if len(keep) and float(np.sqrt(((kept_lab - lab[i]) ** 2).sum(1)).min()) < min_delta_e:
            continue
        keep.append(i)
        kept_lab = np.vstack([kept_lab, lab[i]])
    return [Filament(f"{rows[i][0]} {rows[i][1]}", rows[i][3]) for i in keep]


def catalogue(name: str = "bambu", brand_list: Optional[Sequence[str]] = None) -> List[Filament]:
    """name: bambu | bambu-matte | bambu-basic | db (open database, optionally limited to brand_list)."""
    if name == "db":
        return db_catalogue(brand_list)
    if name not in SETS:
        raise KeyError(f"unknown filament set {name}; have {', '.join(list(SETS) + ['db'])}")
    return SETS[name]


def background_candidates(name: str, pool: Optional[Sequence[Filament]] = None) -> List[Filament]:
    """Filaments allowed as background inserts. 'all' = the whole pool; 'neutral' = the near-greys in it
    (black through white), which plan faster."""
    pool = list(pool) if pool is not None else catalogue("bambu")
    if name == "all":
        return pool
    lab = _lab([f.hex for f in pool])
    chroma = (lab[:, 1] ** 2 + lab[:, 2] ** 2) ** 0.5
    neutral = [f for f, c in zip(pool, chroma) if c < 12]
    return neutral or pool


def parse_spools(text: str) -> List[Filament]:
    """Parse a spool list: one filament per line as 'Name=#RRGGBB', 'Name,#RRGGBB' or 'Name #RRGGBB'
    (a CSV file with a header row works too). Blank lines and lines starting with # are skipped."""
    import re
    out: List[Filament] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.search(r"#?([0-9a-fA-F]{6})\s*$", line)
        if not m:
            continue                                   # header row or junk
        name = line[:m.start()].rstrip(" =,;\t\"'#").strip().strip("\"'")
        out.append(Filament(name or f"#{m.group(1).upper()}", "#" + m.group(1).upper()))
    return out


def load_spools(path: str) -> List[Filament]:
    from pathlib import Path
    return parse_spools(Path(path).read_text())
