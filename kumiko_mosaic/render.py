"""Previews (SVG with real insert silhouettes, PNG) and assembly maps (CSV, text)."""
from __future__ import annotations

import csv
import io
import math
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont
from shapely.affinity import affine_transform

from . import inserts
from .grid import Grid, SQRT3
from .imagemap import hex_to_rgb, luminance
from .patterns import pattern_label


def _text_color(bg_hex: str) -> str:
    return "#000000" if luminance(hex_to_rgb(bg_hex)) > 0.25 else "#FFFFFF"


def _shade(hex_color: str, factor: float) -> str:
    r, g, b = hex_to_rgb(hex_color)
    return "#%02X%02X%02X" % (int(r * factor), int(g * factor), int(b * factor))


def cell_colors(cell, color_layer: str, background_color: str, pattern_color: str):
    """(background colour, insert colour or None) as printed."""
    if color_layer == "background":
        return cell.color, (pattern_color if cell.pattern else None)
    if color_layer == "both":
        return cell.color, (_shade(cell.color, 0.8) if cell.pattern else None)
    return background_color, (cell.color if cell.pattern else None)


def _cell_transform(cell, grid: Grid):
    """Affine map from the canonical apex-up insert frame (mm, centroid at origin, y up) to the
    cell's panel coordinates (y down). Full cells rotate +-90 degrees; half cells use the
    rotation of the full triangle they were cut from."""
    s = grid.spec.inner_side
    p = grid.spec.pitch
    if cell.is_half:
        # Reconstruct the parent full triangle from the polygon itself (label independent): the
        # vertical leg lies on the column edge that carries the parent's base.
        xs = [x for x, _ in cell.poly]
        ys = [y for _, y in cell.poly]
        base_x = next(x for x in xs if xs.count(x) == 2)
        apex_x = next(x for x in xs if xs.count(x) == 1)
        if cell.side == "top":
            apex = (apex_x, max(ys) - p / 2)
        else:
            apex = (apex_x, min(ys) + p / 2)
        cx = (2 * base_x + apex[0]) / 3.0
        cy = apex[1]
        pointing = "right" if apex[0] > base_x else "left"
    else:
        cx, cy = cell.centroid
        pointing = cell.orientation
    ang = -math.pi / 2 if pointing == "right" else math.pi / 2  # canonical apex +y -> +x / -x
    # y flip (canonical y up -> panel y down) combined with rotation
    c, s_ = math.cos(ang), math.sin(ang)
    # canonical (u,v) -> rotate -> (u c - v s, u s + v c) -> flip y -> panel
    return [c, -s_, -s_, -c, cx, cy]  # shapely affine: x' = a u + b v + xoff ; y' = d u + e v + yoff


def _half_mask(cell, grid: Grid, shape):
    """Clip a half-cell silhouette to its cell (strips may run to the bar centreline)."""
    from shapely.geometry import Polygon
    return shape.intersection(Polygon(cell.poly).buffer(grid.spec.mitsuke / 2.0, join_style="mitre"))


