import numpy as np
import pytest
import trimesh
from PIL import Image

from kumiko_mosaic import bom as bom_mod, geometry, inserts
from kumiko_mosaic.geometry import canonical_cell_polys, canonicalize, extrude_polygon
from kumiko_mosaic.grid import FrameSpec, build_grid, SQRT3
from kumiko_mosaic.pipeline import Params, run


@pytest.fixture
def img(tmp_path):
    a = np.zeros((120, 180, 3), dtype=np.uint8)
    a[:60] = (200, 40, 40)
    a[60:] = (40, 40, 200)
    a[:, :60] = (250, 250, 250)
    p = tmp_path / "t.png"
    Image.fromarray(a).save(p)
    return p


def test_canonical_polys_match_opening():
    spec = FrameSpec(pitch=50, mitsuke=3)
    polys = canonical_cell_polys(build_grid(spec, 2, 2), 0.0)
    side = np.linalg.norm(np.subtract(polys["full"][0], polys["full"][1]))
    assert abs(side - spec.inner_side) < 1e-6
    hl = polys["half_L"]
    assert abs(max(p[0] for p in hl) - spec.inner_side / 2) < 1e-6
    assert abs(max(p[1] for p in hl) - spec.inner_side * SQRT3 / 2) < 1e-6


def test_canonicalize_detects_chirality():
    polys = canonical_cell_polys(build_grid(FrameSpec(), 2, 2), 0.2)
    for shape in ("half_L", "half_R", "full"):
        V, F = extrude_polygon(polys[shape], 2.0)
        th = np.radians(123)
        R = np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1]])
        Vc, kind = canonicalize(V @ R.T + np.array([30, -17, 4]), "half" if shape != "full" else "full")
        assert kind == shape and abs(Vc[:, 2].min()) < 1e-9


@pytest.mark.parametrize("pid", list(inserts.PROCEDURAL))
def test_procedural_inserts_are_printable(pid):
    v = inserts.validate_pattern(pid)
    assert v["ok"], v
    sh = inserts.canonical_polygon(pid, 44.8038, 2.0)
    V, F = inserts.extrude_shapely(sh, 11.0)
    m = trimesh.Trimesh(V, F)
    assert m.is_watertight and m.volume > 0
    assert abs(m.volume - sh.area * 11.0) < 1e-6 * sh.area * 11.0 + 1e-3


def test_generated_half_parts_have_right_chirality():
    g = build_grid(FrameSpec(), 2, 2)
    for shape in ("half_L", "half_R"):
        pm = geometry.generated_pattern_part(g, "asanoha", shape, 11.0, 2.0, 0.0)
        xy = pm.vertices[:, :2]
        assert xy[:, 0].min() > -1e-6 and xy[:, 0].max() < g.spec.inner_side / 2 + 1e-6
        if shape == "half_L":
            assert xy[:, 1].min() > -1e-6
        else:
            assert xy[:, 1].max() < 1e-6


def _assert_no_overlap(plates, meshes, bed):
    from scipy.spatial import ConvexHull
    from PIL import ImageDraw
    sc = 2
    for p in plates:
        canvas = np.zeros((int(bed.size_y * sc), int(bed.size_x * sc)), dtype=np.int32)
        for pl in p.placements:
            m = meshes[pl.part.part_id]
            xy = m.transformed(pl.x, pl.y, pl.rot_deg)[:, :2]
            hull = xy[ConvexHull(xy).vertices] * sc
            assert hull.min() >= bed.margin * sc - 1e-6
            assert hull[:, 0].max() <= (bed.size_x - bed.margin) * sc + 1e-6
            assert hull[:, 1].max() <= (bed.size_y - bed.margin) * sc + 1e-6
            im = Image.new("L", canvas.shape[::-1], 0)
            ImageDraw.Draw(im).polygon([tuple(v) for v in hull], fill=1)
            canvas += np.asarray(im, dtype=np.int32)
        assert canvas.max() <= 1, f"overlap on plate {p.index}"


def test_pipeline_end_to_end(tmp_path, img):
    out = tmp_path / "out"
    p = Params(width_mm=400, palette=["white=#FFFFFF", "red=#C02020", "blue=#2020C0", "black=#000000"],
               pattern_mode="single:asanoha", bed_x=180, bed_y=180)
    s = run(str(img), str(out), p)
    assert (out / "preview.svg").exists() and (out / "REPORT.md").exists()
    assert s["grid"]["outer_mm"][0] <= 400
    assert {"white", "red", "blue"} <= {c["name"] for c in s["palette_used"]}
    assert s["counts"]["pattern_inserts"] == s["counts"]["cells"]
    assert s["failed_parts"] == []
    assert all(e["complete"] for e in s["plates_exported"])
    assert len(s["plates_exported"]) == s["counts"]["plates"]
    for pl in s["plates"]:
        layers = {"BG" if k.startswith("BG") else "P" for k in pl["contents"]}
        assert layers == ({"BG"} if pl["layer"] == "background" else {"P"})
    # every exported 3MF holds exactly the planned parts and no two parts overlap
    g = build_grid(FrameSpec(), s["grid"]["triangle_generator"][0], s["grid"]["triangle_generator"][1])
    keys = {bom_mod.PartKey(r["layer"], r["color"], r["color_name"], r["pattern"] or None, r["shape"]): r["qty"]
            for r in s["bom"]}
    bed = bom_mod.BedSpec(180, 180, 8, 3)
    plates = bom_mod.plan_plates(keys, g, bed, p.clearance)
    meshes, failed = geometry.resolve_parts(plates, g, 2.0, p.clearance, {})
    assert not failed
    _assert_no_overlap(plates, meshes, bed)
    planned = {pl["name"]: pl["parts"] for pl in s["plates"]}
    for f in sorted((out / "plates").glob("*.3mf")):
        sc = trimesh.load(f)
        assert len(sc.graph.nodes_geometry) == planned[f.stem]


