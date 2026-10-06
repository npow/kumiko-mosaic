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
    return (cell.bg_color or background_color), (cell.color if cell.pattern else None)


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
    # background filaments: lowercase letter suffix, omitted for the most common one
    bgs: Dict[str, int] = {}
    for c in grid.cells:
        if c.bg_color:
            bgs[c.bg_color] = bgs.get(c.bg_color, 0) + 1
    bg_order = sorted(bgs, key=lambda h: -bgs[h])
    bg_suffix = {h: ("" if i == 0 else "abcdefgh"[i - 1]) for i, h in enumerate(bg_order)}
    codes = {}
    for c in grid.cells:
        base = f"{pmap[c.pattern]}{cmap[c.color]}" if c.pattern else "-"
        codes[(c.col, c.row)] = base + (bg_suffix.get(c.bg_color, "") if c.bg_color else "")
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
    bgs = {}
    for c in grid.cells:
        if c.bg_color:
            bgs.setdefault(c.bg_color, [c.bg_name or c.bg_color, 0])
            bgs[c.bg_color][1] += 1
    if len(bgs) > 1:
        y += 70
        d.text((120, y), "Background filaments (suffix letter; none = the most common)", fill="black", font=f_m)
        y += 60
        order = sorted(bgs, key=lambda h: -bgs[h][1])
        for i, hx in enumerate(order):
            d.rectangle([120, y, 220, y + 50], fill=hex_to_rgb(hx), outline="black")
            suf = "(no suffix)" if i == 0 else f"suffix '{'abcdefgh'[i-1]}'"
            d.text((250, y + 4), f"{suf}  =  {bgs[hx][0]}  {hx}  x{bgs[hx][1]}", fill="black", font=f_s)
            y += 60
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
    bgs = {}
    for c in grid.cells:
        if c.bg_color:
            bgs.setdefault(c.bg_color, [c.bg_name or c.bg_color, 0])
            bgs[c.bg_color][1] += 1
    order = sorted(bgs, key=lambda h: -bgs[h][1])
    lines = ["Codes: letter = pattern, digit = strip filament, '-' = background only"
             + ("; lowercase suffix = background filament (none = " + bgs[order[0]][0] + ")" if len(order) > 1 else "") + ".",
             "Patterns: " + ", ".join(f"{v}={k}" for k, v in pmap.items()),
             "Filaments: " + ", ".join(f"{i+1}={n} {hx}" for i, (hx, n) in enumerate(cols))]
    if len(order) > 1:
        lines.append("Backgrounds: " + ", ".join(f"{'abcdefgh'[i-1] if i else '(none)'}={bgs[h][0]} {h}" for i, h in enumerate(order)))
    lines.append("")
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


# ---- compact, zoomable SVG ------------------------------------------------------------------------

