"""Local web UI: upload an image, tweak parameters, get the plan and plate files.

Run:  uvicorn kumiko_mosaic.web:app --reload   (then open http://127.0.0.1:8000)
"""
from __future__ import annotations

import io
import json
import shutil
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .pipeline import Params, run

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
RUNS.mkdir(exist_ok=True)

app = FastAPI(title="kumiko mosaic")
app.mount("/runs", StaticFiles(directory=str(RUNS)), name="runs")
GALLERY = ROOT / "examples" / "gallery"
if GALLERY.is_dir():
    app.mount("/gallery", StaticFiles(directory=str(GALLERY), html=True), name="gallery")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (ROOT / "web" / "index.html").read_text()


@app.post("/api/run")
async def api_run(image: UploadFile = File(...), params: str = Form("{}")):
    try:
        raw = json.loads(params)
        # drop empty strings / nulls so dataclass defaults apply
        clean = {k: v for k, v in raw.items() if v not in ("", None, [])}
        p = Params(**clean)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"bad params: {e}")
    rid = uuid.uuid4().hex[:10]
    rdir = RUNS / rid
    rdir.mkdir()
    img_path = rdir / ("input" + Path(image.filename or "img.png").suffix.lower())
    img_path.write_bytes(await image.read())
    try:
        summary = run(str(img_path), str(rdir), p)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(rdir, ignore_errors=True)
        raise HTTPException(400, str(e))
    summary["run_id"] = rid
    summary["files"] = {
        "preview_svg": f"/runs/{rid}/preview.svg",
        "preview_png": f"/runs/{rid}/preview.png",
        "compare": f"/runs/{rid}/compare.jpg",
        "report": f"/runs/{rid}/REPORT.md",
        "assembly_csv": f"/runs/{rid}/assembly_map.csv",
        "assembly_pdf": f"/runs/{rid}/assembly_sheet.pdf",
        "assembly_txt": f"/runs/{rid}/assembly_map.txt",
        "zip": f"/api/zip/{rid}",
        "plates": [{"name": f.stem, "svg": f"/runs/{rid}/plates/{f.name}",
                    "threemf": f"/runs/{rid}/plates/{f.with_suffix('.3mf').name}" if f.with_suffix('.3mf').exists() else None,
                    "stl": f"/runs/{rid}/plates/{f.with_suffix('.stl').name}" if f.with_suffix('.stl').exists() else None}
                   for f in sorted((rdir / "plates").glob("*.svg"))],
    }
    (rdir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    return JSONResponse(summary)


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