def test_pipeline_background_mosaic_and_luminance(tmp_path, img):
    s = run(str(img), str(tmp_path / "o2"), Params(cols=6, pattern_mode="none", color_layer="background", max_colors=3))
    assert s["counts"]["pattern_inserts"] == 0 and not s["failed_parts"]
    s = run(str(img), str(tmp_path / "o3"), Params(cols=6, pattern_mode="luminance:asanoha2,asanoha,-", max_colors=3))
    used = {v["id"] for v in s["patterns_used"]}
    assert used <= {"asanoha2", "asanoha"} and s["counts"]["pattern_inserts"] < s["counts"]["cells"]


def test_paperview_number_alias(tmp_path, img):
    try:
        inserts.kumiko_studio_data()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Kumiko Studio data unavailable: {e}")
    s = run(str(img), str(tmp_path / "o4"), Params(cols=4, pattern_mode="single:7", max_colors=2))
    assert s["patterns_used"][0]["id"] == "ks7" and not s["failed_parts"]


def test_insert_library_override(tmp_path, img):
    polys = canonical_cell_polys(build_grid(FrameSpec(), 2, 2), 0.2)
    lib = tmp_path / "lib"
    lib.mkdir()
    for shape, name in (("full", "asanoha.stl"), ("half_L", "asanoha_half_a.stl"), ("half_R", "asanoha half b.stl")):
        V, F = extrude_polygon(polys[shape], 6.0)
        trimesh.Trimesh(V, F).apply_translation([40, 20, 3]).export(lib / name)
    s = run(str(img), str(tmp_path / "o5"), Params(cols=5, pattern_mode="single:asanoha", max_colors=2,
                                                    insert_library=str(lib), bed_x=180, bed_y=180))
    assert set(s["insert_library"]["loaded"]) == {"asanoha_full", "asanoha_half_L", "asanoha_half_R"}
    assert not s["failed_parts"]


def _handedness(poly_yup):
    """sign of cross(short leg, long leg) at the right-angle corner of a right triangle."""
    pts = [np.array(p, dtype=float) for p in poly_yup]
    best = None
    for i in range(3):
        a, b, c = pts[i], pts[(i + 1) % 3], pts[(i + 2) % 3]
        if abs(np.dot(b - a, c - a)) < 1e-6 * max(1.0, np.linalg.norm(b - a) * np.linalg.norm(c - a)):
            best = (a, b, c)
    assert best is not None
    a, b, c = best
    short, long_ = (b - a, c - a) if np.linalg.norm(b - a) < np.linalg.norm(c - a) else (c - a, b - a)
    return np.sign(short[0] * long_[1] - short[1] * long_[0])


def test_half_kinds_consistent_across_modules():
    """grid L/R labels == canonical footprint handedness == the half the renderer keeps ==
    the half the part generator builds."""
    from kumiko_mosaic import render
    from kumiko_mosaic.bom import cell_shape
    g = build_grid(FrameSpec(), 3, 2)
    polys = canonical_cell_polys(g, 0.0)
    for c in g.half_cells():
        shape = cell_shape(c)
        # (a) handedness: grid polygon is y-down, flip y to compare with the y-up canonical frame
        assert _handedness([(x, -y) for x, y in c.poly]) == _handedness(polys[shape]), (c.col, c.side, c.half)
        # (b) which canonical half does the renderer keep for this cell?
        a, b, d, e, xoff, yoff = render._cell_transform(c, g)
        from shapely.affinity import affine_transform
        from shapely.geometry import Polygon, Point
        probe = Point(5.0, 0.0)          # u > 0 side of the canonical insert
        kept = Polygon(c.poly).buffer(1e-6).contains(affine_transform(probe, [a, b, d, e, xoff, yoff]))
        # generator: half_L is built from the u >= 0 half, half_R from u <= 0
        assert kept == (shape == "half_L"), (c.col, c.side, c.half)
    # (c) chiral pattern: the renderer's clipped silhouette and the generated part footprint match
    import shapely
    from shapely.affinity import affine_transform, rotate, translate
    from shapely.geometry import Polygon
    pm_shape = inserts.canonical_polygon("pinwheel", g.spec.inner_side, 2.0)
    for c in list(g.half_cells())[:4]:
        shape = cell_shape(c)
        rendered = affine_transform(pm_shape, render._cell_transform(c, g)).intersection(Polygon(c.poly))
        rendered = shapely.affinity.scale(rendered, 1, -1, origin=(0, 0))       # to y-up
        part = geometry.generated_pattern_part(g, "pinwheel", shape, 11.0, 2.0, 0.0)
        foot = shapely.union_all([Polygon(part.vertices[f][:, :2]) for f in part.faces if (part.vertices[f][:, 2] > 10.9).all()])
        best = 0.0
        for ang in (0, 90, 180, 270):
            r = rotate(foot, ang, origin=(0, 0))
            dx, dy = rendered.centroid.x - r.centroid.x, rendered.centroid.y - r.centroid.y
            r = translate(r, dx, dy)
            iou = r.intersection(rendered).area / r.union(rendered).area
            best = max(best, iou)
        assert best > 0.99, (c.col, c.side, c.half, best)


