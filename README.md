# Kumiko mosaic

Turn a photo into a 3D-printed kumiko panel.

Kumiko is the Japanese craft of lattice screens made from thin wooden strips. This tool takes an image
and plans a wall panel in that style. The panel is a lattice of triangles. Each triangle holds a
printed line insert, and the colour and density of those lines reproduce the picture.

![The Great Wave as a kumiko panel](examples/gallery/great_wave_60cols_compare.jpg)

## What it gives you

- **A plan for every triangle.** Filament, line pattern and background for each cell, so you don't
  colour thousands of triangles by hand.
- **Colours you can buy.** They come from the Bambu Lab PLA catalogue, or from a list of the spools you
  own.
- **Files to print and build with.** Plates in 3MF and STL, one colour per plate, a part count and
  filament estimate, and an assembly sheet showing which insert goes where.
- **A fit for Paper View's frame.** You print his
  [Large Kumiko Frame Generator](https://makerworld.com/en/models/1614814-large-kumiko-frame-generator)
  frame, and this tool generates the inserts that go in it.

## Try it

```bash
git clone https://github.com/npow/kumiko-mosaic && cd kumiko-mosaic
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
./serve.sh        # open http://localhost:8000
```

Upload a photo and click **Plan it**. From the command line:

```bash
python -m kumiko_mosaic.cli photo.jpg out/
```

`out/` contains the preview, `REPORT.md` (frame settings, filaments, part counts), the assembly sheet
and the plate files.

## Inserts

Each triangle gets a flat background insert and, usually, a line insert: a kumiko pattern of thin
strips. At the default size there are 17 line patterns, from sparse to dense. The planner uses a
sparse pattern where the image is dark and a dense one where it is bright, and limits a panel to four
patterns to keep the sorting manageable.

![Line patterns](docs/img/patterns.png)

Strips are 2 mm wide and every opening is at least 2.5 mm, so the inserts stay open lines. Paper View's
40 original inserts can be used too (`--pattern-mode single:7`).

## Colours

The planner picks eight filaments for the line inserts and three for the backgrounds from the 55 Bambu
Lab PLA Matte and Basic colours, whichever fit the image best. To use spools you already have, list them
instead.

![Filaments](docs/img/filaments.png)

## Zoom in

The panel is a vector drawing, so you can zoom until single inserts fill the screen. The preview on
the web app's result page does this (scroll to zoom, drag to pan, double-click to zoom in), and so
does every panel in `/catalog`. The picture below shows the idea in three steps.

![Zoom: whole panel, a square, the line inserts](docs/img/zoom.png)

## Examples

![Photo, kumiko plan, zoom into the inserts](examples/catalog_tour.gif)

![119 kumiko plans](examples/catalog_wall.jpg)

Credits for the images are in [examples/catalog/CREDITS.md](examples/catalog/CREDITS.md).

## Notes

- Bold, high-contrast subjects work best. Busy or foggy scenes lose their subject.
- Size is set by the number of columns. The default is 40, about 1.75 m wide at 50 mm pitch. At 60 or
  more columns the picture is clearly recognisable, and the panel has several thousand parts.
- Options, colour matching, the resolution guide, patterns and geometry are in
  [docs/reference.md](docs/reference.md).

## Licence

The code is [MIT](LICENSE). The example images are derived from Wikimedia Commons photos and keep
their licences, listed in [examples/LICENSE.md](examples/LICENSE.md). The frame itself is Paper View's
and is covered by his licence; this repository does not include it.
