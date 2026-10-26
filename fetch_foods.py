"""Download nutrient data for the foods in config.yaml from USDA FoodData Central.

Usage:
    export FDC_API_KEY=your_key      # free at https://fdc.nal.usda.gov/api-key-signup
    python fetch_foods.py            # fetch anything not already cached
    python fetch_foods.py --refresh  # re-fetch everything

Results are cached in data/foods.json (per 100 g). Check the printed USDA
description for each food; if a match is wrong, put the right fdc_id in
config.yaml and run again with --refresh.
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

import usda
from nutrients import NUTRIENTS, extract_per_100g

HERE = Path(__file__).parent
CACHE = HERE / "data" / "foods.json"


def fetch_by_id(fdc_id, key):
    f = usda.food(fdc_id, key)
    return f["description"], f["data_type"], f["per_100g"]


def fetch_by_search(query, key):
    hits = usda.search(query, 5, key)
    if not hits:
        return None
    # Prefer the hit with the most nutrients we track (Foundation entries are sometimes sparse).
    best = max(hits[:5], key=lambda h: len(extract_per_100g(h.get("foodNutrients"))))
    return best["fdcId"], best["description"], best.get("dataType", ""), \
        extract_per_100g(best.get("foodNutrients"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=HERE / "config.yaml")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    key = usda.api_key()
    if key == "DEMO_KEY":
        print("No FDC_API_KEY set; using DEMO_KEY (about 30 requests/hour).")

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    for food in cfg["foods"]:
        name = food["name"]
        if food.get("custom"):
            continue  # nutrients come straight from config (e.g. a product label)
        cached = cache.get(name)
        if cached and not args.refresh and cached.get("fdc_id") == food.get("fdc_id", cached.get("fdc_id")):
            continue

        try:
            if food.get("fdc_id"):
                fdc_id = food["fdc_id"]
                desc, dtype, per100 = fetch_by_id(fdc_id, key)
            else:
                result = fetch_by_search(food.get("query", name), key)
                if result is None:
                    print(f"✗ {name}: no USDA match for '{food.get('query', name)}'")
                    continue
                fdc_id, desc, dtype, per100 = result
        except usda.USDAError as e:
            sys.exit(f"✗ {name}: {e}")

        missing = [k for k in NUTRIENTS if k not in per100]
        cache[name] = {"fdc_id": fdc_id, "description": desc, "data_type": dtype,
                       "per_100g": per100, "missing": missing}
        note = f" (no data: {', '.join(missing)})" if missing else ""
        print(f"✓ {name} → [{fdc_id}] {desc} ({dtype}){note}")

    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
    print(f"\nSaved {len(cache)} foods to {CACHE.relative_to(HERE)}")


if __name__ == "__main__":
    main()
