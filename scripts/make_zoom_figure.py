"""Render docs/img/zoom.png: a 3-step zoom ladder (whole panel -> a square -> the line inserts).

Usage: python scripts/make_zoom_figure.py [image.jpg]   (default examples/samples/great_wave.jpg)
Plans the image at 60 columns, writes the compact vector plan, and renders three crops in headless
Chrome with the next crop marked by a red square on the previous one.
"""
from __future__ import annotations

import asyncio
import io
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import uniform_filter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kumiko_mosaic.pipeline import Params, run  # noqa: E402

W, H = 760, 570          # each panel
GAP = 36


def busiest(img: Image.Image, frac: float, box=(0, 0, 1, 1)):
    """Centre (0..1 of the full image) of the highest-variance window of relative size frac inside box."""
    g = np.asarray(img.convert("L").resize((200, int(200 * img.height / img.width))), dtype=float)
    k = max(3, int(200 * frac))
    m = uniform_filter(g, k); v = uniform_filter(g * g, k) - m * m
    h, w = v.shape
    x0, y0, x1, y1 = [int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)]
    sub = v[y0 + k // 2:y1 - k // 2, x0 + k // 2:x1 - k // 2]
    y, x = np.unravel_index(np.argmax(sub), sub.shape)
    return (x + x0 + k // 2) / w, (y + y0 + k // 2) / h


async def main():
    from playwright.async_api import async_playwright
    src_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "examples" / "samples" / "great_wave.jpg"
    tmp = Path(tempfile.mkdtemp())
    run(str(src_path), str(tmp), Params(cols=60, preview_only=True, write_svg=True))
    svg = (tmp / "plan.svg").read_text()
    x0, y0, vw, vh = map(float, re.search(r'viewBox="([^"]+)"', svg).group(1).split())
    svg = svg.replace("<svg ", f'<svg id="s" width="{W}" height="{H}" ', 1)
    src = Image.open(src_path).convert("RGB")

    def region(cx, cy, w):       # viewBox of width w (mm) centred at fractional (cx, cy) of the plan, aspect W:H
        h = w * H / W
        x = min(max(x0 + cx * vw - w / 2, x0), x0 + vw - w)
        y = min(max(y0 + cy * vh - h / 2, y0), y0 + vh - h)
        return (x, y, w, h)

    full_w = max(vw, vh * W / H)
    full = (x0 + vw / 2 - full_w / 2, y0 + vh / 2 - full_w * H / W / 2, full_w, full_w * H / W)
    cx1, cy1 = busiest(src, 0.22)
    r1 = region(cx1, cy1, vw * 0.22)
    cx2, cy2 = busiest(src.crop((int((r1[0] - x0) / vw * src.width), int((r1[1] - y0) / vh * src.height),
                                 int((r1[0] + r1[2] - x0) / vw * src.width), int((r1[1] + r1[3] - y0) / vh * src.height))), 0.26)
    r2 = region((r1[0] - x0 + cx2 * r1[2]) / vw, (r1[1] - y0 + cy2 * r1[3]) / vh, r1[2] * 0.25)

    async with async_playwright() as p:
        br = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = await br.new_page(viewport={"width": W, "height": H})
        await pg.set_content(f'<body style="margin:0;background:rgb(20,20,24)">{svg}</body>')

        async def shot(vb):
            await pg.evaluate("v=>document.getElementById('s').setAttribute('viewBox',v.join(' '))", list(vb))
            await pg.wait_for_timeout(120)
            return Image.open(io.BytesIO(await pg.screenshot())).convert("RGB")

        shots = [await shot(full), await shot(r1), await shot(r2)]
        await br.close()

    def mark(im, outer, inner):
        sc = W / outer[2]
        a = ((inner[0] - outer[0]) * sc, (inner[1] - outer[1]) * sc, (inner[0] + inner[2] - outer[0]) * sc, (inner[1] + inner[3] - outer[1]) * sc)
        d = ImageDraw.Draw(im)
        d.rectangle(a, outline=(255, 60, 60), width=5)
        return im

    mark(shots[0], full, r1)
    mark(shots[1], r1, r2)
    cells = [round(r1[2] / 50 * 1.155 / 1.0), round(r2[2] / 50 * 1.155)]
    f = ImageFont.truetype("DejaVuSans-Bold.ttf", 22) if Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf").exists() else ImageFont.load_default()
    labels = ["the whole panel", "zoom in on the red square", "the kumiko line inserts"]
    total_w = 3 * W + 2 * GAP
    out = Image.new("RGB", (total_w, H + 44), (20, 20, 24))
    d = ImageDraw.Draw(out)
    for i, (im, lab) in enumerate(zip(shots, labels)):
        x = i * (W + GAP)
        out.paste(im, (x, 44))
        d.text((x + 4, 8), f"{i + 1}. {lab}", fill=(235, 235, 235), font=f)
        if i < 2:
            ax = x + W + GAP // 2
            d.polygon([(ax - 9, H // 2 + 20), (ax - 9, H // 2 + 56), (ax + 9, H // 2 + 38)], fill=(255, 60, 60))
    dst = ROOT / "docs" / "img" / "zoom.png"
    out.save(dst, optimize=True)
    print(dst, out.size, f"{dst.stat().st_size / 1e3:.0f} KB")


if __name__ == "__main__":
    asyncio.run(main())
