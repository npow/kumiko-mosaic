import os
import time

import numpy as np
import pytest
from PIL import Image

from kumiko_mosaic.jobs import cleanup_runs


def test_cleanup_runs_by_age_and_count(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    now = time.time()
    for i, age_h in enumerate([50, 30, 5, 4, 3, 2, 1]):
        d = runs / f"r{i}"
        d.mkdir()
        (d / "f").write_text("x")
        os.utime(d, (now - age_h * 3600, now - age_h * 3600))
    (runs / "busy").mkdir()
    os.utime(runs / "busy", (now - 99 * 3600, now - 99 * 3600))
    removed = cleanup_runs(runs, ttl_hours=24, keep=3, busy=("busy",))
    left = sorted(p.name for p in runs.iterdir())
    assert "busy" in left                         # a running job is never deleted
    assert "r0" not in left and "r1" not in left  # older than the ttl
    assert len([n for n in left if n != "busy"]) == 3     # then trimmed to the newest three
    assert removed == 4


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import kumiko_mosaic.web as web
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(web, "RUNS", runs)
    monkeypatch.setattr(web, "MAX_JOBS", 1)
    return TestClient(web.app)


def _png(tmp_path):
    a = np.zeros((90, 130, 3), dtype=np.uint8)
    a[:, :65] = (220, 40, 40)
    a[:, 65:] = (40, 60, 200)
    p = tmp_path / "t.png"
    Image.fromarray(a).save(p)
    return p


def test_async_run_status_result(client, tmp_path):
    p = _png(tmp_path)
    with open(p, "rb") as f:
        r = client.post("/api/run", files={"image": ("t.png", f, "image/png")},
                        data={"params": '{"cols": 6, "max_colors": 3, "max_patterns": 2, "max_backgrounds": 2, "background_set": "neutral", "export_3mf": false, "export_stl": false}'})
    assert r.status_code == 200
    rid = r.json()["run_id"]
    # a second job while the first is running is refused rather than queued without limit
    with open(p, "rb") as f:
        r2 = client.post("/api/run", files={"image": ("t.png", f, "image/png")}, data={"params": "{}"})
    assert r2.status_code == 429
    stages, state = set(), None
    for _ in range(300):
        st = client.get(f"/api/status/{rid}").json()
        stages.add(st["stage"])
        state = st["state"]
        if state != "running":
            break
        time.sleep(0.2)
    assert state == "done", st
    res = client.get(f"/api/result/{rid}").json()
    assert res["run_id"] == rid and res["files"]["plan_svg"] and res["grid"]["triangle_generator"][0] == 6
    assert len(stages) >= 2                        # progress text changed while it ran


def test_bad_params_and_unknown_run(client, tmp_path):
    with open(_png(tmp_path), "rb") as f:
        r = client.post("/api/run", files={"image": ("t.png", f, "image/png")}, data={"params": '{"nonsense": 1}'})
    assert r.status_code == 400
    assert client.get("/api/status/doesnotexist").status_code == 404


def _wait_idle():
    import kumiko_mosaic.web as web
    for _ in range(100):
        if not web._alive():
            return
        time.sleep(0.1)


def test_failed_run_reports_error(client, tmp_path):
    _wait_idle()
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    with open(bad, "rb") as f:
        rid = client.post("/api/run", files={"image": ("bad.png", f, "image/png")}, data={"params": '{"cols": 6}'}).json()["run_id"]
    for _ in range(100):
        st = client.get(f"/api/status/{rid}").json()
        if st["state"] != "running":
            break
        time.sleep(0.2)
    assert st["state"] == "error" and st["error"]
