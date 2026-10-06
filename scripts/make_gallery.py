"""Build examples/gallery: before/after pages for the sample images at a few settings.

Usage: python scripts/make_gallery.py [samples_dir]   (default examples/samples)
Each sample is run with the default settings (40 columns, auto strip matching) and at 60 columns. Serve with the web app at /gallery.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kumiko_mosaic.pipeline import Params, run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "examples" / "samples"
OUT = ROOT / "examples" / "gallery"
VARIANTS = {
    "60cols": Params(cols=60),
    "40cols": Params(),
}

OUT.mkdir(parents=True, exist_ok=True)
cards = []
for img in sorted(SAMPLES.glob("*")):
    if img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        continue
    name = img.stem
    src = OUT / f"{name}_source.jpg"
    im = Image.open(img).convert("RGB")
    im.thumbnail((900, 900))
    im.save(src, quality=85)
    rows = []
    for vname, params in VARIANTS.items():
        d = OUT / f"{name}_{vname}"
        s = run(str(img), str(d), params)
        pv = Image.open(d / "preview.png")
        pv.thumbnail((1400, 1400))
        pv.save(OUT / f"{name}_{vname}.jpg", quality=85)
        cmp_ = Image.open(d / "compare.jpg")
        cmp_.thumbnail((1600, 1600))
        cmp_.save(OUT / f"{name}_{vname}_compare.jpg", quality=85)
        shutil.rmtree(d / "plates", ignore_errors=True)
        g = s["grid"]
        rows.append({"variant": vname, "img": f"{name}_{vname}.jpg", "grid": g["triangle_generator"],
                     "outer": g["outer_mm"], "cells": s["counts"]["cells"],
                     "inserts": s["counts"]["pattern_inserts"], "palette": [p["hex"] for p in s["palette_used"]],
                     "backgrounds": [b["hex"] for b in s.get("backgrounds_used", [])],
                     "fidelity": (s.get("fidelity") or {}).get("mean_dE")})
        print(name, vname, g["triangle_generator"], g["outer_mm"])
    cards.append({"name": name, "source": src.name, "rows": rows})

html = ["<!doctype html><html><head><meta charset='utf-8'><title>Kumiko mosaic gallery</title>",
        "<style>body{font-family:system-ui;background:#1b1b1f;color:#eee;margin:20px} .card{margin-bottom:40px}",
        ".row{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap} img{max-width:46vw;max-height:70vh;border-radius:6px}",
        "h2{color:#d2ac86} .meta{font-size:13px;color:#bbb} .sw{display:inline-block;width:12px;height:12px;border:1px solid #666;margin-right:2px}</style></head><body>",
        "<h1>Before / after</h1><p class='meta'>Left: source image as fitted to the lattice. Right: the planned panel drawn with the real insert silhouettes "
        "(strips carry the colour over a black background; density chosen per cell to match the image). "
        "Default = 40 columns; the mm size follows the pitch (50 mm here), so lower the pitch for a smaller panel with the same detail.</p>"]
for c in cards:
    html.append(f"<div class='card'><h2>{c['name']}</h2>")
    for r in c["rows"]:
        pal = "".join(f"<span class='sw' style='background:{h}'></span>" for h in r["palette"])
        bgp = "".join(f"<span class='sw' style='background:{h};border-radius:50%'></span>" for h in r.get("backgrounds", []))
        fid = f", fidelity error {r['fidelity']}" if r.get("fidelity") is not None else ""
        html.append(f"<h3 style='font-size:14px'>{r['variant']}: {r['grid'][0]} x {r['grid'][1]}, "
                    f"{r['outer'][0]} x {r['outer'][1]} mm, {r['cells']} cells, {r['inserts']} pattern inserts{fid} &nbsp; strips {pal} &nbsp; backgrounds {bgp}</h3>"
                    f"<div class='row'><img src='{c['source']}'><img src='{r['img']}'></div>")
    html.append("</div>")
html.append("</body></html>")
(OUT / "index.html").write_text("\n".join(html))
(OUT / "gallery.json").write_text(json.dumps(cards, indent=1))
print("gallery written to", OUT)
