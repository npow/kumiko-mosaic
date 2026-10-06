"""Write zoomable vector plans (gzipped SVG) and 900 px originals for the curated catalog images.

Usage: python scripts/build_catalog_zoom.py [--workers 20]
Reads examples/catalog/attribution.json (ids), re-plans each image (deterministic) and writes
examples/catalog/v/<id>.svgz and examples/catalog/o/<id>.jpg.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PUB = ROOT / "examples" / "catalog"
CAT = ROOT / "catalog"


def work(cid):
    from PIL import Image
    from kumiko_mosaic.pipeline import Params, run
    tmp = CAT / "zoom_tmp" / cid
    s = run(str(CAT / "images" / f"{cid}.jpg"), str(tmp), Params(cols=60, preview_only=True, write_svg=True))
    raw = (tmp / "plan.svg").read_bytes()
    (PUB / "v" / f"{cid}.svgz").write_bytes(gzip.compress(raw, 9))
    im = Image.open(CAT / "images" / f"{cid}.jpg").convert("RGB")
    im.thumbnail((900, 900))
    im.save(PUB / "o" / f"{cid}.jpg", quality=72, optimize=True)
    return cid, s["fidelity"]["mean_dE"], len(raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=20)
    a = ap.parse_args()
    ids = [r["id"] for r in json.load(open(PUB / "attribution.json"))]
    (PUB / "v").mkdir(exist_ok=True)
    (PUB / "o").mkdir(exist_ok=True)
    done = 0
    with ProcessPoolExecutor(a.workers) as ex:
        for cid, f, n in ex.map(work, ids):
            done += 1
            if done % 10 == 0:
                print(f"{done}/{len(ids)}", flush=True)
    print("done", done)


if __name__ == "__main__":
    main()
