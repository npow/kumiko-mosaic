"""Command line interface.

Example:
  python -m kumiko_mosaic.cli photo.jpg out/ --width-mm 800 \
      --palette "black=#000000" "latte=#D2AC86" "blue=#1F4E9A" "red=#B5402B" \
      --pattern-mode single:asanoha --bed 256
  python -m kumiko_mosaic.cli --list-patterns
"""
from __future__ import annotations

import argparse
import json
import sys

from .pipeline import Params, run


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="kumiko-mosaic", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", nargs="?")
    ap.add_argument("out_dir", nargs="?")
    ap.add_argument("--list-patterns", action="store_true", help="print the pattern catalogue and exit")
    f = ap.add_argument_group("frame")
    f.add_argument("--pitch", type=float, default=50.0, help="Grid_Pitch: triangle side, mm")
    f.add_argument("--mitsuke", type=float, default=3.0, help="Grid_Thickness: bar width, mm")
    f.add_argument("--border", type=float, default=6.0, help="Border_Thickness, mm")
    f.add_argument("--orientation", choices=["auto", "side-corners", "vertex-corners"], default="auto",
                   help="half-triangle phase of the left column (auto = Paper View's rule)")
    s = ap.add_argument_group("size (optional: give one, the other axis follows the image aspect; default 18 columns)")
    s.add_argument("--width-mm", type=float)
    s.add_argument("--height-mm", type=float)
    s.add_argument("--cols", type=int, help="triangle-altitude columns across")
    s.add_argument("--rows", type=int, help="pitches tall")
    s.add_argument("--max-cells", type=int, help="largest grid with at most this many triangle areas")
    s.add_argument("--measure", choices=["outer", "lattice"], default="outer")
    i = ap.add_argument_group("image and colour")
    i.add_argument("--fit", choices=["cover", "contain", "stretch"], default="cover")
    i.add_argument("--line-boost", type=float, default=4.0,
                   help="keep thin contrasting features that averaging erases (cables, masts); 4-8 is strong, 0 = off")
    i.add_argument("--line-coherence", type=float, default=34.0,
                   help="max colour spread of a thin feature to count as a line rather than texture (lower = fewer speckles)")
    i.add_argument("--palette", nargs="*", default=[], help='filaments you own: "name=#RRGGBB" ... (empty = auto)')
    i.add_argument("--max-colors", type=int, default=8)
    i.add_argument("--filament-set", choices=["bambu", "bambu-matte", "bambu-basic"], default="bambu",
                   help="purchasable catalogue to pick colours from when --palette is not given")
    i.add_argument("--no-enhance", action="store_true", help="skip autocontrast/saturation boost")
    i.add_argument("--dither", action="store_true")
    i.add_argument("--color-layer", choices=["pattern", "background", "both"], default="pattern",
                   help="pattern (default): coloured kumiko strips over one background colour, density matched to the image; background: flat tiles carry the image")
    i.add_argument("--background-color", default="Matte Charcoal=#000000")
    i.add_argument("--pattern-color", default="Matte Latte Brown=#D3B7A7")
    i.add_argument("--frame-color", default="#1A1A1A", help="frame filament colour; dark bars keep the image legible (Paper View latte = #D2AC86)")
    p = ap.add_argument_group("patterns")
    p.add_argument("--pattern-mode", default="auto",
                   help="auto (match filament + strip density per cell; optional auto:id1,id2,... ladder) | none | single:<id> | luminance:<ids> | color")
    p.add_argument("--max-patterns", type=int, default=4, help="auto mode: max distinct patterns (fewer = easier assembly)")
    p.add_argument("--min-hole", type=float, default=2.5, help="auto mode: skip patterns whose openings are narrower than this (mm)")
    p.add_argument("--color-pattern-map", default="{}", help='JSON {"#hex or name": "id"} for --pattern-mode color')
    p.add_argument("--edge-halves", choices=["pattern", "background"], default="pattern")
    p.add_argument("--skip-background-matches", action="store_true")
    p.add_argument("--labels", action="store_true", help="write pattern ids on the preview")
    g = ap.add_argument_group("insert geometry")
    g.add_argument("--strip", type=float, default=2.0, help="kumiko strip width, mm")
    g.add_argument("--insert-depth", type=float, default=11.0)
    g.add_argument("--clearance", type=float, default=0.15, help="inset per edge, mm")
    g.add_argument("--bg-thickness", type=float, default=2.0)
    pr = ap.add_argument_group("printing")
    pr.add_argument("--bed", type=float, nargs="+", default=[256.0], help="bed size mm: one value or X Y")
    pr.add_argument("--bed-margin", type=float, default=8.0)
    pr.add_argument("--gap", type=float, default=3.0)
    pr.add_argument("--insert-library", help="folder of STL/3MF files that override generated parts")
    pr.add_argument("--no-stl", action="store_true")
    pr.add_argument("--no-3mf", action="store_true")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.list_patterns:
        from .inserts import catalogue
        for pid, info in catalogue().items():
            print(f"{pid:12s} {info.name:40s} {info.source}{'  ' + info.note if info.note else ''}")
        return 0
    if not a.image or not a.out_dir:
        ap.error("image and out_dir are required")
    bed = a.bed if len(a.bed) == 2 else [a.bed[0], a.bed[0]]
    params = Params(pitch=a.pitch, mitsuke=a.mitsuke, border=a.border, orientation=a.orientation,
                    width_mm=a.width_mm, height_mm=a.height_mm, cols=a.cols, rows=a.rows,
                    max_cells=a.max_cells, measure=a.measure, fit=a.fit, line_boost=a.line_boost, line_coherence=a.line_coherence, palette=a.palette,
                    max_colors=a.max_colors, filament_set=a.filament_set, dither=a.dither, enhance=not a.no_enhance, color_layer=a.color_layer,
                    background_color=a.background_color, pattern_color=a.pattern_color, frame_color=a.frame_color,
                    pattern_mode=a.pattern_mode, max_patterns=a.max_patterns, min_hole_mm=a.min_hole, color_pattern_map=json.loads(a.color_pattern_map),
                    edge_halves=a.edge_halves, skip_background_matches=a.skip_background_matches,
                    strip_mm=a.strip, insert_depth=a.insert_depth, clearance=a.clearance,
                    bg_thickness=a.bg_thickness, bed_x=bed[0], bed_y=bed[1], bed_margin=a.bed_margin,
                    part_gap=a.gap, insert_library=a.insert_library, export_stl=not a.no_stl,
                    export_3mf=not a.no_3mf, labels=a.labels)
    s = run(a.image, a.out_dir, params)
    g = s["grid"]
    print(f"Frame: Triangle_Generator [{g['triangle_generator'][0]}, {g['triangle_generator'][1]}], "
          f"outer ~{g['outer_mm'][0]} x {g['outer_mm'][1]} mm, width {g['width_parity']}")
    c = s["counts"]
    print(f"Cells {c['cells']}  pattern inserts {c['pattern_inserts']} ({c['distinct_patterns']} patterns, "
          f"{c['distinct_parts']} distinct parts)  background inserts {c['background_inserts']}  plates {c['plates']}")
    for name, e in s["material_estimate_g"].items():
        print(f"  {name:12s} {e['color']}  pattern {e['pattern_inserts']:4d}  bg {e['background_inserts']:4d}  ~{e['grams']} g")
    for v in s["patterns_used"]:
        if not v.get("ok"):
            print(f"WARNING pattern {v['id']}: not a single piece touching all edges")
    if s["failed_parts"]:
        print("Parts not generated:", ", ".join(s["failed_parts"]))
    print(f"Wrote {a.out_dir}/REPORT.md, preview.png, assembly_map.csv, plates/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