def svg_preview(grid: Grid, *, color_layer: str = "pattern", background_color: str = "#000000",
                pattern_color: str = "#D2AC86", frame_color: str = "#D2AC86", labels: bool = False,
                silhouettes: bool = True, strip_mm: float = 2.0, scale: float = 2.0,
                title: Optional[str] = None) -> str:
    s = grid.spec
    m = s.mitsuke
    pad = s.border + m
    W = grid.lattice_width + 2 * pad
    H = grid.lattice_height + 2 * pad
    out = io.StringIO()
    out.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W*scale:.1f}" height="{H*scale:.1f}" '
              f'viewBox="{-pad:.2f} {-pad:.2f} {W:.2f} {H:.2f}" font-family="sans-serif">\n')
    out.write(f'<rect x="{-pad:.2f}" y="{-pad:.2f}" width="{W:.2f}" height="{H:.2f}" fill="{frame_color}"/>\n')
    out.write(f'<rect x="0" y="0" width="{grid.lattice_width:.2f}" height="{grid.lattice_height:.2f}" fill="{background_color}"/>\n')
    # backgrounds
    for c in grid.cells:
        bg, _ = cell_colors(c, color_layer, background_color, pattern_color)
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in c.poly)
        out.write(f'<polygon points="{pts}" fill="{bg}"/>\n')
    # inserts
    cache: Dict[str, object] = {}
    for c in grid.cells:
        _, ins = cell_colors(c, color_layer, background_color, pattern_color)
        if not ins:
            continue
        if silhouettes:
            if c.pattern not in cache:
                cache[c.pattern] = inserts.canonical_polygon(c.pattern, s.inner_side, strip_mm)
            shape = affine_transform(cache[c.pattern], _cell_transform(c, grid))
            if c.is_half:
                shape = _half_mask(c, grid, shape)
            d = inserts.polygon_to_svg_paths(shape, nd=2)
            out.write(f'<path d="{d}" fill="{ins}" fill-rule="evenodd"/>\n')
        else:
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in c.poly)
            out.write(f'<polygon points="{pts}" fill="{ins}"/>\n')
    # bars on top
    for c in grid.cells:
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in c.poly)
        out.write(f'<polygon points="{pts}" fill="none" stroke="{frame_color}" stroke-width="{m}" stroke-linejoin="round"/>\n')
    # border ring on top (covers strip ends that run under the outer bars)
    Wl, Hl = grid.lattice_width, grid.lattice_height
    out.write(f'<path fill="{frame_color}" fill-rule="evenodd" d="M{-pad:.2f},{-pad:.2f} H{Wl+pad:.2f} V{Hl+pad:.2f} H{-pad:.2f} Z '
              f'M0,0 H{Wl:.2f} V{Hl:.2f} H0 Z"/>\n')
    if labels:
        fs = max(2.5, s.pitch * 0.14)
        for c in grid.cells:
            if c.is_half:
                continue
            cx, cy = c.centroid
            bg, ins = cell_colors(c, color_layer, background_color, pattern_color)
            out.write(f'<text x="{cx:.2f}" y="{cy + fs*0.35:.2f}" font-size="{fs:.2f}" text-anchor="middle" '
                      f'fill="{_text_color(ins or bg)}" stroke="{ins or bg}" stroke-width="0.6" paint-order="stroke">{pattern_label(c.pattern)}</text>\n')
    if title:
        out.write(f'<text x="{-pad+2:.2f}" y="{-pad+6:.2f}" font-size="5" fill="{_text_color(frame_color)}">{title}</text>\n')
    out.write("</svg>\n")
    return out.getvalue()


