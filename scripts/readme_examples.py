"""Regenerate the Examples section of README.md from examples/gallery/gallery.json."""
from pathlib import Path
import json, re

ROOT = Path(__file__).resolve().parent.parent
cards = json.load(open(ROOT / "examples" / "gallery" / "gallery.json"))
order = ["great_wave", "golden_gate_bridge", "toucan", "earth", "starry_night", "red_fuji"]
cards.sort(key=lambda c: order.index(c["name"]) if c["name"] in order else 99)
titles = {"great_wave": "The Great Wave (Hokusai)", "golden_gate_bridge": "Golden Gate Bridge", "toucan": "Toco toucan",
          "earth": "Earth from Apollo 17", "starry_night": "The Starry Night (Van Gogh)", "red_fuji": "Red Fuji (Hokusai)"}
lines = ["## Examples\n",
         "Source on the left, planned panel on the right, drawn with the real insert silhouettes. All at 60 columns "
         "(2.6 m wide at 50 mm pitch, 1.6 m at 30 mm), 8 Bambu PLA strip filaments, 3 grey background filaments, "
         "at most 4 line patterns, dark frame. The fidelity number is the mean CIELAB error at viewing distance "
         "(lower is better; under 10 reads as the picture from across a room). "
         "Rebuild with `python scripts/make_gallery.py && python scripts/readme_examples.py`.\n"]
for c in cards:
    r = next((r for r in c["rows"] if r["variant"] == "60cols"), c["rows"][0])
    cmp_ = f"examples/gallery/{c['name']}_{r['variant']}_compare.jpg"
    lines.append(f"**{titles.get(c['name'], c['name'])}**: {r['grid'][0]} x {r['grid'][1]} triangles, "
                 f"{r['cells']} cells, {r['inserts']} pattern inserts, fidelity {r['fidelity']}\n")
    lines.append(f"![{titles.get(c['name'], c['name'])}]({cmp_})\n")
section = "\n".join(lines)
readme = (ROOT / "README.md").read_text()
if "## Examples" in readme:
    readme = re.sub(r"## Examples\n.*?(?=\n## )", section, readme, flags=re.S)
else:
    readme = readme.replace("## Install", section + "\n## Install", 1)
(ROOT / "README.md").write_text(readme)
print("README examples section updated with", len(cards), "images")