def svg_compact(grid: Grid, *, background_color: str = "#000000", frame_color: str = "#1A1A1A",
                strip_mm: float = 2.0) -> str:
    """Resolution-independent plan: one <path> per pattern in <defs>, one <use> with a transform per
    cell, backgrounds merged into one path per colour, lattice bars as a single stroked path.
    ~150 bytes per cell (a 60-column panel is under a megabyte; gzip makes it ~100 KB)."""
    s = grid.spec
    m = s.mitsuke
    pad = s.border + m
    Wl, Hl = grid.lattice_width, grid.lattice_height
    f = lambda v: f"{v:.1f}".rstrip("0").rstrip(".") if abs(v) >= 0.05 else "0"  # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-pad:.1f} {-pad:.1f} {Wl + 2 * pad:.1f} {Hl + 2 * pad:.1f}">']
    pats = sorted({c.pattern for c in grid.cells if c.pattern})
    out.append("<defs>")
    shapes = {}
    for p in pats:
        shapes[p] = inserts.canonical_polygon(p, s.inner_side, strip_mm)
        out.append(f'<path id="{p}" fill-rule="evenodd" d="{inserts.polygon_to_svg_paths(shapes[p], nd=2)}"/>')
    out.append("</defs>")
    out.append(f'<rect x="{-pad:.1f}" y="{-pad:.1f}" width="{Wl + 2 * pad:.1f}" height="{Hl + 2 * pad:.1f}" fill="{frame_color}"/>')
    # backgrounds, one path per colour
    bgs = {}
    for c in grid.cells:
        d = "M" + "L".join(f"{f(x)},{f(y)}" for x, y in c.poly) + "Z"
        bgs.setdefault(c.bg_color or background_color, []).append(d)
    for col, ds in bgs.items():
        out.append(f'<path fill="{col}" d="{"".join(ds)}"/>')
    # strips: full cells via <use>, half cells as explicit clipped paths
    by_col = {}
    for c in grid.cells:
        if c.pattern:
            by_col.setdefault(c.color, []).append(c)
    for col, cells in by_col.items():
        out.append(f'<g fill="{col}">')
        for c in cells:
            a, b, d_, e, x0, y0 = _cell_transform(c, grid)
            if not c.is_half:
                out.append(f'<use href="#{c.pattern}" transform="matrix({f(a)} {f(d_)} {f(b)} {f(e)} {x0:.1f} {y0:.1f})"/>')
            else:
                shape = _half_mask(c, grid, affine_transform(shapes[c.pattern], [a, b, d_, e, x0, y0]))
                dd = inserts.polygon_to_svg_paths(shape, nd=1)
                if dd:
                    out.append(f'<path fill-rule="evenodd" d="{dd}"/>')
        out.append("</g>")
    bars = "".join("M" + "L".join(f"{f(x)},{f(y)}" for x, y in c.poly) + "Z" for c in grid.cells)
    out.append(f'<path fill="none" stroke="{frame_color}" stroke-width="{m}" stroke-linejoin="round" d="{bars}"/>')
    out.append(f'<path fill="{frame_color}" fill-rule="evenodd" d="M{-pad:.1f},{-pad:.1f}H{Wl + pad:.1f}V{Hl + pad:.1f}H{-pad:.1f}ZM0,0H{Wl:.1f}V{Hl:.1f}H0Z"/>')
    out.append("</svg>")
    return "".join(out)


# ---- bag labels and per-plate codes ---------------------------------------------------------------

def _bg_suffix_map(grid: Grid) -> Dict[str, str]:
    bgs: Dict[str, int] = {}
    for c in grid.cells:
        if c.bg_color:
            bgs[c.bg_color] = bgs.get(c.bg_color, 0) + 1
    order = sorted(bgs, key=lambda h: -bgs[h])
    return {h: ("" if i == 0 else "abcdefgh"[i - 1]) for i, h in enumerate(order)}


def part_code(grid: Grid, layer: str, color: str, pattern: Optional[str]) -> str:
    """Bag code of a physical part: pattern inserts 'B3' (letter = pattern, digit = strip filament);
    background inserts 'BG', 'BG a', 'BG b' (suffix matches the lowercase letter in cell codes)."""
    pmap, cols, _ = assembly_codes(grid)
    if layer == "pattern":
        cmap = {hx: str(i + 1) for i, (hx, _) in enumerate(cols)}
        return f"{pmap[pattern]}{cmap[color]}"
    suf = _bg_suffix_map(grid).get(color, "")
    return "BG" + (f" {suf}" if suf else "")


def _ranges(nums) -> str:
    nums = sorted(set(nums))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(out)


def plate_codes(grid: Grid, plates) -> List[dict]:
    """For each plate: which bag codes it holds and how many of each."""
    rows = []
    for p in plates:
        counts: Dict[str, int] = {}
        for pl in p.placements:
            k = pl.part
            code = part_code(grid, k.layer, k.color, k.pattern)
            counts[code] = counts.get(code, 0) + 1
        rows.append({"plate": p.index, "file": p.name(), "color_name": p.color_name, "layer": p.layer, "codes": counts})
    return rows


def _fit(d, text: str, size: int, maxw: int, bold: bool = False):
    """Largest font (down to 60% of size) in which text fits maxw; ellipsis if still too wide."""
    for sz in range(size, int(size * 0.6) - 1, -2):
        f = _font(sz)
        if d.textlength(text, font=f) <= maxw:
            return text, f
    f = _font(int(size * 0.6))
    while len(text) > 4 and d.textlength(text + "...", font=f) > maxw:
        text = text[:-1]
    return text + "...", f


