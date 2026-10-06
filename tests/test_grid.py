import math
from kumiko_mosaic.grid import FrameSpec, build_grid, size_grid, paperview_phase_left, SQRT3


def area(poly):
    n = len(poly)
    return abs(sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))) / 2


def test_cell_counts_and_areas():
    spec = FrameSpec(pitch=50, mitsuke=3, border=6)
    tri = spec.pitch * spec.col_width / 2
    for cols, rows in [(1, 1), (3, 2), (18, 24), (50, 16)]:
        g = build_grid(spec, cols, rows)
        assert g.n_full == cols * (2 * rows - 1) and g.n_half == 2 * cols
        assert len(g.cells) == cols * (2 * rows + 1)
        assert all(abs(area(c.poly) - tri) < 1e-6 for c in g.full_cells())
        assert all(abs(area(c.poly) - tri / 2) < 1e-6 for c in g.half_cells())
        assert abs(sum(area(c.poly) for c in g.cells) - 2 * cols * rows * tri) < 1e-6


def test_paperview_catalogue_panel_sizes():
    # From Paper View's Patreon catalogue: 218 x 81.8 cm, 79.4 x 121.8 cm, 44.8 x 81.8 cm
    spec = FrameSpec(pitch=50, mitsuke=3, border=6)
    for (cols, rows), (w, h) in {(50, 16): (2180, 818), (18, 24): (794, 1218), (10, 16): (448, 818)}.items():
        g = build_grid(spec, cols, rows)
        assert abs(g.outer_width - w) < 0.5, (cols, g.outer_width, w)
        assert abs(g.outer_height - h) < 0.5
    assert abs(spec.inner_side - 44.8038) < 1e-3   # Kumiko Studio's measured tip spacing


def test_phase_rule_and_half_kinds():
    assert paperview_phase_left(18) == 1 and paperview_phase_left(17) == 0
    g = build_grid(FrameSpec(), 18, 24)
    assert g.summary()["top_left_corner"] == "side at corner"
    halves = {(c.col, c.side): c.half for c in g.half_cells()}
    assert halves[(0, "top")] != halves[(1, "top")]           # columns alternate
    assert halves[(0, "top")] != halves[(0, "bottom")]        # same column, mirrored ends
    assert halves[(17, "top")] == halves[(1, "top")]          # right column has phase 0


def test_size_grid_modes():
    spec = FrameSpec(pitch=50, mitsuke=3, border=6)
    cols, rows = size_grid(spec, 1.5, width_mm=800)
    g = build_grid(spec, cols, rows)
    assert g.outer_width <= 800 and g.outer_width > 800 - spec.col_width
    assert abs(g.lattice_width / g.lattice_height - 1.5) < 0.15
    cols, rows = size_grid(spec, 1.0, rows=10)
    assert rows == 10 and abs(cols * spec.col_width - 500) < spec.col_width
    cols, rows = size_grid(spec, 2.0, max_cells=100)
    assert 2 * cols * rows <= 100
