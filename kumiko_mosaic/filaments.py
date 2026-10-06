"""Purchasable filament catalogue (Bambu Lab PLA Matte and PLA Basic, official hex codes from
Bambu's colour tables, as collected by the Kumiko Studio project). Add your own below or pass
--palette with the filaments you already have."""
from __future__ import annotations

from typing import Dict, List

from .imagemap import Filament

BAMBU_MATTE = [
    ("Ivory White", "#FFFFFF"), ("Bone White", "#CBC6B8"), ("Desert Tan", "#E8DBB7"), ("Latte Brown", "#D3B7A7"),
    ("Caramel", "#AE835B"), ("Terracotta", "#B15533"), ("Dark Brown", "#7D6556"), ("Dark Chocolate", "#4D3324"),
    ("Lilac Purple", "#AE96D4"), ("Sakura Pink", "#E8AFCF"), ("Mandarin Orange", "#F99963"), ("Lemon Yellow", "#F7D959"),
    ("Plum", "#950051"), ("Scarlet Red", "#DE4343"), ("Dark Red", "#BB3D43"), ("Dark Green", "#68724D"),
    ("Grass Green", "#61C680"), ("Apple Green", "#C2E189"), ("Ice Blue", "#A3D8E1"), ("Sky Blue", "#56B7E6"),
    ("Marine Blue", "#0078BF"), ("Dark Blue", "#042F56"), ("Ash Gray", "#9B9EA0"), ("Nardo Gray", "#757575"),
    ("Charcoal", "#000000"),
]
BAMBU_BASIC = [
    ("Jade White", "#FFFFFF"), ("Magenta", "#EC008C"), ("Gold", "#E4BD68"), ("Mistletoe Green", "#3F8E43"),
    ("Red", "#C12E1F"), ("Purple", "#5E43B7"), ("Beige", "#F7E6DE"), ("Pink", "#F55A74"), ("Sunflower Yellow", "#FEC600"),
    ("Bronze", "#847D48"), ("Turquoise", "#00B1B7"), ("Indigo Purple", "#482960"), ("Light Gray", "#D1D3D5"),
    ("Hot Pink", "#F5547C"), ("Yellow", "#F4EE2A"), ("Cocoa Brown", "#6F5034"), ("Cyan", "#0086D6"), ("Blue Grey", "#5B6579"),
    ("Silver", "#A6A9AA"), ("Orange", "#FF6A13"), ("Bright Green", "#BECF00"), ("Brown", "#9D432C"), ("Blue", "#0A2989"),
    ("Dark Gray", "#545454"), ("Gray", "#8E9089"), ("Pumpkin Orange", "#FF9016"), ("Bambu Green", "#00AE42"),
    ("Maroon Red", "#9D2235"), ("Cobalt Blue", "#0056B8"), ("Black", "#000000"),
]

SETS: Dict[str, List[Filament]] = {
    "bambu-matte": [Filament(f"Matte {n}", h) for n, h in BAMBU_MATTE],
    "bambu-basic": [Filament(f"Basic {n}", h) for n, h in BAMBU_BASIC],
}
SETS["bambu"] = SETS["bambu-matte"] + SETS["bambu-basic"]


def catalogue(name: str = "bambu") -> List[Filament]:
    if name not in SETS:
        raise KeyError(f"unknown filament set {name}; have {', '.join(SETS)}")
    return SETS[name]
