"""Fetch candidate catalog images from Wikimedia Commons with licence + attribution metadata.

Usage: python scripts/fetch_catalog.py [--per-query 25] [--target 1000]
Writes catalog/images/<id>.jpg (1280 px wide thumbnails) and catalog/sources.json.
Only Public domain, CC0, CC BY and CC BY-SA files are kept (no NC/ND). Polite: one API call
per 0.4 s, 4 download threads, descriptive User-Agent. Re-runnable: existing ids are skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "catalog"
UA = {"User-Agent": "kumiko-mosaic-catalog/0.1 (personal 3D-printing project; github.com/npow/kumiko-mosaic)"}
OK_LICENSES = ("Public domain", "CC0", "CC BY", "CC BY-SA", "PD", "Pd")

QUERIES = {
    "mountains": ["Matterhorn sunrise", "Mount Rainier", "Denali mountain", "Mount Fuji cherry blossoms", "Himalaya peak sunrise",
                  "Half Dome Yosemite", "Torres del Paine", "Dolomites sunrise lake", "Mount Hood reflection", "Grand Teton"],
    "sky and water": ["sunset silhouette palm", "aurora borealis", "lighthouse storm waves", "waterfall long exposure", "iceberg blue",
                      "Milky Way over desert", "lightning storm", "sailboats sunset sea", "moon over ocean", "rainbow over valley"],
    "animals": ["tiger portrait", "lion male portrait", "zebra", "penguin colony", "snowy owl", "bald eagle portrait", "scarlet macaw",
                "flamingo", "monarch butterfly", "peacock displaying", "koi fish pond", "red fox snow", "gray wolf portrait",
                "elephant sunset", "giraffe portrait", "giant panda", "polar bear", "kingfisher", "hummingbird flower", "chameleon",
                "tree frog red eyed", "toucan", "parrot lorikeet", "jellyfish", "sea turtle", "gorilla portrait", "leopard portrait", "puffin"],
    "flowers and nature": ["sunflower field", "tulip field Netherlands", "lotus flower", "red rose closeup", "poppy field", "cherry blossom",
                           "lavender field", "autumn maple leaves red", "orchid closeup", "dandelion", "fern unfurling", "cactus flower",
                           "pumpkin patch", "lemons", "strawberries", "rainbow eucalyptus"],
    "art": ["Hokusai woodblock print", "Hiroshige woodblock print", "Van Gogh painting", "Klimt painting", "Monet painting", "Mucha poster",
            "Henri Rousseau painting", "Gauguin painting", "Kandinsky painting", "Matisse painting", "Mondrian painting", "Rembrandt portrait",
            "Vermeer painting", "Munch painting", "Utagawa Kuniyoshi", "ukiyo-e wave", "art nouveau poster", "vintage travel poster",
            "Escher", "Persian miniature", "stained glass window", "Edvard Munch Scream"],
    "architecture": ["Taj Mahal", "Eiffel Tower night", "Sagrada Familia", "torii gate", "pagoda autumn", "Sydney Opera House", "Tower Bridge",
                     "Brooklyn Bridge", "Burj Khalifa", "Petra Treasury", "Santorini white domes", "Venice canal gondola", "Angkor Wat sunrise",
                     "Neuschwanstein castle", "Hagia Sophia", "Colosseum", "Machu Picchu", "windmill Netherlands", "Alhambra", "Forbidden City"],
    "space": ["Earth from space", "Moon full close-up", "Saturn rings", "Jupiter Great Red Spot", "Orion nebula", "Pillars of Creation",
              "Mars surface", "solar eclipse corona", "Apollo astronaut", "Hubble galaxy"],
    "vehicles and objects": ["vintage car red", "Vespa scooter", "hot air balloon", "steam locomotive", "biplane", "sailing ship", "bicycle red wall",
                             "lighthouse red white", "traditional Japanese lantern", "tea kettle", "guitar closeup", "chess pieces", "carousel"],
    "people and culture": ["geisha Kyoto", "Day of the Dead skull", "Venetian carnival mask", "Holi colors", "Albert Einstein", "Marie Curie",
                           "samurai armor", "totem pole", "flamenco dancer", "Maasai portrait", "sumo", "dragon dance"],
    "cities": ["Manhattan skyline sunset", "Hong Kong skyline night", "Tokyo Shibuya crossing", "Paris rooftops", "London phone box",
               "New York taxi", "Havana street cars", "Marrakech market", "Chefchaouen blue city", "Burano colorful houses"],
}


def api(params):
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60))
        except Exception:
            time.sleep(2 + attempt * 3)
    return {}


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def license_ok(lic: str) -> bool:
    l = lic or ""
    if re.search(r"\bNC\b|\bND\b|-NC|-ND", l):
        return False
    return any(l.startswith(k) or k in l for k in OK_LICENSES)


def search(q: str, limit: int):
    r = api({"action": "query", "generator": "search", "gsrsearch": q + " filetype:bitmap", "gsrnamespace": 6,
             "gsrlimit": min(50, limit * 2), "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime",
             "iiurlwidth": 1280, "format": "json"})
    out = []
    for p in sorted(r.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        ii = (p.get("imageinfo") or [{}])[0]
        w, h = ii.get("width", 0), ii.get("height", 0)
        meta = ii.get("extmetadata", {})
        lic = meta.get("LicenseShortName", {}).get("value", "")
        if w < 1600 or h < 900 or not (0.55 < w / h < 2.3) or not license_ok(lic):
            continue
        if ii.get("mime") not in ("image/jpeg", "image/png"):
            continue
        out.append({"title": p["title"], "w": w, "h": h, "license": lic,
                    "license_url": meta.get("LicenseUrl", {}).get("value", ""),
                    "author": clean(meta.get("Artist", {}).get("value", "")) or "unknown",
                    "page": "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(p["title"].replace(" ", "_")),
                    "thumb": ii.get("thumburl") or ii["url"]})
        if len(out) >= limit:
            break
    return out


def ahash(im: Image.Image) -> int:
    g = im.convert("L").resize((8, 8))
    px = list(g.getdata())
    avg = sum(px) / 64
    return sum(1 << i for i, v in enumerate(px) if v > avg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-query", type=int, default=12)
    ap.add_argument("--target", type=int, default=1000)
    a = ap.parse_args()
    (OUT / "images").mkdir(parents=True, exist_ok=True)
    src_path = OUT / "sources.json"
    sources = json.loads(src_path.read_text()) if src_path.exists() else {}
    seen_titles = {v["title"] for v in sources.values()}
    hashes = []
    for v in sources.values():
        hashes.append(v.get("ahash", 0))

    cand_path = OUT / "candidates.json"
    cands = []
    if cand_path.exists():
        cands = [c for c in json.loads(cand_path.read_text()) if c["title"] not in {v["title"] for v in sources.values()}]
        print(f"reusing {len(cands)} saved candidates", flush=True)
        seen_titles |= {c["title"] for c in cands}
    for cat, qs in ([] if cands else QUERIES.items()):
        for q in qs:
            if len(sources) + len(cands) >= a.target * 1.3:
                break
            for c in search(q, a.per_query):
                if c["title"] not in seen_titles:
                    seen_titles.add(c["title"])
                    c.update({"category": cat, "query": q})
                    cands.append(c)
            time.sleep(0.4)
        print(f"{cat}: candidates so far {len(cands)}", flush=True)
    if cands and not cand_path.exists():
        cand_path.write_text(json.dumps(cands, indent=1, ensure_ascii=False))

    def dl(c):
        cid = hashlib.sha1(c["title"].encode()).hexdigest()[:10]
        path = OUT / "images" / f"{cid}.jpg"
        for attempt in range(5):
            try:
                data = urllib.request.urlopen(urllib.request.Request(c["thumb"], headers=UA), timeout=90).read()
                im = Image.open(io.BytesIO(data)).convert("RGB")
                time.sleep(0.4)
                return cid, im, c
            except Exception as e:  # noqa: BLE001
                fails.append(str(e)[:60])
                time.sleep(3 * (attempt + 1))        # back off on 429 / transient errors
        return None

    kept = 0
    fails: list = []
    dup = 0
    with ThreadPoolExecutor(2) as ex:
        for res in ex.map(dl, cands):
            if res is None:
                continue
            cid, im, c = res
            h = ahash(im)
            if any(bin(h ^ x).count("1") <= 2 for x in hashes):      # near-duplicate
                dup += 1
                continue
            hashes.append(h)
            im.save(OUT / "images" / f"{cid}.jpg", quality=88)
            c = {k: v for k, v in c.items() if k != "thumb"}
            c["ahash"] = h
            c["file"] = f"images/{cid}.jpg"
            sources[cid] = c
            kept += 1
            if kept % 50 == 0:
                src_path.write_text(json.dumps(sources, indent=1, ensure_ascii=False))
                print(f"downloaded {kept} (dups {dup}, errors {len(fails)}: {sorted(set(fails))[:2]})", flush=True)
            if len(sources) >= a.target:
                break
    src_path.write_text(json.dumps(sources, indent=1, ensure_ascii=False))
    print(f"catalog images: {len(sources)} (dups {dup}, errors {len(fails)})")


if __name__ == "__main__":
    main()
