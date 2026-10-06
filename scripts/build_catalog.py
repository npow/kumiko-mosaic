"""Plan every image in catalog/sources.json in fast preview-only mode.

Usage: python scripts/build_catalog.py [--cols 60] [--workers 16]
Writes catalog/out/<id>/compare.jpg and catalog/results.json. Resumable: finished ids are skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CAT = ROOT / "catalog"


def work(args):
    cid, cols = args
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    from kumiko_mosaic.pipeline import Params, run
    t = time.time()
    try:
        s = run(str(CAT / "images" / f"{cid}.jpg"), str(CAT / "out" / cid), Params(cols=cols, preview_only=True))
        return cid, {"ok": True, "cols": cols, "bgset": "all", "fidelity": s["fidelity"], "grid": s["grid"]["triangle_generator"],
                     "outer_mm": s["grid"]["outer_mm"], "counts": s["counts"], "patterns": s["patterns_used"],
                     "strips": [p["name"] for p in s["palette_used"]], "strip_hex": [p["hex"] for p in s["palette_used"]],
                     "backgrounds": [b["name"] for b in s["backgrounds_used"]],
                     "bg_hex": [b["hex"] for b in s["backgrounds_used"]], "seconds": round(time.time() - t)}
    except Exception as e:  # noqa: BLE001
        return cid, {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-400:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cols", type=int, default=60)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    sources = json.load(open(CAT / "sources.json"))
    res_path = CAT / "results.json"
    results = json.load(open(res_path)) if res_path.exists() else {}
    todo = [cid for cid in sources if not results.get(cid, {}).get("ok") or results[cid].get("cols") != a.cols
            or results[cid].get("bgset") != "all"]
    if a.limit:
        todo = todo[: a.limit]
    print(f"{len(todo)} to plan of {len(sources)}", flush=True)
    done = 0
    t0 = time.time()
    with ProcessPoolExecutor(a.workers) as ex:
        futs = [ex.submit(work, (cid, a.cols)) for cid in todo]
        for f in as_completed(futs):
            cid, r = f.result()
            results[cid] = r
            done += 1
            if done % 10 == 0 or done == len(todo):
                res_path.write_text(json.dumps(results, indent=1))
                rate = (time.time() - t0) / done
                print(f"{done}/{len(todo)}  {rate:.1f}s/img  eta {rate * (len(todo) - done) / 60:.0f} min  "
                      f"failed {sum(1 for v in results.values() if not v.get('ok'))}", flush=True)
    res_path.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
