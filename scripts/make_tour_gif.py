"""Render examples/catalog_tour.gif: for each of ~12 catalog images, the photo, a wipe to the kumiko
plan, then a smooth zoom into the line inserts (frames come from the vector plans via headless Chrome).

Usage: python scripts/make_tour_gif.py [--n 12] [--w 600] [--h 450]
Needs: playwright (+ system Chrome), ffmpeg.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import math
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import uniform_filter

ROOT = Path(__file__).resolve().parent.parent
PUB = ROOT / "examples" / "catalog"
BG = (20, 20, 24)


def pick(n):
    html = (PUB / "index.html").read_text()
    items = json.loads(re.search(r"const ITEMS=(\[.*?\]);const BASE", html, re.S).group(1))
    cand = [i for i in items if i["z"] and 1.05 <= i["a"] <= 1.9 and i["f"] < 12 and i["s"] > 0.2]
    cand.sort(key=lambda i: i["s"] * (1 - i["f"] / 20), reverse=True)
    chosen, bins = [], set()
    for i in cand:                                   # one per hue family first for variety
        b = int(i["h"] * 10)
        if b in bins:
            continue
        bins.add(b); chosen.append(i)
        if len(chosen) >= n:
            break
    for i in cand:
        if len(chosen) >= n:
            break
        if i not in chosen:
            chosen.append(i)
    return sorted(chosen, key=lambda i: i["h"])


def font(size):
    for f in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            pass
    return ImageFont.load_default()


def caption(im, text):
    d = ImageDraw.Draw(im, "RGBA")
    f = font(max(13, im.width // 40))
    w = d.textlength(text, font=f)
    d.rounded_rectangle([10, im.height - 38, 10 + w + 20, im.height - 10], 8, fill=(0, 0, 0, 170))
    d.text((20, im.height - 34), text, font=f, fill=(235, 235, 235))
    return im


def ease(t):
    return t * t * (3 - 2 * t)


def target(src_img, frac):
    g = np.asarray(src_img.convert("L").resize((160, int(160 * src_img.height / src_img.width))), dtype=float)
    k = max(3, int(160 * frac))
    m = uniform_filter(g, k); m2 = uniform_filter(g * g, k)
    var = m2 - m * m
    h, w = var.shape
    my, mx = int(h * 0.2), int(w * 0.2)
    sub = var[my:h - my, mx:w - mx]
    y, x = np.unravel_index(np.argmax(sub), sub.shape)
    return (x + mx) / w, (y + my) / h


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--w", type=int, default=520)
    ap.add_argument("--h", type=int, default=390)
    ap.add_argument("--colors", type=int, default=96)
    ap.add_argument("--dither", default="bayer:bayer_scale=3")
    a = ap.parse_args()
    from playwright.async_api import async_playwright
    W, H = a.w, a.h
    items = pick(a.n)
    tmp = Path(tempfile.mkdtemp(prefix="tour_"))
    frames = []                                       # (path, seconds)

    def add(im, secs):
        p = tmp / f"f{len(frames):04d}.png"
        im.save(p)
        frames.append((p, secs))

    async with async_playwright() as p:
        br = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        page = await br.new_page(viewport={"width": W, "height": H})
        for k, it in enumerate(items):
            cid = it["id"]
            svg = gzip.decompress((PUB / "v" / f"{cid}.svgz").read_bytes()).decode()
            vbm = re.search(r'viewBox="([^"]+)"', svg)
            x0, y0, vw, vh = map(float, vbm.group(1).split())
            svg = svg.replace("<svg ", f'<svg id="s" width="{W}" height="{H}" ', 1)
            await page.set_content(f'<body style="margin:0;background:rgb{BG}">{svg}</body>')
            # fit viewBox with the canvas aspect, plan centred
            fw = max(vw, vh * W / H); fh = fw * H / W
            fit = (x0 + vw / 2 - fw / 2, y0 + vh / 2 - fh / 2, fw, fh)

            async def shot(vb):
                for attempt in range(4):
                    try:
                        await page.evaluate("v=>document.getElementById('s').setAttribute('viewBox',v.join(' '))", list(vb))
                        await page.wait_for_timeout(40 + 80 * attempt)
                        return Image.open(__import__("io").BytesIO(await page.screenshot())).convert("RGB")
                    except Exception as e:  # noqa: BLE001
                        print("  screenshot retry", attempt, str(e)[:50], flush=True)
                        await page.wait_for_timeout(300)
                raise RuntimeError("screenshot failed")

            try:
                plan = await shot(fit)
            except RuntimeError:
                print(f"skipping {cid}", flush=True)
                continue
            # photo placed exactly where the plan sits on the canvas
            sc = W / fw
            px, py, pw, ph = int((vw * 0 + (x0 - fit[0]) * sc)), int((y0 - fit[1]) * sc), int(vw * sc), int(vh * sc)
            photo = Image.new("RGB", (W, H), BG)
            src = Image.open(PUB / "o" / f"{cid}.jpg").convert("RGB")
            photo.paste(src.resize((pw, ph), Image.LANCZOS), (px, py))
            add(caption(photo.copy(), "photo"), 0.9)
            for j in range(1, 4):                      # wipe photo -> plan, left to right
                cut = int(W * j / 4)
                f = photo.copy(); f.paste(plan.crop((0, 0, cut, H)), (0, 0))
                ImageDraw.Draw(f).line([(cut, 0), (cut, H)], fill=(230, 230, 230), width=2)
                add(caption(f, "kumiko plan"), 0.07)
            add(caption(plan.copy(), "kumiko plan"), 1.0)
            tx, ty = target(src, 0.1)
            zr = 0.075
            zw = vw * zr; zh = zw * H / W
            cx = x0 + tx * vw; cy = y0 + ty * vh
            zoom_vb = (cx - zw / 2, cy - zh / 2, zw, zh)
            n_z = 4
            for j in range(1, n_z + 1):
                e = ease(j / n_z)
                w_ = fw * (zw / fw) ** e                # geometric zoom
                h_ = w_ * H / W
                ccx = (fit[0] + fw / 2) * (1 - e) + cx * e
                ccy = (fit[1] + fh / 2) * (1 - e) + cy * e
                add(caption(await shot((ccx - w_ / 2, ccy - h_ / 2, w_, h_)), "zoomed in: the line inserts"),
                    0.09 if j < n_z else 1.5)
            print(f"{k + 1}/{len(items)} {cid}", flush=True)
        await br.close()

    lst = tmp / "list.txt"
    with open(lst, "w") as f:
        for pth, secs in frames:
            f.write(f"file '{pth}'\nduration {secs}\n")
        f.write(f"file '{frames[-1][0]}'\n")
    out = ROOT / "examples" / "catalog_tour.gif"
    vf = (f"split[a][b];[a]palettegen=max_colors={a.colors}:stats_mode=diff[p];"
          f"[b][p]paletteuse=dither={a.dither}:diff_mode=rectangle")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", vf,
                    "-fps_mode", "vfr", "-loop", "0", str(out)], check=True)
    print(f"frames kept in {tmp}"); print(f"{out}  {out.stat().st_size / 1e6:.1f} MB, {len(frames)} frames, {sum(s for _, s in frames):.0f} s")


if __name__ == "__main__":
    asyncio.run(main())