def bag_labels(grid: Grid, bom, plates, strip_mm: float = 2.0, per_page=(3, 8)) -> List[Image.Image]:
    """Printable A4 pages (300 dpi) of labels, one per kind of part: colour swatch, pattern thumbnail,
    code, filament name, counts (full / half L / half R) and the plates it is printed on."""
    groups: Dict[tuple, dict] = {}
    for k, q in bom.items():
        g = groups.setdefault((k.layer, k.color, k.pattern),
                              {"name": k.color_name, "full": 0, "half_L": 0, "half_R": 0, "plates": set()})
        g[k.shape] += q
    for p in plates:
        for pl in p.placements:
            k = pl.part
            groups[(k.layer, k.color, k.pattern)]["plates"].add(p.index)
    items = sorted(groups.items(), key=lambda kv: (kv[0][0] != "pattern", part_code(grid, *kv[0])))
    cols_n, rows_n = per_page
    PW, PH = 2480, 3508
    m = 90
    lw, lh = (PW - 2 * m) // cols_n, (PH - 2 * m) // rows_n
    f_code, f_name, f_sm = _font(int(lh * 0.30)), _font(int(lh * 0.115)), _font(int(lh * 0.095))
    side = grid.spec.inner_side
    tri_h = side * SQRT3 / 2
    pages: List[Image.Image] = []
    for start in range(0, len(items), cols_n * rows_n):
        pg = Image.new("RGB", (PW, PH), "white")
        d = ImageDraw.Draw(pg)
        for n, ((layer, color, pattern), g) in enumerate(items[start:start + cols_n * rows_n]):
            x0 = m + (n % cols_n) * lw
            y0 = m + (n // cols_n) * lh
            d.rectangle([x0 + 6, y0 + 6, x0 + lw - 6, y0 + lh - 6], outline=(150, 150, 150), width=3)
            # thumbnail: the insert in its filament colour on a dark triangle
            tsz = int(lh * 0.62)
            sc = tsz * 0.92 / side
            cx, cy = x0 + 30 + tsz // 2, y0 + 30 + int(tsz * 0.70)
            P = lambda x, y: (cx + x * sc, cy - y * sc)  # noqa: E731
            d.polygon([P(-side / 2, -tri_h / 3), P(side / 2, -tri_h / 3), P(0, 2 * tri_h / 3)], fill=(25, 25, 25))
            if layer == "pattern":
                shape = inserts.canonical_polygon(pattern, side, strip_mm)
                for gg in (shape.geoms if shape.geom_type == "MultiPolygon" else [shape]):
                    d.polygon([P(x, y) for x, y in gg.exterior.coords], fill=hex_to_rgb(color))
                    for hole in gg.interiors:
                        d.polygon([P(x, y) for x, y in hole.coords], fill=(25, 25, 25))
            else:
                d.polygon([P(-side / 2 + 3, -tri_h / 3 + 2), P(side / 2 - 3, -tri_h / 3 + 2), P(0, 2 * tri_h / 3 - 3)],
                          fill=hex_to_rgb(color))
            tx = x0 + 60 + tsz
            avail = x0 + lw - 20 - tx
            d.text((tx, y0 + 24), part_code(grid, layer, color, pattern), fill="black", font=f_code)
            d.rectangle([tx, y0 + 24 + int(lh * 0.36), tx + 50, y0 + 24 + int(lh * 0.36) + 36], fill=hex_to_rgb(color), outline="black", width=2)
            t, f = _fit(d, g["name"], int(lh * 0.115), avail - 62)
            d.text((tx + 60, y0 + 24 + int(lh * 0.36)), t, fill="black", font=f)
            if layer == "background":
                what = "background insert"
            else:
                info = inserts.catalogue().get(pattern)
                what = info.name if info else pattern
            t, f = _fit(d, what, int(lh * 0.095), avail)
            d.text((tx, y0 + 24 + int(lh * 0.50)), t, fill=(70, 70, 70), font=f)
            parts = [f"{g['full']} full"] + ([f"{g['half_L']} half L"] if g["half_L"] else []) + ([f"{g['half_R']} half R"] if g["half_R"] else [])
            t, f = _fit(d, ", ".join(parts), int(lh * 0.115), avail, True)
            d.text((tx, y0 + 24 + int(lh * 0.62)), t, fill="black", font=f)
            t, f = _fit(d, f"plates {_ranges(g['plates'])}", int(lh * 0.095), avail)
            d.text((tx, y0 + 24 + int(lh * 0.79)), t, fill=(70, 70, 70), font=f)
        pages.append(pg)
    return pages
