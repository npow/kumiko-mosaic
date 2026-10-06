"""Build the browsable catalog pages from catalog/results.json + catalog/sources.json.

Writes:
  catalog/index.html        every planned image (local only, served at /catalog-full)
  examples/catalog/         curated top picks with small compare images (committed, served at /catalog)
Usage: python scripts/build_catalog_site.py [--top 120] [--per-category 18]
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kumiko_mosaic.imagemap import hex_to_rgb, rgb_to_lab  # noqa: E402


def hue_bin(i: dict) -> int:
    """Dominant hue family (0-7, 45 degree bins) of the most vivid strip filament, 8 = neutral."""
    lab = rgb_to_lab(np.array([hex_to_rgb(h) for h in i["strip_hex"] + i["bg_hex"]], dtype=float))
    chroma = np.sqrt(lab[:, 1] ** 2 + lab[:, 2] ** 2)
    k = int(np.argmax(chroma))
    if chroma[k] < 25:
        return 8
    return int(((np.degrees(np.arctan2(lab[k, 2], lab[k, 1])) % 360) // 45))


def wow_score(i: dict) -> float:
    """Curation score: colourful, varied and well reproduced. Fidelity alone favours trivial
    black-background or greyscale images, so it is only a penalty above a decent level."""
    hexes = i["strip_hex"] + i["bg_hex"]
    lab = rgb_to_lab(np.array([hex_to_rgb(h) for h in hexes], dtype=float))
    chroma = np.sqrt(lab[:, 1] ** 2 + lab[:, 2] ** 2)
    colourful = float(np.mean(sorted(chroma)[-4:]))            # the vividest four filaments
    variety = min(1.0, len(set(i["strip_hex"])) / 5.0)
    contrast = min(1.0, i["fidelity"]["L_std_source"] / 22.0)
    penalty = max(0.0, i["fidelity"]["mean_dE"] - 12.0) * 1.5
    return colourful * variety * (0.5 + 0.5 * contrast) - penalty

ROOT = Path(__file__).resolve().parent.parent
CAT = ROOT / "catalog"
PUB = ROOT / "examples" / "catalog"

CSS = """body{font-family:system-ui;background:#1b1b1f;color:#eee;margin:16px}h1{margin:0 0 6px}
.bar{position:sticky;top:0;background:#1b1b1fee;padding:8px 0;z-index:5}.bar button,.bar select{background:#2a2a30;color:#eee;border:1px solid #444;border-radius:5px;padding:5px 10px;margin:2px;cursor:pointer}
.bar button.on{background:#d2ac86;color:#222}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(520px,1fr));gap:14px}
.card{background:#24242a;border-radius:8px;padding:8px}.card img{width:100%;border-radius:5px;display:block}
.t{font-size:13px;margin:6px 2px 0}.m{font-size:11px;color:#aaa;margin:2px}a{color:#9ec5ff}.sw{display:inline-block;width:11px;height:11px;border:1px solid #666;margin-right:1px}
"""
JS = """const cards=[...document.querySelectorAll('.card')];let cat='all';
function apply(){const s=document.getElementById('sort').value;const g=document.getElementById('g');
cards.sort((a,b)=>s=='fid'?a.dataset.f-b.dataset.f:s=='cells'?a.dataset.c-b.dataset.c:a.dataset.n.localeCompare(b.dataset.n));
cards.forEach(c=>{c.style.display=(cat=='all'||c.dataset.k==cat)?'':'none';g.appendChild(c)});}
document.querySelectorAll('.bar button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.bar button').forEach(x=>x.classList.remove('on'));b.classList.add('on');cat=b.dataset.k;apply()});
document.getElementById('sort').onchange=apply;apply();"""


def page(title: str, intro: str, items: list, img_prefix: str) -> str:
    cats = sorted({i["category"] for i in items})
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{CSS}</style></head><body>",
           f"<h1>{html.escape(title)}</h1><p class='m'>{intro}</p><div class='bar'>",
           f"<button class='on' data-k='all'>all ({len(items)})</button>"]
    for c in cats:
        out.append(f"<button data-k='{html.escape(c)}'>{html.escape(c)} ({sum(1 for i in items if i['category'] == c)})</button>")
    out.append("<select id='sort'><option value='fid'>best fidelity first</option><option value='cells'>fewest cells first</option>"
               "<option value='name'>name</option></select></div><div class='grid' id='g'>")
    for i in items:
        sw = "".join(f"<span class='sw' style='background:{h}'></span>" for h in i["strip_hex"])
        bg = "".join(f"<span class='sw' style='background:{h};border-radius:50%'></span>" for h in i["bg_hex"])
        name = i["title"].replace("File:", "").rsplit(".", 1)[0]
        out.append(f"<div class='card' data-k='{html.escape(i['category'])}' data-f='{i['fidelity']['mean_dE']}' "
                   f"data-c='{i['counts']['cells']}' data-n='{html.escape(name)}'>"
                   f"<img loading='lazy' src='{img_prefix}{i['id']}.jpg'>"
                   f"<div class='t'>{html.escape(name[:80])}</div>"
                   f"<div class='m'>{i['grid'][0]} x {i['grid'][1]} triangles, {i['counts']['cells']} cells, fidelity {i['fidelity']['mean_dE']}, "
                   f"{i['counts']['distinct_patterns']} patterns &nbsp;{sw} {bg}</div>"
                   f"<div class='m'>{html.escape(i['author'][:60])} &middot; <a href='{i['license_url'] or i['page']}'>{html.escape(i['license'])}</a> &middot; <a href='{i['page']}'>source</a></div></div>")
    out.append(f"</div><script>{JS}</script></body></html>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=120)
    ap.add_argument("--per-category", type=int, default=18)
    ap.add_argument("--per-hue", type=int, default=16, help="cap per dominant hue family, for colour variety")
    a = ap.parse_args()
    sources = json.load(open(CAT / "sources.json"))
    results = json.load(open(CAT / "results.json"))
    items = []
    for cid, r in results.items():
        if r.get("ok") and cid in sources and (CAT / "out" / cid / "compare.jpg").exists():
            items.append({"id": cid, **sources[cid], **r})
    items.sort(key=lambda i: i["fidelity"]["mean_dE"])
    all_items = list(items)
    (CAT / "index.html").write_text(page("Kumiko mosaic catalog (all)",
        f"{len(items)} images planned at 60 columns. Source left, plan right. Fidelity = mean CIELAB error at viewing distance (lower is better).",
        items, "out/").replace("src='out/", "src='out/").replace(".jpg'>", ".jpg'>"))
    # all-catalog pages reference out/<id>/compare.jpg
    txt = (CAT / "index.html").read_text()
    for i in items:
        txt = txt.replace(f"src='out/{i['id']}.jpg'", f"src='out/{i['id']}/compare.jpg'")
    (CAT / "index.html").write_text(txt)

    # curated set: best wow score, capped per category and per search query for variety
    picked, per, perq, perh = [], {}, {}, {}
    for i in sorted(all_items, key=wow_score, reverse=True):
        if i["fidelity"]["L_std_source"] < 12 or i["fidelity"]["mean_dE"] > 16:
            continue
        hb = hue_bin(i)
        if per.get(i["category"], 0) >= a.per_category or perq.get(i["query"], 0) >= 3 or perh.get(hb, 0) >= a.per_hue:
            continue
        i["wow"] = round(wow_score(i), 1)
        picked.append(i)
        per[i["category"]] = per.get(i["category"], 0) + 1
        perq[i["query"]] = perq.get(i["query"], 0) + 1
        perh[hb] = perh.get(hb, 0) + 1
        if len(picked) >= a.top:
            break
    picked.sort(key=lambda i: i["fidelity"]["mean_dE"])
    shutil.rmtree(PUB, ignore_errors=True)
    PUB.mkdir(parents=True)
    for i in picked:
        im = Image.open(CAT / "out" / i["id"] / "compare.jpg")
        im.thumbnail((1100, 380))
        im.save(PUB / f"{i['id']}.jpg", quality=72, optimize=True)
    (PUB / "index.html").write_text(page("Kumiko mosaic catalog: top picks",
        f"{len(picked)} of {len(items)} planned images, chosen for colour, variety and fidelity, at most {a.per_category} per category and 3 per search. "
        "Source left, plan right (60 columns, 8 Bambu PLA strip filaments, up to 3 grey backgrounds, at most 4 line patterns). "
        "Images from Wikimedia Commons; author and licence under each. Fidelity = mean CIELAB error at viewing distance (lower is better).",
        picked, ""))
    json.dump([{k: v for k, v in i.items() if k not in ("ahash", "trace")} for i in picked], open(PUB / "index.json", "w"), indent=1, ensure_ascii=False)
    size = sum(f.stat().st_size for f in PUB.iterdir()) / 1e6
    print(f"all: {len(items)}  curated: {len(picked)}  ({size:.1f} MB)  categories: {per}")


if __name__ == "__main__":
    main()