def png_preview(grid: Grid, *, color_layer: str = "pattern", background_color: str = "#000000",
                pattern_color: str = "#D2AC86", frame_color: str = "#D2AC86", labels: bool = True,
                silhouettes: bool = True, strip_mm: float = 2.0, px_per_mm: float = 3.0) -> Image.Image:
    s = grid.spec
    m = s.mitsuke
    pad = s.border + m
    W = int((grid.lattice_width + 2 * pad) * px_per_mm) + 1
    H = int((grid.lattice_height + 2 * pad) * px_per_mm) + 1
    img = Image.new("RGB", (W, H), hex_to_rgb(frame_color))
    d = ImageDraw.Draw(img)

    def P(x, y):
        return ((x + pad) * px_per_mm, (y + pad) * px_per_mm)

    d.rectangle([P(0, 0), P(grid.lattice_width, grid.lattice_height)], fill=hex_to_rgb(background_color))
    for c in grid.cells:
        bg, _ = cell_colors(c, color_layer, background_color, pattern_color)
        d.polygon([P(x, y) for x, y in c.poly], fill=hex_to_rgb(bg))
    cache: Dict[str, object] = {}
    for c in grid.cells:
        _, ins = cell_colors(c, color_layer, background_color, pattern_color)
        if not ins:
            continue
        if silhouettes:
            if c.pattern not in cache:
                cache[c.pattern] = inserts.canonical_polygon(c.pattern, s.inner_side, strip_mm)
            shape = affine_transform(cache[c.pattern], _cell_transform(c, grid))
            if c.is_half:
                shape = _half_mask(c, grid, shape)
            geoms = list(shape.geoms) if shape.geom_type == "MultiPolygon" else [shape]
            for g in geoms:
                if g.is_empty:
                    continue
                d.polygon([P(x, y) for x, y in g.exterior.coords], fill=hex_to_rgb(ins))
                for hole in g.interiors:
                    hx = [P(x, y) for x, y in hole.coords]
                    # hole colour = background of the cell
                    bg, _ = cell_colors(c, color_layer, background_color, pattern_color)
                    d.polygon(hx, fill=hex_to_rgb(bg))
        else:
            d.polygon([P(x, y) for x, y in c.poly], fill=hex_to_rgb(ins))
    bar = max(1, int(round(m * px_per_mm)))
    for c in grid.cells:
        pts = [P(x, y) for x, y in c.poly]
        d.line(pts + [pts[0]], fill=hex_to_rgb(frame_color), width=bar, joint="curve")
    # border ring on top
    Wl, Hl = grid.lattice_width, grid.lattice_height
    fc = hex_to_rgb(frame_color)
    d.rectangle([P(-pad, -pad), P(Wl + pad, 0)], fill=fc)
    d.rectangle([P(-pad, Hl), P(Wl + pad, Hl + pad)], fill=fc)
    d.rectangle([P(-pad, -pad), P(0, Hl + pad)], fill=fc)
    d.rectangle([P(Wl, -pad), P(Wl + pad, Hl + pad)], fill=fc)
    if labels and s.pitch * px_per_mm >= 40:
        size = max(8, int(s.pitch * 0.14 * px_per_mm))
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size)
        except Exception:
            font = ImageFont.load_default()
        for c in grid.cells:
            if c.is_half:
                continue
            bg, ins = cell_colors(c, color_layer, background_color, pattern_color)
            d.text(P(*c.centroid), pattern_label(c.pattern), fill=hex_to_rgb(_text_color(ins or bg)), font=font,
                   anchor="mm", stroke_width=2, stroke_fill=hex_to_rgb(ins or bg))
    return img


def assembly_rows(grid: Grid) -> List[dict]:
    rows = []
    _, _, codes = assembly_codes(grid)
    for c in sorted(grid.cells, key=lambda c: (c.col, c.row)):
        rows.append({
            "col": c.col + 1, "cell": c.row + 1, "code": codes[(c.col, c.row)], "orientation": c.orientation,
            "half_kind": c.half or "", "edge": c.side or "",
            "pattern": c.pattern or "", "color": c.color, "color_name": c.color_name,
            "x_mm": round(c.centroid[0], 1), "y_mm": round(c.centroid[1], 1),
        })
    return rows


def assembly_csv(grid: Grid) -> str:
    rows = assembly_rows(grid)
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


def assembly_text(grid: Grid) -> str:
    """One line per column (left to right); cells top to bottom. '<' / '>' = apex direction,
    [..:L] / [..:R] = half cells."""
    lines = []
    by_col: Dict[int, list] = {}
    for c in grid.cells:
        by_col.setdefault(c.col, []).append(c)
    for col in sorted(by_col):
        toks = []
        for c in sorted(by_col[col], key=lambda c: c.row):
            t = f"{pattern_label(c.pattern)}/{c.color_name}"
            toks.append(f"[{t}:{c.half}]" if c.is_half else t + ("<" if c.orientation == "left" else ">"))
        lines.append(f"col {col+1:3d} (top->bottom): " + " ".join(toks))
    return "\n".join(lines) + "\n"


def compare_image(fitted: Image.Image, preview: Image.Image, height: int = 700) -> Image.Image:
    """Source (as fitted to the lattice) next to the planned panel, same height."""
    a = fitted.convert("RGB")
    b = preview.convert("RGB")
    a = a.resize((max(1, int(a.width * height / a.height)), height), Image.LANCZOS)
    b = b.resize((max(1, int(b.width * height / b.height)), height), Image.LANCZOS)
    out = Image.new("RGB", (a.width + b.width + 12, height), "white")
    out.paste(a, (0, 0))
    out.paste(b, (a.width + 12, 0))
    return out


