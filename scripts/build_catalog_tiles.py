"""Split catalog compare images into plan-only and source-only tiles and build the mosaic pages.

Writes
  catalog/tiles/{p,s}/<id>.jpg      every planned image (local)         + catalog/index.html
  examples/catalog/{p,s}/<id>.jpg   curated set (committed)             + examples/catalog/index.html
  examples/catalog_wall.jpg         one-image montage of the curated plans for the README
Usage: python scripts/build_catalog_tiles.py [--top 120] [--per-category 26] [--per-hue 40] [--per-subject 1]
"""
from __future__ import annotations

import argparse
import colorsys
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from build_catalog_site import hue_bin, wow_score  # noqa: E402

CAT = ROOT / "catalog"
PUB = ROOT / "examples" / "catalog"
TILE_H = 360


def title_stem(title: str) -> str:
    """File-name family: 'Herbarium. Fragaria vesca. img-010' and 'img-008' share one stem."""
    t = title.replace("File:", "").rsplit(".", 1)[0].lower()
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"[0-9_\-]+", " ", t)
    return re.sub(r"[^a-z]+", " ", t).strip()


def thumb_vec(path: Path) -> np.ndarray:
    im = Image.open(path).convert("RGB").resize((24, 24), Image.BILINEAR)
    return np.asarray(im, dtype=float).ravel() / 255.0


DUP_DIST = 0.15      # RMS difference of 24x24 thumbnails; same-subject pairs measured 0.11-0.13, distinct scenes >= 0.17


def split(cid: str, res: dict):
    im = Image.open(CAT / "out" / cid / "compare.jpg").convert("RGB")
    W, H = im.size
    lw, lh = res["outer_mm"][0] - 15, res["outer_mm"][1] - 18     # lattice from outer size (3 mm bars, 6 mm border)
    a_w = int(H * lw / lh)
    src = im.crop((0, 0, a_w, H))
    plan = im.crop((a_w + 12, 0, W, H))
    return src, plan


def tile_stats(plan: Image.Image) -> dict:
    a = np.asarray(plan.resize((64, 64)), dtype=float) / 255.0
    r, g, b = a.reshape(-1, 3).mean(0)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    # saturation-weighted circular mean hue (average colour of a multi-colour image is muddy)
    hs = np.array([colorsys.rgb_to_hsv(*p) for p in a.reshape(-1, 3)])
    w = hs[:, 1] * hs[:, 2]
    if w.sum() > 1e-6:
        ang = np.arctan2((np.sin(hs[:, 0] * 2 * np.pi) * w).sum(), (np.cos(hs[:, 0] * 2 * np.pi) * w).sum())
        hue = (ang / (2 * np.pi)) % 1.0
    else:
        hue = h
    return {"hue": round(float(hue), 3), "sat": round(float(s), 3), "lum": round(float(v), 3)}