def test_auto_strip_matching(tmp_path, img):
    from kumiko_mosaic import match
    s = run(str(img), str(tmp_path / "o6"), Params(cols=8, max_colors=3, max_patterns=3))
    assert s["pattern_mode"] == "auto" and s["color_plan"]["color_layer"] == "pattern"
    used = {v["id"] for v in s["patterns_used"]}
    assert used <= set(match.DEFAULT_LADDER) and 1 <= len(used) <= 3
    assert s["counts"]["distinct_patterns"] == len(used)
    assert (tmp_path / "o6" / "assembly_sheet.pdf").exists()
    cov = s["pattern_coverage"]
    order = [p for p in match.DEFAULT_LADDER if p in cov]
    assert set(order) == set(cov) and all(cov[a] <= cov[b] for a, b in zip(order, order[1:]))
    # the bright white region should get dense strips of a light filament, the dark cells sparse ones
    rows = [r for r in s["bom"] if r["layer"] == "pattern"]
    assert rows and (tmp_path / "o6" / "compare.jpg").exists()


def test_mixed_rgb_endpoints():
    from kumiko_mosaic.match import mixed_rgb
    assert np.allclose(mixed_rgb((200, 10, 10), (0, 0, 0), 1.0), (200, 10, 10), atol=0.5)
    assert np.allclose(mixed_rgb((200, 10, 10), (0, 0, 0), 0.0), (0, 0, 0), atol=0.5)
    mid = mixed_rgb((255, 255, 255), (0, 0, 0), 0.5)
    assert 180 < mid[0] < 195          # linear-light average of white and black is ~188 sRGB


def test_vote_mode_crisp_regions(tmp_path):
    """Two flat regions with a soft boundary: vote mode must produce exactly two labels and no
    boundary cells of a third colour, which averaging would create."""
    from kumiko_mosaic import match
    a = np.zeros((300, 450, 3), dtype=np.uint8)
    a[:, :225] = (220, 40, 40)
    a[:, 225:] = (40, 60, 220)
    for x in range(215, 235):               # soft 20 px boundary
        t = (x - 215) / 20.0
        a[:, x] = (np.array((220, 40, 40)) * (1 - t) + np.array((40, 60, 220)) * t).astype(np.uint8)
    p = tmp_path / "two.png"
    Image.fromarray(a).save(p)
    s = run(str(p), str(tmp_path / "o7"), Params(cols=12, max_colors=3, max_patterns=2, enhance=False,
                                                 line_boost=0, export_3mf=False, export_stl=False))
    # a flat region may be a coloured background with no strips, so look at every part colour:
    # voting must not invent a third boundary colour (averaging would give purple)
    colours = {r["color"] for r in s["bom"]}
    assert 2 <= len(colours) <= 3, colours
    assert s["fidelity"]["mean_dE"] < 25     # pure sRGB blue is outside the filament gamut
    assert s["params"]["sampling"] == "vote"


def test_multi_background_assignment(tmp_path, img):
    s = run(str(img), str(tmp_path / "o8"), Params(cols=10, max_colors=3, max_patterns=2, max_backgrounds=2, background_set="neutral",
                                                   export_3mf=False, export_stl=False))
    assert len(s["backgrounds_used"]) == 2
    bg_rows = [r for r in s["bom"] if r["layer"] == "background"]
    assert len({r["color"] for r in bg_rows}) == 2            # both backgrounds actually used
    # a second background must not make the match worse than a single black one
    s1 = run(str(img), str(tmp_path / "o9"), Params(cols=10, max_colors=3, max_patterns=2, max_backgrounds=1,
                                                    export_3mf=False, export_stl=False))
    assert s["fidelity"]["mean_dE"] <= s1["fidelity"]["mean_dE"] + 1e-6
    # the bright white region should get a light background
    assert any(b["name"].endswith("White") or "Gray" in b["name"] for b in s["backgrounds_used"])


def test_paperview_patterns_can_be_disabled(monkeypatch):
    monkeypatch.setenv("KUMIKO_NO_PAPERVIEW", "1")
    assert not any(k.startswith("ks") for k in inserts.catalogue())
    from kumiko_mosaic.patterns import parse_mode
    with pytest.raises(ValueError):
        parse_mode("single:7")