# ---- assembly guide -------------------------------------------------------------------------------

LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def assembly_codes(grid: Grid):
    """Short codes: pattern -> letter, filament -> digit. Cell code 'B3' = pattern B in colour 3;
    '-' = background only. Returns (pattern_map, color_map, {cell: code})."""
    pats = sorted({c.pattern for c in grid.cells if c.pattern})
    pmap = {p: LETTERS[i] for i, p in enumerate(pats)}
    cols = []
    for c in grid.cells:
        if c.pattern and (c.color, c.color_name) not in cols:
            cols.append((c.color, c.color_name))
    cmap = {hx: str(i + 1) for i, (hx, _) in enumerate(cols)}
    codes = {}
    for c in grid.cells:
        codes[(c.col, c.row)] = f"{pmap[c.pattern]}{cmap[c.color]}" if c.pattern else "-"
    return pmap, cols, codes


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def assembly_sheet(grid: Grid, *, color_layer: str, background_color: str, pattern_color: str,
                   frame_color: str, cols_per_page: int = 8, cell_px: int = 110) -> List[Image.Image]:
    """Printable pages: page 1 = legend + overview, then the panel in column strips with every
    cell labelled by its code and column numbers along the top and bottom."""
    pmap, cols, codes = assembly_codes(grid)
    s = grid.spec
    scale = cell_px / s.col_width            # px per mm
    pages: List[Image.Image] = []
    page_w, page_h = 2480, 3508              # A4 at 300 dpi, portrait

    # --- legend page
    pg = Image.new("RGB", (page_w, page_h), "white")
    d = ImageDraw.Draw(pg)
    f_h, f_m, f_s = _font(70), _font(44), _font(34)
    d.text((120, 100), "Kumiko mosaic: assembly guide", fill="black", font=f_h)
    y = 230
    d.text((120, y), f"Panel: {grid.cols} columns x {grid.rows} pitches, {grid.n_full} full + {grid.n_half} half cells.",
           fill="black", font=f_s)
    d.text((120, y + 46), "Columns are numbered left to right, cells top to bottom within a column.", fill="black", font=f_s)
    y += 130
    d.text((120, y), "Patterns (letter)", fill="black", font=f_m)
    y += 70
    for p, letter in pmap.items():
        from . import inserts as _ins
        shape = _ins.canonical_polygon(p, s.inner_side, 2.0)
        sz = 150
        tri_img = Image.new("RGB", (sz, sz), "white")
        td = ImageDraw.Draw(tri_img)
        sc = sz * 0.9 / s.inner_side
        h = s.inner_side * SQRT3 / 2

        def P(x, yy):
            return (sz / 2 + x * sc, sz * 0.8 - yy * sc)
        td.polygon([P(-s.inner_side / 2, -h / 3), P(s.inner_side / 2, -h / 3), P(0, 2 * h / 3)], fill=(40, 40, 40))
        geoms = list(shape.geoms) if shape.geom_type == "MultiPolygon" else [shape]
        for g in geoms:
            td.polygon([P(x, yy) for x, yy in g.exterior.coords], fill=(210, 172, 134))
            for hole in g.interiors:
                td.polygon([P(x, yy) for x, yy in hole.coords], fill=(40, 40, 40))
        pg.paste(tri_img, (120, y))
        d.text((300, y + 40), f"{letter}  =  {p}", fill="black", font=f_m)
        y += sz + 20
    y += 30
    d.text((120, y), "Filaments (digit)", fill="black", font=f_m)
    y += 70
    for i, (hx, name) in enumerate(cols):
        d.rectangle([120, y, 220, y + 60], fill=hex_to_rgb(hx), outline="black")
        d.text((250, y + 8), f"{i + 1}  =  {name}  {hx}", fill="black", font=f_m)
        y += 80
    bg_label = background_color if color_layer == "pattern" else "cell colour"
    d.text((120, y + 20), f"'-' = no pattern insert (background {bg_label} only). Every cell also gets a background insert.",
           fill="black", font=f_s)
    # overview thumbnail
    ov = png_preview(grid, color_layer=color_layer, background_color=background_color, pattern_color=pattern_color,
                     frame_color=frame_color, labels=False, px_per_mm=1.5)
    ov.thumbnail((page_w - 240, page_h - y - 200))
    pg.paste(ov, (120, y + 120))
    pages.append(pg)

    # --- column strip pages
    f_code = _font(int(cell_px * 0.28))
    f_small = _font(int(cell_px * 0.2))
    f_num = _font(int(cell_px * 0.3))
    f_title = _font(int(cell_px * 0.36))
    margin = int(cell_px * 1.2)
    for c0 in range(0, grid.cols, cols_per_page):
        c1 = min(grid.cols, c0 + cols_per_page)
        strip_w = int((c1 - c0) * s.col_width * scale) + 2 * margin
        strip_h = int(grid.lattice_height * scale) + 2 * margin
        pg = Image.new("RGB", (strip_w, strip_h), "white")
        d = ImageDraw.Draw(pg)
        x_off = margin - c0 * s.col_width * scale
        y_off = margin
        for c in grid.cells:
            if not (c0 <= c.col < c1):
                continue
            pts = [(x * scale + x_off, yy * scale + y_off) for x, yy in c.poly]
            bg, ins = cell_colors(c, color_layer, background_color, pattern_color)
            fill = ins or bg
            d.polygon(pts, fill=hex_to_rgb(fill), outline=hex_to_rgb(frame_color), width=3)
            code = codes[(c.col, c.row)]
            cx, cy = c.centroid
            font = f_small if c.is_half else f_code
            tx = cx * scale + x_off
            ty = cy * scale + y_off
            if c.is_half:  # push the label into the wide end of the half cell
                ty += (8 if c.side == "top" else -8) * scale * (s.pitch / 50)
            d.text((tx, ty), code, fill=hex_to_rgb(_text_color(fill)), font=font, anchor="mm",
                   stroke_width=2, stroke_fill=hex_to_rgb(fill))
        for col in range(c0, c1):
            x = (col + 0.5) * s.col_width * scale + x_off
            d.text((x, margin - cell_px * 0.35), str(col + 1), fill="black", font=f_num, anchor="mm")
            d.text((x, strip_h - margin + cell_px * 0.35), str(col + 1), fill="black", font=f_num, anchor="mm")
        d.text((margin * 0.4, cell_px * 0.3), f"Columns {c0 + 1}-{c1} of {grid.cols}  (panel top at top)",
               fill="black", font=f_title, anchor="lm")
        pages.append(pg)
    return pages


def assembly_pick_list(grid: Grid) -> str:
    """Text pick list: one line per column, codes top to bottom, plus totals per code."""
    pmap, cols, codes = assembly_codes(grid)
    lines = ["Codes: letter = pattern, digit = filament, '-' = background only.",
             "Patterns: " + ", ".join(f"{v}={k}" for k, v in pmap.items()),
             "Filaments: " + ", ".join(f"{i+1}={n} {hx}" for i, (hx, n) in enumerate(cols)), ""]
    by_col: Dict[int, list] = {}
    for c in grid.cells:
        by_col.setdefault(c.col, []).append(c)
    for col in sorted(by_col):
        cs = sorted(by_col[col], key=lambda c: c.row)
        toks = []
        for c in cs:
            code = codes[(c.col, c.row)]
            toks.append(f"[{code}]" if c.is_half else code)
        lines.append(f"col {col+1:3d}: " + " ".join(toks))
    totals: Dict[str, int] = {}
    for code in codes.values():
        totals[code] = totals.get(code, 0) + 1
    lines += ["", "Totals: " + ", ".join(f"{k} x{v}" for k, v in sorted(totals.items()))]
    return "\n".join(lines) + "\n"
