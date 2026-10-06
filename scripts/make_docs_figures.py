"""Generate the README figures from the code: docs/img/patterns.png and docs/img/filaments.png.

Usage: python scripts/make_docs_figures.py
The pattern sheet shows exactly the patterns the planner may use at the default pitch and strip
width (same filter as the planner), sorted sparse to dense.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kumiko_mosaic import filaments, inserts, match  # noqa: E402
from kumiko_mosaic.grid import FrameSpec, build_grid  # noqa: E402
from kumiko_mosaic.imagemap import hex_to_rgb  # noqa: E402

BG = (24, 24, 28)


def font(size, bold=False):
    for f in (("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),):
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            pass
    return ImageFont.load_default()


def patterns_figure(out: Path):
    g = build_grid(FrameSpec(), 4, 2)
    side = g.spec.inner_side
    ids = match.usable_ladder(g, match.DEFAULT_LADDER, 2.0)
    cov = match.coverage_table(g, ids, 2.0)
    S, pad, per_row = 190, 14, 6
    rows = math.ceil(len(ids) / per_row)
    W = per_row * (S + pad) + pad
    H = rows * (S + 60) + 20
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    h = side * math.sqrt(3) / 2
    sc = S * 0.92 / side
    for i, pid in enumerate(ids):
        x0 = pad + (i % per_row) * (S + pad)
        y0 = 12 + (i // per_row) * (S + 60)
        P = lambda x, y: (x0 + S / 2 + x * sc, y0 + S * 0.69 - y * sc)  # noqa: E731
        d.polygon([P(-side / 2, -h / 3), P(side / 2, -h / 3), P(0, 2 * h / 3)], fill=(0, 0, 0))
        sh = inserts.canonical_polygon(pid, side, 2.0)
        for gg in (sh.geoms if sh.geom_type == "MultiPolygon" else [sh]):
            d.polygon([P(x, y) for x, y in gg.exterior.coords], fill=(236, 190, 90))
            for hole in gg.interiors:
                d.polygon([P(x, y) for x, y in hole.coords], fill=(0, 0, 0))
        name = inserts.PROCEDURAL[pid]["name"]
        d.text((x0 + S / 2, y0 + S + 2), name, fill=(235, 235, 235), font=font(14, True), anchor="mt")
        d.text((x0 + S / 2, y0 + S + 24), f"{cov[pid] * 100:.0f}% strips", fill=(170, 170, 176), font=font(13), anchor="mt")
    img.save(out, optimize=True)
    return len(ids)


def filaments_figure(out: Path):
    sets = [("Bambu Lab PLA Matte", filaments.SETS["bambu-matte"]), ("Bambu Lab PLA Basic", filaments.SETS["bambu-basic"])]
    sw_w, sw_h, pad, per_row = 150, 66, 10, 7
    W = per_row * (sw_w + pad) + pad
    y = 14
    heights = []
    for _, fl in sets:
        heights.append(40 + math.ceil(len(fl) / per_row) * (sw_h + 28 + pad))
    img = Image.new("RGB", (W, sum(heights) + 20), BG)
    d = ImageDraw.Draw(img)
    for (title, fl), hh in zip(sets, heights):
        d.text((pad, y), title, fill=(210, 172, 134), font=font(18, True))
        yy = y + 36
        for i, f in enumerate(fl):
            x = pad + (i % per_row) * (sw_w + pad)
            yi = yy + (i // per_row) * (sw_h + 28 + pad)
            d.rounded_rectangle([x, yi, x + sw_w, yi + sw_h], 8, fill=hex_to_rgb(f.hex), outline=(70, 70, 76))
            d.text((x + 2, yi + sw_h + 4), f.name.split(" ", 1)[1], fill=(225, 225, 228), font=font(12))
        y += hh
    img.save(out, optimize=True)
    return sum(len(f) for _, f in sets)


if __name__ == "__main__":
    n = patterns_figure(ROOT / "docs" / "img" / "patterns.png")
    m = filaments_figure(ROOT / "docs" / "img" / "filaments.png")
    gal = ROOT / "examples" / "gallery"
    if gal.is_dir():
        Image.open(ROOT / "docs" / "img" / "patterns.png").save(gal / "density_ladder.png")
    print(f"{n} patterns, {m} filaments")
