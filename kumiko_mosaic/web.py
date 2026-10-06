"""Local web UI: upload an image, tweak parameters, get the plan and plate files.

Run:  uvicorn kumiko_mosaic.web:app --reload   (then open http://127.0.0.1:8000)
"""
from __future__ import annotations

import io
import json
import multiprocessing as mp
import os
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .jobs import cleanup_runs, run_job, write_status
from .pipeline import Params

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
RUNS.mkdir(exist_ok=True)

from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _lifespan(_app):
    cleanup_runs(RUNS, TTL_HOURS)               # drop old runs when the server starts
    yield


app = FastAPI(title="kumiko mosaic", lifespan=_lifespan)
app.mount("/runs", StaticFiles(directory=str(RUNS)), name="runs")
from fastapi.responses import Response  # noqa: E402


@app.get("/catalog/v/{cid}.svg")
def catalog_vector(cid: str):
    """Vector plan stored gzipped (about 50 KB); the browser inflates it transparently."""
    f = ROOT / "examples" / "catalog" / "v" / f"{Path(cid).name}.svgz"
    if not f.exists():
        raise HTTPException(404)
    return Response(f.read_bytes(), media_type="image/svg+xml",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "public, max-age=3600"})


for _mount, _dir in (("/catalog", ROOT / "examples" / "catalog"), ("/catalog-full", ROOT / "catalog")):
    if _dir.is_dir():
        app.mount(_mount, StaticFiles(directory=str(_dir), html=True), name=_mount.strip("/"))
GALLERY = ROOT / "examples" / "gallery"
if GALLERY.is_dir():
    app.mount("/gallery", StaticFiles(directory=str(GALLERY), html=True), name="gallery")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (ROOT / "web" / "index.html").read_text()


_WORKERS: dict = {}          # run id -> Process, for runs started by this server
MAX_JOBS = int(os.environ.get("KUMIKO_MAX_JOBS", "2"))
TTL_HOURS = float(os.environ.get("KUMIKO_RUN_TTL_HOURS", "24"))


def _alive() -> list:
    for rid in [r for r, p in _WORKERS.items() if not p.is_alive()]:
        _WORKERS.pop(rid).join(timeout=0)
    return list(_WORKERS)


@app.post("/api/run")
async def api_run(image: UploadFile = File(...), params: str = Form("{}")):
    """Start a plan in a worker process and return its id; poll /api/status/<id>, fetch /api/result/<id>."""
    try:
        raw = json.loads(params)
        clean = {k: v for k, v in raw.items() if v not in ("", None, [])}
        Params(**clean)                              # validate field names and types early
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"bad params: {e}")
    busy = _alive()
    if len(busy) >= MAX_JOBS:
        raise HTTPException(429, f"{len(busy)} plans are already running; try again in a minute")
    cleanup_runs(RUNS, TTL_HOURS, busy=busy)
    rid = uuid.uuid4().hex[:10]
    rdir = RUNS / rid
    rdir.mkdir()
    img_path = rdir / ("input" + Path(image.filename or "img.png").suffix.lower())
    img_path.write_bytes(await image.read())
    write_status(rdir, state="running", stage="Queued", progress=0.0)
    proc = mp.get_context("spawn").Process(target=run_job, args=(str(rdir), str(img_path), clean), daemon=True)
    proc.start()
    _WORKERS[rid] = proc
    return {"run_id": rid}


def _status(rid: str) -> dict:
    rdir = RUNS / Path(rid).name
    f = rdir / "status.json"
    if not f.exists():
        raise HTTPException(404, "unknown run")
    st = json.loads(f.read_text())
    proc = _WORKERS.get(rid)
    if st.get("state") == "running" and proc is not None and not proc.is_alive():
        st = {"state": "error", "stage": "Failed", "progress": 1.0,
              "error": f"the worker process exited unexpectedly (code {proc.exitcode})"}
    return st


@app.get("/api/status/{rid}")
def api_status(rid: str):
    return _status(rid)


@app.get("/api/result/{rid}")
def api_result(rid: str):
    st = _status(rid)
    if st["state"] != "done":
        raise HTTPException(409, st.get("error") or "not finished")
    return JSONResponse(json.loads((RUNS / Path(rid).name / "summary.json").read_text()))


@app.get("/api/zip/{rid}")
def api_zip(rid: str):
    rdir = RUNS / rid
    if not rdir.is_dir():
        raise HTTPException(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in rdir.rglob("*"):
            if f.is_file() and not f.name.startswith("input"):
                z.write(f, f.relative_to(rdir))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename=kumiko_{rid}.zip"})


@app.get("/api/patterns")
def api_patterns():
    from .inserts import catalogue
    return [{"id": k, "name": v.name, "source": v.source, "note": v.note} for k, v in catalogue().items()]
