"""Look up label nutrition (Open Food Facts) for the Aldi product on file for each food type.

For every row in data/aldi_prices.json sold as a package, search Open Food Facts for that Aldi product and keep its
per-100 g calories, protein, fat, carbs, fiber and sodium, but only when they roughly agree with the USDA food the
planner uses for that type (calories within 20%, protein within 25% or 2 g). A mismatch usually means the entry is a
different product or a different state (canned vs cooked), so USDA stays. Fresh produce and meat sold by the pound
have no label and are skipped.

Writes data/aldi_labels.json: {type: {"off_name", "brands", "per_100g": {...}}}. Searches are rate limited, so this
takes about ten minutes. Re-run to refresh; existing answers are kept unless --redo is given.
    python build_aldi_labels.py
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

import aldi
import basket
import catalog
import optimize

OUT = Path(__file__).parent / "data" / "aldi_labels.json"
HEADERS = {"User-Agent": "diet-optimizer/0.1 (personal project)"}
BRANDS = ("aldi", "simply nature", "goldhen", "earthly grains", "southern grove", "millville", "specially selected",
          "happy farms", "season's choice", "friendly farms", "fit & active", "baker's corner", "reggano", "clancy",
          "appleton farms", "never any", "park street", "benton", "sundae shoppe", "stonemill", "kirkwood", "lunch mate",
          "deutsche kuche", "chef's cupboard", "pueblo lindo", "casa mamita", "fremont fish", "tuscan garden",
          "nature's nectar", "carlini", "L'oven fresh", "bremer", "burman", "little salad bar", "countryside creamery")
KEYS = {"energy-kcal_100g": "energy", "proteins_100g": "protein", "fat_100g": "fat", "carbohydrates_100g": "carbs",
        "fiber_100g": "fiber"}


def clean(title):
    t = re.sub(r"\b\d+(\.\d+)?\s*(oz|lb|ct|count|pack)\b", " ", title, flags=re.I)
    t = re.sub(r",.*$|- .*$", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def search(term):
    url = ("https://us.openfoodfacts.org/cgi/search.pl?" + urllib.parse.urlencode({
        "search_terms": term, "search_simple": 1, "action": "process", "json": 1, "page_size": 12,
        "fields": "product_name,brands,stores,nutriments"}))
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=40) as r:
                return json.load(r).get("products", [])
        except Exception:
            time.sleep(10 * (attempt + 1))
    return []


def per100(p):
    n = p.get("nutriments", {})
    out = {}
    for src, dst in KEYS.items():
        if n.get(src) is None:
            return None
        out[dst] = round(float(n[src]), 2)
    na = n.get("sodium_100g")
    out["sodium"] = round(float(na) * 1000, 1) if na is not None else None
    if out["sodium"] is None:
        del out["sodium"]
    return out


def agrees(off, usda):
    e, u = off["energy"], usda.get("energy", 0)
    if u <= 0 or abs(e - u) > 0.2 * u:
        return False
    pr, pu = off["protein"], usda.get("protein", 0)
    return abs(pr - pu) <= max(2.0, 0.25 * pu)


def usda_per_100g():
    """{type: per-100 g nutrients of the USDA food used for that type}."""
    cfg = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text(encoding="utf-8"))
    staples, _ = optimize.load_foods(cfg, json.loads((Path(__file__).parent / "data" / "usda_cache.json").read_text(encoding="utf-8")), set())
    out = {}
    for f in staples + catalog._load():
        t = aldi.type_of(f)
        if t and t not in out and f["serving_g"] > 0:
            out[t] = {k: v * 100.0 / f["serving_g"] for k, v in f["per_serving"].items()}
    return out


def main():
    redo = "--redo" in sys.argv
    done = {} if redo or not OUT.exists() else json.loads(OUT.read_text(encoding="utf-8"))
    usda = usda_per_100g()
    for label, row in basket.prices().items():
        if label in done or row["mode"] == "lb" or label not in usda:
            continue
        title = clean(row["title"])
        best, best_score = None, 0
        words = set(re.findall(r"[a-z]+", title.lower()))
        for p in search(title):
            brand = f"{p.get('brands', '')} {p.get('stores', '')}".lower()
            per = per100(p)
            if not per or not any(b in brand for b in BRANDS):
                continue
            name_words = set(re.findall(r"[a-z]+", (p.get("product_name") or "").lower()))
            score = len(words & name_words) / max(1, len(words))
            if score > best_score and agrees(per, usda[label]):
                best, best_score = (p, per), score
        if best and best_score >= 0.5:
            done[label] = {"off_name": best[0].get("product_name"), "brands": best[0].get("brands"), "per_100g": best[1]}
            print(f"{label:22s} OK   {best[0].get('product_name')!r} kcal {best[1]['energy']:.0f} (usda {usda[label]['energy']:.0f})", flush=True)
        else:
            done[label] = None
            print(f"{label:22s} --   no agreeing Aldi label for {title!r}", flush=True)
        OUT.write_text(json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8")
        time.sleep(7)
    print(f"{sum(1 for v in done.values() if v)} of {len(done)} types have an Aldi label")


if __name__ == "__main__":
    main()
