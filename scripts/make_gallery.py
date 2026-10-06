"""Build examples/gallery: before/after pages for the sample images at 60 and 40 columns.

Usage: python scripts/make_gallery.py [samples_dir]   (default examples/samples)
Runs every sample with the default settings, writes side-by-side images, gallery.json and index.html
(served by the web app at /gallery). The run folders are deleted afterwards; only images are kept.
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
        shutil.rmtree(d, ignore_errors=True)
        g = s["grid"]
        rows.append({"variant": vname, "img": f"{name}_{vname}.jpg", "grid": g["triangle_generator"],
                     "outer": g["outer_mm"], "cells": s["counts"]["cells"],
                     "inserts": s["counts"]["pattern_inserts"], "palette": [p["hex"] for p in s["palette_used"]],
                     "backgrounds": [b["hex"] for b in s.get("backgrounds_used", [])],
                     "fidelity": (s.get("fidelity") or {}).get("mean_dE")})
        print(name, vname, g["triangle_generator"], g["outer_mm"])
    cards.append({"name": name, "source": src.name, "rows": rows})

ORDER = ["great_wave", "golden_gate_bridge", "toucan", "earth", "starry_night", "red_fuji"]
cards.sort(key=lambda c: ORDER.index(c["name"]) if c["name"] in ORDER else 99)
html = ["<!doctype html><html><head><meta charset='utf-8'><title>Kumiko mosaic gallery</title>",
        "<style>body{font-family:system-ui;background:#1b1b1f;color:#eee;margin:20px} .card{margin-bottom:40px}",
        "img{max-width:96vw;max-height:80vh;border-radius:6px} h2{color:#d2ac86} .meta{font-size:13px;color:#bbb}",
        ".sw{display:inline-block;width:12px;height:12px;border:1px solid #666;margin-right:2px}</style></head><body>",
        "<h1>Before / after</h1><p class='meta'>Source on the left, planned panel on the right, drawn with the real insert shapes. "
        "Every insert is a kumiko line pattern (2 mm strips, openings of at least 2.5 mm) in one of 8 Bambu PLA filaments, "
        "over a background insert in one of 3 catalogue colours; dark frame; at most 4 patterns per panel. "
        "Fidelity is the mean colour error at viewing distance (lower is better). More images: <a href='/catalog/' style='color:#9ec5ff'>catalog</a>.</p>",
        "<h2>Line patterns available at 50 mm pitch</h2><img src='density_ladder.png' style='max-width:96vw'>"]
for c in cards:
    html.append(f"<div class='card'><h2>{c['name']}</h2>")
    for r in c["rows"]:
        pal = "".join(f"<span class='sw' style='background:{h}'></span>" for h in r["palette"])
        bgp = "".join(f"<span class='sw' style='background:{h};border-radius:50%'></span>" for h in r.get("backgrounds", []))
        fid = f", fidelity {r['fidelity']}" if r.get("fidelity") is not None else ""
        html.append(f"<h3 style='font-size:14px'>{r['variant']}: {r['grid'][0]} x {r['grid'][1]}, "
                    f"{r['outer'][0]} x {r['outer'][1]} mm, {r['cells']} cells, {r['inserts']} pattern inserts{fid} &nbsp; strips {pal} &nbsp; backgrounds {bgp}</h3>"
                    f"<img src='{c['name']}_{r['variant']}_compare.jpg'>")
    html.append("</div>")
html.append("</body></html>")
(OUT / "index.html").write_text("\n".join(html))
(OUT / "gallery.json").write_text(json.dumps(cards, indent=1))
print("gallery written to", OUT)
