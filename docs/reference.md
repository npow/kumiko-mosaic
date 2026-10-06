# Reference

Detailed options and design notes. For a quick start see the [README](../README.md).

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

## Run it permanently

`scripts/install_service.sh --https-port 8444` installs a systemd user service (starts at boot,
restarts after a crash) and publishes the app on your tailnet over HTTPS with `tailscale serve`.
Without `--https-port` it only installs the service. Settings: `PORT`, `KUMIKO_MAX_JOBS` (plans
running at once, default 2) and `KUMIKO_RUN_TTL_HOURS` (how long results are kept, default 24).
Logs: `journalctl --user -u kumiko-mosaic -f`.

## Use: web UI

```bash
./serve.sh            # binds 0.0.0.0:8000 (reachable on a tailnet); PORT=8080 ./serve.sh
# or: uvicorn kumiko_mosaic.web:app --port 8000   (localhost only)
```

The gallery of before/after examples is served at `/gallery`.

Upload the image, give one size constraint (outer width in mm is the usual one), paste the
filaments you own as `name=#hex` lines, choose a pattern mode, click **Plan it**. Everything
lands in `runs/<id>/` and in the zip download.

## Use: command line

```bash
python -m kumiko_mosaic.cli --list-patterns
python -m kumiko_mosaic.cli photo.jpg out/ --width-mm 800 \
    --palette "black=#000000" "latte=#D2AC86" "marine blue=#1F4E9A" "dark red=#B5402B" \
    --pattern-mode single:y
```

Every run also writes `compare.jpg`, the source next to the plan.

Outputs in `out/`: `REPORT.md`, `preview.png/.svg`, `compare.jpg`, `assembly_sheet.pdf`,
`assembly_map.txt/.csv`, `summary.json`, `plates/<plate>.3mf|.stl|.svg`.

### Keeping track of what goes where

Every cell gets a short code: a letter for the pattern and a digit for the filament, e.g. `B3`;
`-` means background only. `assembly_sheet.pdf` page 1 is the legend (pattern silhouettes,
filament swatches) with a panel overview; the following pages show the panel in strips of
8 columns at a size where every code is readable, with column numbers top and bottom.
`assembly_map.txt` is the same as a pick list, one line per column, cells top to bottom, half
cells in brackets, plus totals per code. Sorting the printed parts into labelled bags by code
first (one bag per pattern+filament) makes the assembly a lookup, not a puzzle.

### Sizing (optional)

The image fixes the aspect ratio, so one number sets the size. With nothing given the panel is
40 columns wide, which is enough cells for a photo to read. At 50 mm pitch that is 1.75 m
wide; lower `--pitch` (his generator goes down to about 30 mm) for a smaller panel with the
same detail. Otherwise give one of:

| flag | meaning |
|---|---|
| `--width-mm W` / `--height-mm H` | largest frame whose finished outer size fits; other axis follows the image aspect |
| `--cols N` | columns across, each one triangle altitude (pitch x 0.866) wide |
| `--rows N` | pitches tall (one full triangle side per row) |
| `--max-cells N` | biggest frame with at most N triangle areas (= 2 x cols x rows) |
| `--measure lattice` | mm values refer to the bar-to-bar lattice instead of the outer edge |

`--pitch`, `--mitsuke`, `--border` are his Grid_Pitch, Grid_Thickness, Border_Thickness.
Smaller pitch = more pixels for the same wall size but many more inserts; the report shows
counts and grams so you can trade off.

### Colour (how the image is reproduced)

The **kumiko strips carry the colour** and the **background insert behind each cell** sets the
base tone. By default 3 background filaments are chosen per panel from the **whole filament catalogue**
(`--max-backgrounds 3`, `--background-set all`; `neutral` restricts them to greys and plans
faster) together with 8 strip filaments. Letting backgrounds be any colour is what makes
saturated subjects work: with greys only, a yellow taxi or a green frog comes out olive.
With black behind everything the panel could never be brighter than mid-grey (lines cover at
most ~half a cell); a white or grey background behind bright cells restores the full range.
The achievable cell colours are every (background, strip filament, pattern) triple:
coverage x strip + (1 - coverage) x background, mixed in linear light.

