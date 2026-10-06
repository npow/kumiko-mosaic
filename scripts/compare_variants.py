"""Compare planner settings by fidelity across images (preview-only runs, in parallel).

Usage: python scripts/compare_variants.py NAME=json_params [NAME=json_params ...] [--images a,b,c] [--cols 40]
Example: python scripts/compare_variants.py base='{}' wide='{"pattern_mode":"auto"}'
Prints mean CIELAB error (lower is better) per image and variant, then the mean per variant.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEFAULT_IMAGES = "toucan,golden_gate_bridge,great_wave,earth,starry_night,red_fuji"


def work(job):
    img, name, params, cols = job
    from kumiko_mosaic.pipeline import Params, run
    p = {"cols": cols, "preview_only": True, **params}
    s = run(str(ROOT / "examples" / "samples" / f"{img}.jpg"), f"/tmp/cmp_{img}_{name}", Params(**p))
    return img, name, s["fidelity"]["mean_dE"], s["fidelity"]["p90_dE"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("variants", nargs="+")
    ap.add_argument("--images", default=DEFAULT_IMAGES)
    ap.add_argument("--cols", type=int, default=40)
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    variants = {}
    for v in a.variants:
        k, _, j = v.partition("=")
        variants[k] = json.loads(j or "{}")
    jobs = [(i, k, p, a.cols) for i in a.images.split(",") for k, p in variants.items()]
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(work, jobs))
    table = {}
    for img, name, m, p90 in res:
        table.setdefault(img, {})[name] = (m, p90)
    names = list(variants)
    print(f"{'image':22s}" + "".join(f"{n:>16s}" for n in names))
    for img, d in table.items():
        print(f"{img:22s}" + "".join(f"{d[n][0]:>9.2f}/{d[n][1]:<6.1f}" for n in names))
    print(f"{'MEAN':22s}" + "".join(f"{sum(d[n][0] for d in table.values()) / len(table):>16.2f}" for n in names))
    print("(mean dE / p90 dE; lower is better)")


if __name__ == "__main__":
    main()