def montage(plans, width=2400, target=170, gap=4):
    """Justified-row montage (same greedy algorithm as the web page)."""
    rows, row, sar = [], [], 0.0
    for im in plans:
        ar = im.width / im.height
        row.append((im, ar)); sar += ar
        if sar * target + gap * (len(row) - 1) >= width:
            rows.append((row, (width - gap * (len(row) - 1)) / sar)); row, sar = [], 0.0
    if row:
        rows.append((row, target))
    H = int(sum(h for _, h in rows) + gap * (len(rows) + 1))
    canvas = Image.new("RGB", (width, H), (20, 20, 24))
    y = gap
    for row, h in rows:
        x = 0.0
        n = len(row)
        hh = int(round(h))
        for k, (im, ar) in enumerate(row):
            w = int(round(ar * h)) if k < n - 1 or h != target else int(round(ar * h))
            canvas.paste(im.resize((max(1, w), hh), Image.LANCZOS), (int(x), y))
            x += w + gap
        y += hh + gap
    return canvas


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kumiko mosaic catalog</title><style>
html,body{margin:0;background:#141418;color:#ddd;font:13px system-ui}
#bar{position:sticky;top:0;z-index:9;background:#141418e8;padding:6px 8px;display:flex;flex-wrap:wrap;gap:4px;align-items:center}
button{background:#24242a;color:#ccc;border:0;border-radius:12px;padding:3px 11px;cursor:pointer;font:inherit}
#bar button.on{background:#d2ac86;color:#222}#bar .sp{flex:1}
#g{padding:0 4px 8px}.row{display:flex;gap:4px;margin-bottom:4px}
.t{position:relative;display:block;flex:none;overflow:hidden;background:#222;cursor:zoom-in}
.t img{position:absolute;inset:0;width:100%;height:100%;display:block;transition:opacity .18s}
.t img.s{opacity:0}.t:hover img.s{opacity:1}
#lb{position:fixed;inset:0;background:#0b0b0e;display:none;z-index:20}#lb.on{display:block}
#host{position:absolute;inset:0;display:flex;align-items:center;justify-content:center}
#host>img{max-width:100vw;max-height:100vh}
#ctl{position:absolute;top:10px;right:12px;display:flex;gap:6px;align-items:center;z-index:2}
#ctl a{color:#9ec5ff;text-decoration:none;padding:3px 8px}#hint{position:absolute;bottom:10px;left:12px;color:#777;z-index:2}
</style></head><body>
<div id="bar"></div><div id="g"></div>
<div id="lb"><div id="host"></div><div id="ctl"><button id="orig">original</button><button id="fit">fit</button><a id="dl" target="_blank">svg</a><button id="x">&times;</button></div><div id="hint">scroll to zoom, drag to pan, double-click to zoom in</div></div>
<script>
const ITEMS=__ITEMS__;const BASE="__BASE__";
let cat="all",mode="colour",showSrc=false;
const bar=document.getElementById("bar"),g=document.getElementById("g"),lb=document.getElementById("lb"),host=document.getElementById("host");
const cats=[...new Set(ITEMS.map(i=>i.c))].sort();
function btn(label,fn,on){const b=document.createElement("button");b.textContent=label;b.onclick=()=>{fn();render()};if(on)b.className="on";bar.appendChild(b)}
function buildBar(){bar.innerHTML="";
 btn("all "+ITEMS.length,()=>cat="all",cat=="all");
 cats.forEach(c=>btn(c+" "+ITEMS.filter(i=>i.c==c).length,()=>cat=c,cat==c));
 const sp=document.createElement("span");sp.className="sp";bar.appendChild(sp);
 btn("by colour",()=>mode="colour",mode=="colour");btn("best first",()=>mode="best",mode=="best")}
function sorted(list){const l=[...list];
 if(mode=="best")return l.sort((a,b)=>a.f-b.f);
 const key=i=>i.s<0.16?[2,i.v]:[0,i.h];
 return l.sort((a,b)=>{const x=key(a),y=key(b);return x[0]-y[0]||x[1]-y[1]})}
function render(){buildBar();g.innerHTML="";
 const list=sorted(ITEMS.filter(i=>cat=="all"||i.c==cat));
 const W=g.clientWidth-8,gap=4,target=innerWidth<700?150:Math.max(190,Math.min(300,innerWidth/7));
 let row=[],sar=0;
 const flush=(h)=>{const r=document.createElement("div");r.className="row";
  row.forEach(i=>{const a=document.createElement("a");a.className="t";a.style.height=h+"px";a.style.width=(i.a*h)+"px";
   a.innerHTML=`<img loading="lazy" src="${BASE}p/${i.id}.jpg"><img class="s" loading="lazy" src="${BASE}s/${i.id}.jpg">`;
   a.onclick=()=>openItem(i);r.appendChild(a)});g.appendChild(r);row=[];sar=0};
 list.forEach(i=>{row.push(i);sar+=i.a;if(sar*target+gap*(row.length-1)>=W)flush((W-gap*(row.length-1))/sar)});
 if(row.length)flush(target)}
// ---- zoom viewer ---------------------------------------------------------------------------
let svg=null,full=null,vb=null,origImg=null,showOrig=false;
const dl=document.getElementById("dl"),origBtn=document.getElementById("orig");
function setView(){svg.setAttribute("viewBox",`${vb.x} ${vb.y} ${vb.w} ${vb.h}`)}
function toUser(e){const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse())}
function zoomAt(cx,cy,k){const p=toUser({clientX:cx,clientY:cy});const nw=Math.min(full.w*1.05,Math.max(full.w/90,vb.w*k)),r=nw/vb.w;
 vb={x:p.x-(p.x-vb.x)*r,y:p.y-(p.y-vb.y)*r,w:nw,h:vb.h*r};setView()}
function panZoom(){svg.style.cssText="width:100vw;height:100vh;touch-action:none;cursor:grab;display:block";
 svg.setAttribute("preserveAspectRatio","xMidYMid meet");vb={...full};setView();
 const ptrs=new Map();let last=0;
 svg.onwheel=e=>{e.preventDefault();zoomAt(e.clientX,e.clientY,Math.exp(e.deltaY*0.0015))};
 svg.ondblclick=e=>zoomAt(e.clientX,e.clientY,0.3);
 svg.onpointerdown=e=>{svg.setPointerCapture(e.pointerId);ptrs.set(e.pointerId,[e.clientX,e.clientY]);svg.style.cursor="grabbing";last=0};
 svg.onpointerup=svg.onpointercancel=e=>{ptrs.delete(e.pointerId);svg.style.cursor="grab"};
 svg.onpointermove=e=>{if(!ptrs.has(e.pointerId))return;const prev=ptrs.get(e.pointerId);ptrs.set(e.pointerId,[e.clientX,e.clientY]);
  if(ptrs.size==1){const s=svg.getScreenCTM().a;vb.x-=(e.clientX-prev[0])/s;vb.y-=(e.clientY-prev[1])/s;setView()}
  else if(ptrs.size==2){const [a,b]=[...ptrs.values()];const d=Math.hypot(a[0]-b[0],a[1]-b[1]);if(last)zoomAt((a[0]+b[0])/2,(a[1]+b[1])/2,last/d);last=d}}}
async function openItem(i){lb.classList.add("on");host.innerHTML="";showOrig=false;svg=null;
 dl.style.display=origBtn.style.display=i.z?"":"none";
 if(i.z){host.textContent="loading";const t=await (await fetch(BASE+"v/"+i.id+".svg")).text();host.innerHTML=t;svg=host.querySelector("svg");
  const v=svg.viewBox.baseVal;full={x:v.x,y:v.y,w:v.width,h:v.height};panZoom();dl.href=BASE+"v/"+i.id+".svg";
  origImg=new Image();origImg.src=BASE+(i.z?"o/":"s/")+i.id+".jpg"}
 else{host.innerHTML=`<img src="${BASE}p/${i.id}.jpg">`}}
origBtn.onclick=()=>{if(!svg)return;showOrig=!showOrig;origBtn.textContent=showOrig?"plan":"original";host.replaceChildren(showOrig?origImg:svg)};
document.getElementById("fit").onclick=()=>{if(svg){vb={...full};setView()}};
const close=()=>{lb.classList.remove("on");host.innerHTML="";origBtn.textContent="original"};
document.getElementById("x").onclick=close;addEventListener("keydown",e=>{if(e.key=="Escape")close()});
let rt;addEventListener("resize",()=>{clearTimeout(rt);rt=setTimeout(render,150)});render();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=120)
    ap.add_argument("--per-category", type=int, default=26)
    ap.add_argument("--per-hue", type=int, default=40, help="cap per dominant hue family, for colour variety")
    ap.add_argument("--per-subject", type=int, default=1, help="max images per search query (subject)")
    a = ap.parse_args()
    sources = json.load(open(CAT / "sources.json"))
    results = json.load(open(CAT / "results.json"))
    ids = [c for c, r in results.items() if r.get("ok") and c in sources and (CAT / "out" / c / "compare.jpg").exists()]
    (CAT / "tiles" / "p").mkdir(parents=True, exist_ok=True)
    (CAT / "tiles" / "s").mkdir(parents=True, exist_ok=True)
    items = []
    for cid in ids:
        r = results[cid]
        src, plan = split(cid, r)
        st = tile_stats(plan)
        plan_t = plan.resize((int(plan.width * TILE_H / plan.height), TILE_H), Image.LANCZOS)
        src_t = src.resize(plan_t.size, Image.LANCZOS)         # same box as the plan so hover swaps cleanly
        plan_t.save(CAT / "tiles" / "p" / f"{cid}.jpg", quality=72, optimize=True)
        src_t.save(CAT / "tiles" / "s" / f"{cid}.jpg", quality=70, optimize=True)
        items.append({"id": cid, "c": sources[cid]["category"], "a": round(plan_t.width / plan_t.height, 4),
                      "f": r["fidelity"]["mean_dE"], "h": st["hue"], "s": st["sat"], "v": st["lum"],
                      "_full": {"id": cid, **sources[cid], **r}})
    # full local page
    light = [{k: v for k, v in i.items() if k != "_full"} for i in items]
    (CAT / "index.html").write_text(PAGE.replace("__ITEMS__", json.dumps(light)).replace("__BASE__", "tiles/"))
    # curated set (same selection logic as the catalog page)
    cand = sorted((i["_full"] for i in items), key=wow_score, reverse=True)
    vecs = {}

    def select(cap_q):
        picked, per, perq, perh, stems, skipped = [], {}, {}, {}, set(), 0
        for f in cand:
            if f["fidelity"]["L_std_source"] < 12 or f["fidelity"]["mean_dE"] > 16:
                continue
            hb = hue_bin(f)
            if per.get(f["category"], 0) >= a.per_category or perq.get(f["query"], 0) >= cap_q or perh.get(hb, 0) >= a.per_hue:
                continue
            v = vecs.setdefault(f["id"], thumb_vec(CAT / "tiles" / "s" / f"{f['id']}.jpg"))
            stem = title_stem(f["title"])
            if stem in stems or any(np.sqrt(((v - vecs[q]) ** 2).mean()) < DUP_DIST for q in picked):
                skipped += 1
                continue
            stems.add(stem)
            picked.append(f["id"]); per[f["category"]] = per.get(f["category"], 0) + 1
            perq[f["query"]] = perq.get(f["query"], 0) + 1; perh[hb] = perh.get(hb, 0) + 1
            if len(picked) >= a.top:
                break
        return picked, skipped

    # one picture per subject (search query) by default: same-subject photos look alike even when
    # pixels differ, so --top is a maximum rather than a quota
    cap_q = a.per_subject
    picked, skipped = select(cap_q)
    print(f"per-subject cap {cap_q}: {len(picked)} picked (max {a.top})")
    print(f"skipped {skipped} near-duplicates")
    for sub in ("p", "s"):
        shutil.rmtree(PUB / sub, ignore_errors=True)
    for sub, ext in (("v", ".svgz"), ("o", ".jpg")):          # drop vectors of images no longer curated
        for f in (PUB / sub).glob("*" + ext):
            if f.stem not in picked:
                f.unlink()
    for sub in ("p", "s", "v", "o"):
        (PUB / sub).mkdir(parents=True, exist_ok=True)
    by_id = {i["id"]: i for i in items}
    for cid in picked:
        shutil.copy(CAT / "tiles" / "p" / f"{cid}.jpg", PUB / "p" / f"{cid}.jpg")
        shutil.copy(CAT / "tiles" / "s" / f"{cid}.jpg", PUB / "s" / f"{cid}.jpg")
    cur = [{**{k: v for k, v in by_id[c].items() if k != "_full"}, "z": int((PUB / "v" / f"{c}.svgz").exists())} for c in picked]
    (PUB / "index.html").write_text(PAGE.replace("__ITEMS__", json.dumps(cur)).replace("__BASE__", ""))
    # attribution (no text on the page, so keep it machine- and human-readable next to it)
    attrib = [{"id": c, "title": sources[c]["title"], "author": sources[c]["author"], "license": sources[c]["license"],
               "license_url": sources[c]["license_url"], "page": sources[c]["page"], "category": sources[c]["category"],
               "fidelity": results[c]["fidelity"]["mean_dE"]} for c in picked]
    json.dump(attrib, open(PUB / "attribution.json", "w"), indent=1, ensure_ascii=False)
    lines = ["# Image credits\n", "All images are from Wikimedia Commons. Plans are derivative renderings of the originals.\n"]
    for r in attrib:
        lines.append(f"- [{r['title'].replace('File:', '')}]({r['page']}) by {r['author'][:80]}, [{r['license']}]({r['license_url'] or r['page']})")
    (PUB / "CREDITS.md").write_text("\n".join(lines) + "\n")
    # README montage, in colour order like the page
    order = sorted(cur, key=lambda i: (2, i["v"]) if i["s"] < 0.16 else (0, i["h"]))
    wall = montage([Image.open(PUB / "p" / f"{i['id']}.jpg") for i in order])
    wall.save(ROOT / "examples" / "catalog_wall.jpg", quality=80, optimize=True)
    size = sum(f.stat().st_size for f in PUB.rglob("*") if f.is_file()) / 1e6
    print(f"tiles: {len(items)}  curated: {len(picked)} ({size:.1f} MB)  wall {wall.size}")


if __name__ == "__main__":
    main()