Every run reports a **fidelity score**: the plan and the source are both blurred to roughly
one pitch (what the eye sees from a few metres) and compared. Two numbers: the colour error
in CIELAB (lower is better) and SSIM of lightness (higher is better, 1 is identical structure).
On the sample images, 3 backgrounds instead of 1 cut the colour error by 15 to 40%.

**Assignment.** Each cell takes the achievable option (background, strip filament, pattern) that is
nearest to the cell's average colour (`--sampling mean`, default). The alternative is region
voting (`--sampling vote`, after Kopf and Lischinski's pixel-art abstraction): smooth the image,
quantise every pixel, take the majority per cell. Measured on six images with
`scripts/compare_variants.py`, averaging beats voting on both numbers (colour error 9.75 vs 11.13,
SSIM 0.496 vs 0.478), and looks cleaner now that a cell can choose among hundreds of options.
Other things that were tried and are off by default: unsharp masking (`--sharpen`, worse on every
image), error diffusion (`--dither`, colour error 9.75 to 9.47 but SSIM 0.496 to 0.471, so it trades
structure for smoothness), and a different smoothing radius for voting (4 to 20 mm changes the mean
error by under 0.3, with no consistent best value per image).

**Cropping.** `--crop left,top,right,bottom` (fractions of the image) or drag a box on the picture in
the web app. An automatic crop to the "salient" region was tried and removed: colour-distance
saliency cut off the toucan's body and the dark side of the Earth, so it was worse than no crop.

The density ladder is filtered per pitch so that **every opening stays at least `--min-hole`
(2.5 mm) wide** and no pattern covers more than 65% of the cell: lines must stay lines, not
merge into near-solid plates. The candidate list has about 30 line patterns (hemp leaf,
meshes, kagome, stripes, stars, hexagons, pinwheel, fans, rings...); at 50 mm pitch with 2 mm
strips 21 of them pass, from `y` at 17% to `mesh3ww` at 63% coverage (the four densest use 2.5 to 3.5 mm strips). `--max-patterns` then
picks the best few for the image.
Consequence: a cell can never be brighter than about half the filament colour over black, so
panels read darker and flatter than the photo. Levers: a lighter background filament
(`--background-color "dark grey=#4A4A4A"`), wider strips (`--strip 3`), or a smaller
`--min-hole`. Override the candidate list with `--pattern-mode auto:id,id,...`.

### Resolution: what it takes to look like the picture

| columns | cells (3:2 image) | reads as | width at 50 mm pitch | at 30 mm pitch |
|---|---|---|---|---|
| 40 | ~2,100 | shapes and colours from across the room | 1.75 m | 1.05 m |
| 60 | ~4,800 | clearly the picture | 2.6 m | 1.57 m |
| 80 | ~8,500 | close to the original | 3.5 m | 2.1 m |

Every cell is one insert plus one background triangle, so 60 columns means roughly 10,000
printed parts. Portraits and thin lines need the high end; bold, high-contrast subjects work
at the low end. Smaller pitch keeps the wall size down but shrinks the openings, so fewer
dense patterns survive the `--min-hole` test (at 30 mm only `y, asanoha, mesh2, step2`).

* `--max-patterns 4` (default): the tool tries every subset of the ladder of that size and keeps
  the one with the lowest total colour error, so a panel uses at most 4 distinct patterns.
  On Starry Night: 3 patterns cost almost nothing (mean error 8.2 vs 7.2 with all nine), 1 is
  noticeably worse (12.7). Fewer patterns = fewer kinds of parts to sort and place.
* Colours are **real filaments**. Without `--palette` the tool picks the best `--max-colors`
  from a purchasable catalogue (`--filament-set bambu`: Bambu Lab PLA Matte + Basic, 55 colours
  with the manufacturer's hex codes; `bambu-matte` / `bambu-basic` to restrict). The report
  names them, e.g. "Matte Marine Blue". `--palette "Matte Dark Red=#BB3D43" ...` lists the
  spools you already have instead.
* **Open database.** `--filament-set db` picks from about 1,300 distinct PLA and PLA+ colours from 55 brands,
  from [SpoolmanDB](https://github.com/Donkie/SpoolmanDB) (MIT; a filtered snapshot is in
  `kumiko_mosaic/data/`, refresh with `scripts/update_filament_db.py`). `--brands "Polymaker,eSun"` limits it,
  `--list-brands` shows them. Colour-shifting, dual-tone, sparkle, wood and carbon-fibre filaments are left
  out because their printed colour is not the single listed hex, and colours within a small difference of
  each other are listed once. On six images the mean colour error is 8.6 with all brands against 9.8 with
  the Bambu set; a single brand (Polymaker, eSun) is about as good as Bambu's 55. Without it, `--max-colors` filaments
  are chosen from the image, biased towards the vivid colours needed at full coverage.
* `--background-color` is the filament of the flat inserts behind everything (default black).
* `--frame-color` is the frame filament. **Print the frame dark** (default `#1A1A1A`): with
  Paper View's latte frame the bright lattice sits over every cell and flattens the picture
  badly; with a dark frame the same panel is clearly legible. The preview shows the difference.
* `--color-layer background --pattern-mode none` makes a flat triangle mosaic instead; `both`
  colours both layers.
* Images are auto-contrasted and slightly saturated before sampling (`--no-enhance` to skip).
* `--line-boost 4` (default) keeps thin, strongly contrasting features that plain averaging
  erases: if a small part of a cell differs sharply from the rest (a cable, a mast, an
  outline), the cell takes that colour, so the feature survives as a one-cell line. 0 turns it
  off, 8 is aggressive and can add speckle in textured areas. Only features with a coherent
  colour qualify (`--line-coherence`, default 34; lower it if textured areas get speckled).
  Checked on all five gallery images: bridge cables and Fuji's snow streaks appear, the
  sunflower and lighthouse are unchanged.
* `--dither` adds error diffusion between neighbouring triangles (`dither_strength`, default 0.3); it lowers the colour error slightly and the structure score more, so it is off by default.

Look at `examples/gallery/index.html` (also served at `/gallery` by the web app): before/after
for Starry Night, the Great Wave, Mona Lisa, Girl with a Pearl Earring and the Golden Gate
Bridge at 40 and 24 columns. Rebuild it with `python scripts/make_gallery.py`.
* `--color-layer both`, `--dither`, `--skip-background-matches` (no insert where the cell colour
  equals the background filament).

### Patterns

Ids from `--list-patterns`. Two sources:

* **procedural** (original, parametric): `y`, `y2`, `asanoha`, `asanoha2`, `asanoha3`,
  `rings2`, `rings3`, `hexagram`, `hexagram2`, `mesh2`, `mesh3`, `fan3`, `fan5`, `sunburst4`,
  `pinwheel`, `step2`, `hex`, `hex2`, `cross2`, `weave2`. Defined as centre-lines in
  `kumiko_mosaic/inserts.py`; adding one is a few lines. Every one is validated to be a single
  connected piece that touches all three frame edges.
* **ks1 .. ks40**: the silhouettes of Paper View's own 40 inserts, as extracted by the Kumiko
  Studio project (github.com/wangdrew/kumiko-designer). A bare number (`single:7`) means ks7.
  Not bundled: the data file is downloaded on first use to `~/.cache/kumiko_mosaic/`. His
  MakerWorld license forbids redistributing derivatives, so keep those for personal prints.
  Six of them (10, 11, 12, 13, 16, 40) are loose multi-part designs that need glue.

Modes: `single:<id>`, `luminance:<dark,...,light>` (`-` = no insert), `color` with
`--color-pattern-map '{"#1F4E9A": "asanoha", "dark red": "ks3"}'`, `none`.
`--edge-halves background` leaves the edge half-cells without pattern inserts.

### Insert geometry

| flag | default | note |
|---|---|---|
| `--strip` | 2.0 mm | kumiko strip width (his inserts use 2 mm) |
| `--insert-depth` | 11 mm | his inserts are 11 mm in a 12 mm deep frame |
| `--clearance` | 0.15 mm | inset per edge so the insert slides into the opening |
| `--bg-thickness` | 2 mm | generated flat background insert |

The opening is `pitch - sqrt(3) x mitsuke` tip to tip (44.80 mm at 50/3, the same number
Kumiko Studio measured on his STLs). **Print one pattern insert and one background triangle
first** and adjust `--clearance` before printing hundreds.

### Printing

`--bed 256` or `--bed 250 220`, `--bed-margin 8` (his advice: keep inserts off the plate edge),
`--gap 3`. Pattern plates and background plates are separate because he prints backgrounds at
0.1 mm layers and inserts at 0.2 mm. Each plate is one colour. `--insert-library DIR` lets
STL/3MF files override generated parts (`asanoha.stl`, `asanoha_half*.stl`, `ks07.stl`,
`background.stl`; half chirality is detected automatically).

## Geometry (verified)

* Bars run **vertically**; triangles point left and right. Columns are one altitude wide,
  rows one pitch tall, adjacent columns offset by half a pitch, so every column has a half
  triangle at the top and the bottom. Verified on his wall-panel photos.
* Outer size = `mitsuke + cols x pitch x sqrt(3)/2 + 2 x border` by
  `2 x mitsuke + rows x pitch + 2 x border`. This reproduces all three panels in his Patreon
  catalogue exactly (218 x 81.8 cm = 50 x 16, 79.4 x 121.8 = 18 x 24, 44.8 x 81.8 = 10 x 16).
* Half-triangle phase: his rightmost column has lattice vertices at the corners; the left
  column therefore alternates with the column count (even width: side-at-corner top-left, which
  his 18-column panel photo shows). This is also why his instructions say "even width: tick
  flip half triangles" in kumikodesigner. Override with `--orientation`.
* Up/down (left/right) cells use the same part rotated 180 degrees; the two half kinds L/R are
  mirror images and are separate parts (`*_half_L`, `*_half_R`).
* One thing to confirm on his reference card: `Triangle_Generator [cols, rows]` should show
  Size A = rows and Size B = cols. If Size A shows 2 x rows, his height count is in half
  pitches; halve it.

## Workflow

1. Run the tool, read `REPORT.md`.
2. In his **Frame Reference Generator.scad** enter `Triangle_Generator` and your bed; print the
   frame per his instructions, with the hanger plate the report names.
3. Print `plates/*` in the listed colours (pattern plates at 0.2 mm, background plates at
   0.1 mm, Arachne walls as he recommends).
4. Assemble by `assembly_map.csv`: column 1 is the left column, cell 1 is the top (a half).

## Layout

```
kumiko_mosaic/grid.py       lattice, sizing, cell model (cols x rows, phases, half kinds)
kumiko_mosaic/inserts.py    pattern catalogue, strip geometry, Kumiko Studio silhouettes, extrusion
kumiko_mosaic/imagemap.py   image fit, per-cell sampling, palettes, Lab matching
kumiko_mosaic/patterns.py   pattern assignment modes
kumiko_mosaic/bom.py        BOM, counts, plate packing, filament estimate
kumiko_mosaic/geometry.py   background/pattern part meshes, insert library, 3MF/STL/SVG export
kumiko_mosaic/render.py     previews with silhouettes, assembly maps
kumiko_mosaic/pipeline.py   orchestration + REPORT.md;  cli.py, web.py, web/index.html
```

Sources: Paper View's model and instructions (MakerWorld 1614814), kumikodesigner.com (the
designer he links to), Kumiko Studio (github.com/wangdrew/kumiko-designer) for the insert
silhouettes and the 44.8038 mm / 11 mm measurements.
