"""End-to-end: image + parameters -> output folder with everything needed to print."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image, ImageOps

from . import bom as bom_mod
import numpy as np

from . import filaments, geometry, inserts, match, render
from .grid import FrameSpec, build_grid, size_grid
from .imagemap import Filament, assign_colors, fit_image, parse_palette, sample_cells
from .patterns import assign_patterns


@dataclass
class Params:
    # frame (Paper View generator parameters)
    pitch: float = 50.0
    mitsuke: float = 3.0
    border: float = 6.0
    orientation: str = "auto"              # auto (Paper View rule) | side-corners | vertex-corners
    # sizing (exactly one, or cols+rows)
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    cols: Optional[int] = None             # triangle altitudes across
    rows: Optional[int] = None             # pitches tall
    max_cells: Optional[int] = None
    measure: str = "outer"
    # image
    fit: str = "cover"
    crop: Optional[List[float]] = None     # [left, top, right, bottom] as fractions of the image, applied first
    enhance: bool = True                   # autocontrast + mild saturation boost before sampling
    sample_shrink: float = 0.85
    sampling: str = "mean"                 # mean (nearest option to each cell's average colour, default) | vote (region voting)
    smooth_mm: float = 10.0                # vote mode: edge-preserving smoothing radius before quantising
    line_boost: float = 4.0                # keep thin contrasting features (cables, masts); 0 = off, 8 = strong
    line_coherence: float = 34.0           # max colour spread (CIELAB) of a feature to count as a line, not texture
    # colours
    palette: List[str] = field(default_factory=list)   # filaments you own ("Name=#hex"); empty -> pick from filament_set
    filament_set: str = "bambu"            # purchasable catalogue to pick from: bambu | bambu-matte | bambu-basic
    max_colors: int = 8
    dither: bool = False
    dither_strength: float = 0.3           # share of each cell's colour error passed to its neighbours
    color_layer: str = "pattern"           # pattern (coloured strips over one background, default) | background | both
    background_color: str = "Matte Charcoal=#000000"   # used when max_backgrounds == 1
    max_backgrounds: int = 3               # auto mode: background filaments chosen per panel (1 = single colour)
    background_set: str = "all"            # all (any catalogue colour; saturated colours need it) | neutral (greys only, faster)
    sharpen: float = 0.0                   # unsharp mask percent before smoothing (0 = off)
    pattern_color: str = "Matte Latte Brown=#D3B7A7"
    frame_color: str = "#1A1A1A"            # frame filament; dark bars keep the picture legible (Paper View latte = #D2AC86)
    # patterns
    pattern_mode: str = "auto"             # auto[:ladder] | none | single:<id> | luminance:<ids> | color
    max_patterns: int = 4                  # auto mode: at most this many distinct patterns (assembly effort)
    min_hole_mm: float = 2.5               # auto mode: patterns whose openings get narrower than this are not used
    color_pattern_map: Dict[str, str] = field(default_factory=dict)
    edge_halves: str = "pattern"
    skip_background_matches: bool = False
    # insert geometry
    strip_mm: float = 2.0                  # kumiko strip width (his inserts: 2 mm)
    insert_depth: float = 11.0             # his inserts: 11 mm in a 12 mm frame
    clearance: float = 0.15                # inset per edge so inserts slide in
    bg_thickness: float = 2.0              # generated background insert thickness
    # printing
    bed_x: float = 256.0
    bed_y: float = 256.0
    bed_margin: float = 8.0
    part_gap: float = 3.0                  # preferred gap between parts on a plate (mm)
    min_part_gap: float = 1.5              # the packer may reduce the gap to this if it fits more parts
    insert_library: Optional[str] = None   # optional folder of STL/3MF overrides
    export_stl: bool = True
    export_3mf: bool = True
    write_svg: bool = False                # preview_only mode: also write plan.svg (compact, zoomable)
    preview_only: bool = False             # catalog mode: plan + preview + fidelity, skip plates and assembly files
    labels: bool = False                   # pattern ids on the preview


def _split_named(s: str) -> Filament:
    return parse_palette([s])[0]


class _Timer:
    """Wall-clock per pipeline stage, reported in summary.json under 'seconds'."""

    def __init__(self):
        import time
        self._t = time.time()
        self._time = time
        self.stages = {}

    def lap(self, name: str):
        now = self._time.time()
        self.stages[name] = round(now - self._t, 1)
        self._t = now


def run(image_path: str, out_dir: str, params: Params, progress=None) -> dict:
    """Plan an image. `progress(stage_text, fraction)` is called as the run advances (optional)."""
    tm = _Timer()

    def step(text: str, frac: float):
        if progress:
            progress(text, frac)

    step("Reading the image", 0.02)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    img = Image.open(image_path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    crop_used = None
    if params.crop:
        crop_used = [float(v) for v in params.crop]
    if crop_used:
        l, t, r, b = crop_used
        if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
            raise ValueError(f"bad crop {crop_used}: need 0 <= left < right <= 1 and 0 <= top < bottom <= 1")
        img = img.crop((int(l * img.width), int(t * img.height), max(int(r * img.width), int(l * img.width) + 8),
                        max(int(b * img.height), int(t * img.height) + 8)))
    spec = FrameSpec(pitch=params.pitch, mitsuke=params.mitsuke, border=params.border,
                     insert_depth=params.insert_depth)
    aspect = img.width / img.height
    cols, rows = size_grid(spec, aspect, width_mm=params.width_mm, height_mm=params.height_mm,
                           cols=params.cols, rows=params.rows, max_cells=params.max_cells,
                           measure=params.measure)
    grid = build_grid(spec, cols, rows, params.orientation)

    bgf = _split_named(params.background_color)
    patf = _split_named(params.pattern_color)
    step("Sampling colours", 0.08)
    fitted, ppm = fit_image(img, grid.lattice_width, grid.lattice_height, params.fit, background=bgf.hex,
                            enhance=params.enhance)
    sample_cells(grid, fitted, ppm, params.sample_shrink, line_boost=params.line_boost,
                 line_coherence=params.line_coherence)
    step("Choosing filaments and patterns", 0.18)
    palette = parse_palette(params.palette) if params.palette else None
    coverage = None
    if params.pattern_mode.startswith("auto") and params.color_layer == "pattern":
        ladder = [x.strip() for x in params.pattern_mode.split(":", 1)[1].split(",")] \
            if ":" in params.pattern_mode else match.DEFAULT_LADDER
        usable = match.usable_ladder(grid, ladder, params.strip_mm, params.min_hole_mm)
        if not usable:
            raise ValueError("no pattern in the ladder is printable at this pitch/strip width")
        cov0 = match.coverage_table(grid, usable, params.strip_mm, params.clearance)
        if palette:
            # the spools you own: strips AND backgrounds are chosen from them and nothing else
            n_bg = max(1, params.max_backgrounds)
            bgs = match.choose_backgrounds(grid, palette, palette, usable, cov0, n_bg)
            strips = match.choose_filaments_multi(grid, palette, bgs, usable, cov0, params.max_colors)
        else:
            cand = filaments.catalogue(params.filament_set)
            if params.max_backgrounds <= 1:
                bgs = [bgf]
                strips = match.choose_filaments(grid, cand, bgf.rgb, usable, cov0, params.max_colors)
            else:
                strips = match.choose_filaments(grid, cand, bgf.rgb, usable, cov0, params.max_colors)
                bgs = match.choose_backgrounds(grid, filaments.background_candidates(params.background_set),
                                               strips, usable, cov0, params.max_backgrounds)
                strips = match.choose_filaments_multi(grid, cand, bgs, usable, cov0, params.max_colors)
        sub = match.choose_pattern_subset_multi(grid, bgs, strips, usable, cov0, params.max_patterns) \
            if params.max_patterns else usable
        coverage = {k: cov0[k] for k in sub}
        options = match.build_options(bgs, strips, sub, coverage)
        step("Assigning a filament and pattern to every cell", 0.45)
        if params.sampling == "vote":
            match.assign_options_vote(grid, options, fitted, smooth_mm=params.smooth_mm,
                                      line_boost=params.line_boost, line_coherence=params.line_coherence,
                                      sharpen=params.sharpen)
        else:
            match.assign_options_mean(grid, options, dither=params.dither_strength if params.dither else 0.0)
        if params.edge_halves == "background":
            for c in grid.half_cells():
                c.pattern = None
        usage: Dict[str, float] = {}
        for c in grid.cells:
            if c.pattern:
                usage[c.color] = usage.get(c.color, 0) + 1
        used = sorted([f for f in strips if f.hex in usage], key=lambda f: -usage[f.hex])
        palette_used_bg = bgs
        score = match.fidelity_score(grid, fitted, coverage)
    else:
        if params.pattern_mode.startswith("auto"):
            params.pattern_mode = "single:y"
        used = assign_colors(grid, palette, params.max_colors, params.dither)
        assign_patterns(grid, params.pattern_mode, params.color_pattern_map, params.edge_halves)
        palette_used_bg = [bgf]
        score = None
    if params.skip_background_matches and params.color_layer == "pattern":
        for c in grid.cells:
            if c.color and c.color.upper() == bgf.hex.upper():
                c.pattern = None

    tm.lap("match")
    step("Building parts and plates", 0.62)
    plan = bom_mod.ColorPlan(params.color_layer, bgf.hex, bgf.name, patf.hex, patf.name)
    bom = bom_mod.build_bom(grid, plan)
    if params.preview_only:
        kw = dict(color_layer=params.color_layer, background_color=bgf.hex, pattern_color=patf.hex,
                  frame_color=params.frame_color, strip_mm=params.strip_mm)
        ppm_preview = min(3.0, 3000.0 / max(grid.lattice_width, grid.lattice_height))
        preview = render.png_preview(grid, labels=False, px_per_mm=ppm_preview, **kw)
        render.compare_image(fitted, preview, height=560).save(out / "compare.jpg", quality=82)
        if params.write_svg:
            (out / "plan.svg").write_text(render.svg_compact(grid, background_color=bgf.hex,
                                                             frame_color=params.frame_color, strip_mm=params.strip_mm))
        return {"grid": grid.summary(), "fidelity": score, "palette_used": [{"name": f.name, "hex": f.hex} for f in used],
                "backgrounds_used": [{"name": f.name, "hex": f.hex} for f in palette_used_bg],
                "counts": {"cells": len(grid.cells), "full": grid.n_full, "half": grid.n_half,
                           "pattern_inserts": sum(q for k, q in bom.items() if k.layer == "pattern"),
                           "background_inserts": sum(q for k, q in bom.items() if k.layer == "background"),
                           "distinct_patterns": len({c.pattern for c in grid.cells if c.pattern})},
                "patterns_used": sorted({c.pattern for c in grid.cells if c.pattern})}
    bed = bom_mod.BedSpec(params.bed_x, params.bed_y, params.bed_margin, params.part_gap, params.min_part_gap)
    library = geometry.load_insert_library(params.insert_library)
    plates = bom_mod.plan_plates(bom, grid, bed, params.clearance,
                                 footprint=geometry.library_footprint(library))
    meshes, failed = geometry.resolve_parts(plates, grid, params.bg_thickness, params.clearance, library,
                                            insert_depth=params.insert_depth, strip_mm=params.strip_mm)
    volumes = {pid: geometry.mesh_volume(m) for pid, m in meshes.items()}

    tm.lap("plan_parts")
    step("Drawing previews and assembly sheets", 0.72)
    # ---- files -------------------------------------------------------------------------------
    fitted.save(out / "fitted_image.png")
    kw = dict(color_layer=params.color_layer, background_color=bgf.hex, pattern_color=patf.hex,
              frame_color=params.frame_color, strip_mm=params.strip_mm)
    if params.color_layer == "pattern":
        # compact vector plan (1 MB at 60 columns); the full preview.svg would be 6 MB of the same picture
        (out / "plan.svg").write_text(render.svg_compact(grid, background_color=bgf.hex,
                                                         frame_color=params.frame_color, strip_mm=params.strip_mm))
    else:
        (out / "preview.svg").write_text(render.svg_preview(grid, labels=params.labels, **kw))
    ppm_preview = min(3.0, 4000.0 / max(grid.lattice_width, grid.lattice_height))
    preview = render.png_preview(grid, labels=params.labels, px_per_mm=ppm_preview, **kw)
    preview.save(out / "preview.png")
    render.compare_image(fitted, preview).save(out / "compare.jpg", quality=88)
    (out / "assembly_map.csv").write_text(render.assembly_csv(grid))
    (out / "assembly_map.txt").write_text(render.assembly_pick_list(grid))
    pages = render.assembly_sheet(grid, color_layer=params.color_layer, background_color=bgf.hex,
                                  pattern_color=patf.hex, frame_color=params.frame_color)
    pages[0].save(out / "assembly_sheet.pdf", save_all=True, append_images=pages[1:], resolution=300)
    pages[0].save(out / "assembly_legend.png")

    lab = render.bag_labels(grid, bom, plates, strip_mm=params.strip_mm)
    lab[0].save(out / "bag_labels.pdf", save_all=True, append_images=lab[1:], resolution=300)
    pcodes = render.plate_codes(grid, plates)
    with open(out / "plate_codes.csv", "w", newline="") as fh:
        import csv as _csv
        w = _csv.writer(fh)
        w.writerow(["plate", "file", "filament", "layer", "codes"])
        for r in pcodes:
            w.writerow([r["plate"], r["file"], r["color_name"], r["layer"], "; ".join(f"{k} x{v}" for k, v in r["codes"].items())])

    tm.lap("previews_and_sheets")
    step("Writing plate files", 0.9)
    plates_dir = out / "plates"
    plates_dir.mkdir(exist_ok=True)
    exported = []
    for p in plates:
        have = [pl for pl in p.placements if pl.part.part_id in meshes]
        if not have:
            continue
        base = plates_dir / p.name()
        if params.export_3mf:
            geometry.plate_to_3mf(p, meshes, base.with_suffix(".3mf"))
        if params.export_stl:
            geometry.plate_to_stl(p, meshes, base.with_suffix(".stl"))
        base.with_suffix(".svg").write_text(geometry.plate_svg(p, meshes, bed.size_x, bed.size_y))
        exported.append({"plate": p.index, "file": base.name, "complete": len(have) == len(p.placements)})

    # pattern validation notes (loose parts etc.)
    used_patterns = sorted({c.pattern for c in grid.cells if c.pattern})
    checks = []
    for pid in used_patterns:
        try:
            v = inserts.validate_pattern(pid, spec.inner_side - 2 * 1.7320508 * params.clearance, params.strip_mm)
        except Exception as e:  # noqa: BLE001
            v = {"id": pid, "ok": False, "error": str(e)}
        info = inserts.catalogue().get(pid)
        v["name"] = info.name if info else pid
        v["source"] = info.source if info else "?"
        if info and info.note:
            v["note"] = info.note
        checks.append(v)

    tm.lap("plate_files")
    summary = {
        "seconds": tm.stages,
        "grid": grid.summary(),
        "image": {"source": str(image_path), "fit": params.fit, "crop": crop_used, "aspect": round(aspect, 4),
                  "lattice_aspect": round(grid.lattice_width / grid.lattice_height, 4)},
        "palette_used": [{"name": f.name, "hex": f.hex} for f in used],
        "backgrounds_used": [{"name": f.name, "hex": f.hex} for f in palette_used_bg],
        "fidelity": score,
        "filament_set": params.filament_set if not params.palette else "user",
        "color_plan": asdict(plan),
        "pattern_mode": params.pattern_mode,
        "pattern_coverage": {k: round(v, 3) for k, v in (coverage or {}).items()},
        "patterns_used": checks,
        "insert_geometry": {"opening_tip_to_tip_mm": round(spec.inner_side, 3), "clearance_mm": params.clearance,
                            "strip_mm": params.strip_mm, "insert_depth_mm": params.insert_depth,
                            "background_thickness_mm": params.bg_thickness,
                            "CALIBRATE": "print one pattern insert and one background triangle first; adjust clearance"},
        "bed": asdict(bed),
        "counts": {"cells": len(grid.cells), "full": grid.n_full, "half": grid.n_half,
                   "distinct_patterns": len({c.pattern for c in grid.cells if c.pattern}),
                   "distinct_parts": len({k.part_id + k.color for k in bom if k.layer == "pattern"}),
                   "pattern_inserts": sum(q for k, q in bom.items() if k.layer == "pattern"),
                   "background_inserts": sum(q for k, q in bom.items() if k.layer == "background"),
                   "plates": len(plates)},
        "material_estimate_g": bom_mod.estimate_material(bom, volumes),
        "plates": [{**r, "codes": c["codes"]} for r, c in zip(bom_mod.plate_summary(plates), render.plate_codes(grid, plates))],
        "plates_exported": exported,
        "failed_parts": sorted(set(failed)),
        "insert_library": {"folder": params.insert_library, "loaded": sorted(library.keys())},
        "bom": bom_mod.bom_rows(bom),
        "insert_counts": bom_mod.insert_generator_checklist(bom),
        "params": asdict(params),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    (out / "REPORT.md").write_text(report_markdown(summary))
    return summary


def report_markdown(s: dict) -> str:
    g = s["grid"]
    L = ["# Kumiko mosaic build plan\n", "## Frame (Paper View's Frame Reference Generator)\n"]
    L.append(f"- Triangle_Generator: **[{g['triangle_generator'][0]}, {g['triangle_generator'][1]}]** "
             f"= {g['cols_triangle_altitudes_wide']} triangle-altitude columns wide x {g['rows_pitches_tall']} pitches tall")
    L.append(f"- Grid_Pitch {g['pitch_mm']} mm, Grid_Thickness {g['grid_thickness_mm']} mm, Border {g['border_mm']} mm")
    L.append(f"- Width is **{g['width_parity']}** -> print plate \"{g['tr_wall_hanger_plate']}\"")
    L.append(f"- Outer panel approx **{g['outer_mm'][0]} x {g['outer_mm'][1]} mm** (lattice {g['lattice_mm'][0]} x {g['lattice_mm'][1]})")
    L.append(f"- Check on his reference card: Size A = {g['kumikodesigner_size_a_height']}, Size B = {g['kumikodesigner_size_b_width']}; "
             f"top-left corner should be '{g['top_left_corner']}'\n")
    c = s["counts"]
    L.append(f"Cells: {c['full']} full + {c['half']} half. Pattern inserts: {c['pattern_inserts']} "
             f"({c['distinct_patterns']} patterns, {c['distinct_parts']} distinct pattern+colour+shape parts). "
             f"Background inserts: {c['background_inserts']}. Plates: {c['plates']}.\n")
    ig = s["insert_geometry"]
    L.append(f"Inserts: opening {ig['opening_tip_to_tip_mm']} mm tip to tip, clearance {ig['clearance_mm']} mm per edge, "
             f"strips {ig['strip_mm']} mm, depth {ig['insert_depth_mm']} mm; backgrounds {ig['background_thickness_mm']} mm thick. "
             "Print one of each first and adjust clearance before committing.\n")
    if s.get("fidelity"):
        fs = s["fidelity"]
        L.append(f"Fidelity (viewing-distance CIELAB error, lower is better): mean {fs['mean_dE']}, p90 {fs['p90_dE']}; "
                 f"lightness spread plan {fs['L_std_plan']} vs source {fs['L_std_source']}.")
    L.append("Backgrounds: " + ", ".join(f"{b['name']} {b['hex']}" for b in s["backgrounds_used"]) + "\n")
    L.append("## Patterns used\n")
    if any(v["id"].startswith("ks") for v in s["patterns_used"]):
        glue = [v["id"] for v in s["patterns_used"] if v.get("note") == "needs glue"]
        L.append("Includes Paper View insert designs (ks*): for your own prints only, they derive from his licensed files."
                 + (f" **{', '.join(glue)} are loose multi-part designs and need glue.**" if glue else "") + "\n")
    for v in s["patterns_used"]:
        flag = "" if v.get("ok") else "  **CHECK: not a single connected piece touching all three edges**"
        L.append(f"- `{v['id']}` {v['name']} ({v['source']}){' - ' + v['note'] if v.get('note') else ''}{flag}")
    L.append(f"\n## Filament per colour (estimate, solid volume x 0.85 x 1.24 g/cm3). Colours picked from: {s['filament_set']}\n")
    L.append("| colour | hex | pattern inserts | background inserts | grams |\n|---|---|---|---|---|")
    for name, e in s["material_estimate_g"].items():
        L.append(f"| {name} | {e['color']} | {e['pattern_inserts']} | {e['background_inserts']} | {e['grams']} |")
    L.append("\n## Insert counts per colour\n")
    for r in s["insert_counts"]:
        L.append(f"- **{r['color_name']}** {r['color']}: {r['instructions']}")
    if not s["insert_counts"]:
        L.append("- (no pattern inserts: background-only mosaic)")
    L.append("\n## Plates (one colour per plate)\n")
    L.append("| # | file | layer | colour | parts | codes on the plate |\n|---|---|---|---|---|---|")
    for p in s["plates"]:
        L.append(f"| {p['plate']} | {p['name']} | {p['layer']} | {p['color_name']} | {p['parts']} | "
                 + ", ".join(f"{k} x{v}" for k, v in p["codes"].items()) + " |")
    if s["failed_parts"]:
        L.append("\nParts that could not be generated: " + ", ".join(s["failed_parts"]))
    L.append("\n## Files\n")
    L.append("- preview.png and plan.svg (or preview.svg): the finished panel with the insert shapes; the SVG zooms without loss")
    L.append("- assembly_sheet.pdf: printable guide; page 1 legend (letter = pattern, digit = filament), then the panel "
             "in strips of 8 columns with every cell labelled, e.g. B3 = pattern B in filament 3")
    L.append("- bag_labels.pdf: one printable label per kind of part (code, filament, counts, plates); plate_codes.csv: codes on each plate")
    L.append("- assembly_map.txt: the same as a pick list, one line per column; assembly_map.csv: per-cell table")
    L.append("- plates/*.3mf, *.stl, *.svg: one file per plate; open in Bambu/Orca Studio, set the filament, print")
    L.append("- summary.json: everything above, machine-readable\n")
    return "\n".join(L)
