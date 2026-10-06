"""Refresh kumiko_mosaic/data/filaments_pla.json from SpoolmanDB (https://github.com/Donkie/SpoolmanDB, MIT).

Keeps PLA and PLA+ filaments with a single flat colour (no translucent, glow, colour-shifting, sparkle, wood or
carbon-fibre families), and
de-duplicates by (manufacturer, name, hex) since the source lists one row per spool size and diameter.
Usage: python scripts/update_filament_db.py
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://donkie.github.io/SpoolmanDB/filaments.json"
LICENSE_URL = "https://raw.githubusercontent.com/Donkie/SpoolmanDB/main/LICENSE"
OUT = ROOT / "kumiko_mosaic" / "data"
MATERIALS = {"PLA", "PLA+"}
# Filaments whose printed colour is not one flat colour (colour-shifting, dual or multi tone, sparkle, wood,
# carbon fibre...) are stored with a single hex in the source but would not match it on a print.
NOT_FLAT = re.compile(r"mystic|magic|rainbow|gradient|dual|tri-?\s?colou?r|chameleon|galaxy|marble|glitter|sparkl|"
                      r"\bwood|carbon|fib(er|re)|granite|stone|multi|duo|shift|iridescent|aurora|nebula|"
                      r"twilight|candy|opal|pearlescent|holo", re.I)


def clean(s: str) -> str:
    s = s.replace(" ", " ").replace("™", "").replace("®", "")
    return re.sub(r"\s+", " ", s).strip()


def main():
    req = urllib.request.Request(URL, headers={"User-Agent": "kumiko-mosaic filament db update"})
    data = json.load(urllib.request.urlopen(req, timeout=120))
    seen, rows = set(), []
    for x in data:
        if x.get("material") not in MATERIALS or x.get("color_hexes") or x.get("translucent") or x.get("glow"):
            continue
        if x.get("pattern") or NOT_FLAT.search(x["name"]) or NOT_FLAT.search(x.get("finish") or ""):
            continue
        hx = (x.get("color_hex") or "").strip().lstrip("#").upper()
        if not re.fullmatch(r"[0-9A-F]{6}", hx):
            continue
        key = (x["manufacturer"], clean(x["name"]), hx)
        if key in seen:
            continue
        seen.add(key)
        rows.append([clean(x["manufacturer"]), clean(x["name"]), x["material"], "#" + hx, x.get("finish") or ""])
    rows.sort(key=lambda r: (r[0].lower(), r[1].lower()))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "filaments_pla.json").write_text(json.dumps({"source": "SpoolmanDB", "url": URL, "count": len(rows),
                                                         "columns": ["manufacturer", "name", "material", "hex", "finish"],
                                                         "rows": rows}, ensure_ascii=False, separators=(",", ":")))
    lic = urllib.request.urlopen(urllib.request.Request(LICENSE_URL, headers={"User-Agent": "kumiko-mosaic"}), timeout=60).read().decode()
    (OUT / "SPOOLMANDB_LICENSE.txt").write_text("Filament colour data in filaments_pla.json comes from SpoolmanDB\n"
                                                 "(https://github.com/Donkie/SpoolmanDB), used under its MIT licence:\n\n" + lic)
    brands = sorted({r[0] for r in rows})
    print(f"{len(rows)} filaments from {len(brands)} brands -> {OUT / 'filaments_pla.json'} "
          f"({(OUT / 'filaments_pla.json').stat().st_size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
