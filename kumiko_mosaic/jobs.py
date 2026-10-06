"""Background jobs for the web app: each plan runs in its own process and reports progress in a file."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path


def files_for(rid: str, rdir: Path) -> dict:
    plan = (rdir / "plan.svg").exists()
    return {
        "preview_svg": f"/runs/{rid}/{'plan.svg' if plan else 'preview.svg'}",
        "plan_svg": f"/runs/{rid}/plan.svg" if plan else None,
        "preview_png": f"/runs/{rid}/preview.png",
        "compare": f"/runs/{rid}/compare.jpg",
        "report": f"/runs/{rid}/REPORT.md",
        "assembly_csv": f"/runs/{rid}/assembly_map.csv",
        "assembly_pdf": f"/runs/{rid}/assembly_sheet.pdf",
        "assembly_txt": f"/runs/{rid}/assembly_map.txt",
        "bag_labels": f"/runs/{rid}/bag_labels.pdf",
        "zip": f"/api/zip/{rid}",
        "plates": [{"name": f.stem, "svg": f"/runs/{rid}/plates/{f.name}",
                    "threemf": f"/runs/{rid}/plates/{f.with_suffix('.3mf').name}" if f.with_suffix(".3mf").exists() else None,
                    "stl": f"/runs/{rid}/plates/{f.with_suffix('.stl').name}" if f.with_suffix(".stl").exists() else None}
                   for f in sorted((rdir / "plates").glob("*.svg"))],
    }


def write_status(rdir: Path, **kw) -> None:
    tmp = rdir / "status.json.tmp"
    tmp.write_text(json.dumps(kw))
    tmp.replace(rdir / "status.json")             # atomic, so a poll never sees half a file


def run_job(rdir: str, image_path: str, params: dict) -> None:
    """Entry point of the worker process."""
    from .pipeline import Params, run
    rd = Path(rdir)
    t0 = time.time()
    write_status(rd, state="running", stage="Starting", progress=0.0, started=t0)

    def progress(stage: str, frac: float):
        write_status(rd, state="running", stage=stage, progress=round(frac, 3), started=t0)

    try:
        summary = run(image_path, rdir, Params(**params), progress=progress)
        summary["run_id"] = rd.name
        summary["files"] = files_for(rd.name, rd)
        (rd / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
        write_status(rd, state="done", stage="Done", progress=1.0, started=t0, seconds=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001
        write_status(rd, state="error", stage="Failed", progress=1.0, error=f"{type(e).__name__}: {e}", started=t0)


def cleanup_runs(runs: Path, ttl_hours: float = 24.0, keep: int = 40, busy=()) -> int:
    """Delete finished run folders older than ttl_hours, then the oldest beyond `keep`. Returns count removed."""
    dirs = sorted((d for d in runs.iterdir() if d.is_dir() and d.name not in busy), key=lambda d: d.stat().st_mtime)
    removed = 0
    cutoff = time.time() - ttl_hours * 3600
    for d in list(dirs):
        if d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            dirs.remove(d)
            removed += 1
    for d in dirs[:max(0, len(dirs) - keep)]:
        shutil.rmtree(d, ignore_errors=True)
        removed += 1
    return removed
