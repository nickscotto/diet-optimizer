"""Small USDA FoodData Central client shared by fetch_foods.py and the web app.

The API key comes from FDC_API_KEY (or a file named by FDC_API_KEY_FILE) and
is never included in messages or errors: request URLs carry it, so failures
are re-raised without the URL.
"""
import math
import os
import re
import time
from pathlib import Path

import requests

from nutrients import extract_per_100g

API = "https://api.nal.usda.gov/fdc/v1"
DATA_TYPES = ["Foundation", "SR Legacy"]  # lab-analyzed, per-100 g, broad nutrient coverage


class USDAError(RuntimeError):
    pass


def api_key():
    key = os.environ.get("FDC_API_KEY")
    if not key and os.environ.get("FDC_API_KEY_FILE"):
        key = Path(os.environ["FDC_API_KEY_FILE"]).read_text().strip()
    return key or "DEMO_KEY"


def clean_query(query):
    """The search endpoint answers 400 to %, /, ( ) and similar characters."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s,.-]", " ", query)).strip()


def get(path, params=None, key=None):
    params = {**(params or {}), "api_key": key or api_key()}
    for attempt in range(4):
        try:
            r = requests.get(f"{API}{path}", params=params, timeout=30)
        except requests.RequestException as e:
            raise USDAError(f"USDA API unreachable ({type(e).__name__})") from None
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise USDAError(f"USDA API returned {r.status_code} for {path}")
        return r.json()
    raise USDAError("USDA API kept rate-limiting. Use your own FDC_API_KEY instead of DEMO_KEY.")


def search(query, page_size=5, key=None):
    """Hits as dicts: fdcId, description, dataType, foodNutrients."""
    res = get("/foods/search", {"query": clean_query(query), "dataType": ",".join(DATA_TYPES),
                                "pageSize": page_size}, key)
    return res.get("foods", [])


def food(fdc_id, key=None):
    """Full record for one food: description, data type, per-100 g nutrients, portions."""
    f = get(f"/food/{int(fdc_id)}", {}, key)
    portions = []
    for p in f.get("foodPortions") or []:
        grams = p.get("gramWeight")
        if not grams:
            continue
        desc = (p.get("portionDescription") or "").strip()
        if desc and desc[0].isdigit():            # SR Legacy style: "1 cup, chopped"
            label = desc
        else:                                     # Foundation style: amount + unit + "shredded"
            unit = (p.get("measureUnit") or {}).get("name", "")
            unit = {"undetermined": "", "RACC": "serving"}.get(unit, unit)
            amount = f"{float(p.get('amount') or 1):g}"
            label = " ".join(w for w in (amount, unit, desc or p.get("modifier") or "") if w)
        portions.append({"grams": float(grams), "label": label})
    return {"fdc_id": int(fdc_id), "description": f["description"], "data_type": f.get("dataType", ""),
            "per_100g": extract_per_100g(f.get("foodNutrients")), "portions": portions}



def pick_serving(portions, kcal_per_100g):
    """The USDA portion closest to ~150 kcal (a slice of cheese, not a cup of it)."""
    target = 150 / (kcal_per_100g / 100) if kcal_per_100g > 0 else 100
    target = min(max(target, 20), 250)
    usable = [p for p in portions if 10 <= p["grams"] <= 400]
    if not usable:
        grams = round(target / 10) * 10
        return float(grams), f"{grams:.0f} g"
    best = min(usable, key=lambda p: abs(math.log(p["grams"] / target)))
    return best["grams"], best["label"] or "1 serving"


# ---------------------------------------------------------------- ranking ---
# USDA's search puts oat oil, apple croissants and chicken spread ahead of the
# plain food. This re-ranks a larger result set so the simplest match comes first.
PROCESSED = {"oil", "bagel", "bread", "muffin", "cookie", "cake", "candy", "babyfood", "baby", "bar", "snack",
             "chip", "pie", "dressing", "restaurant", "fast", "flavored", "imitation", "substitute", "formula",
             "infant", "pastry", "pudding", "cracker", "pancake", "waffle", "beverage", "drink", "extract",
             "concentrate", "powder", "flour", "syrup", "juice", "pizza", "sandwich", "pasta", "noodle",
             "tortilla", "cheesecake", "dessert", "granola", "fried", "breaded", "stuffed", "spread",
             "croissant", "strudel", "souffle", "bratwurst", "sausage", "fat", "meatless", "mix", "frozen",
             "deli", "lunchmeat", "luncheon", "prepackaged", "roll", "smoked", "cured", "nugget", "patty",
             "sliced", "flavor", "seasoned", "basted", "mesquite", "marinated", "glazed", "taco", "burrito"}
MILD = {"dried", "dehydrated", "sprouted", "unspecified", "bran", "dry", "salted", "fermented", "sheep",
        "goat", "buffalo", "evaporated", "condensed"}


def _singular(w):
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("oes", "ches", "shes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us")) and len(w) > 3:
        return w[:-1]
    return w


def _words(s):
    return [_singular(w) for w in re.findall(r"[a-z]+", s.lower())]


def rank(query, hits):
    """Order USDA search hits so the plainest match for `query` comes first."""
    q = _words(query)
    qset = set(q)
    scored = []
    for h in hits:
        desc = h["description"]
        segs = [_words(s) for s in desc.split(",")]
        tokens = {w for s in segs for w in s}
        score = 0.0
        if qset <= tokens:
            score += 3
        if segs and set(segs[0]) == qset:
            score += 3                                    # "Chicken, ..." for "chicken"
        elif any(set(s) == qset for s in segs):
            score += 1.5
        if qset <= {w for s in segs[:3] for w in s}:
            score += 1.5                                  # near the front: "Cereals, oats, ..."
        score -= 0.4 * (len(segs) - 1)                    # fewer qualifiers = plainer
        score -= min(4, 2 * len((tokens & PROCESSED) - qset))
        score -= 1.2 * len((tokens & MILD) - qset)
        if tokens & {"raw", "cooked"}:
            score += 0.5
        if "meat" in tokens and "only" in tokens:
            score += 1.5                                  # plain cuts: "..., breast, meat only, cooked"
        if h.get("dataType") == "Foundation":
            score += 0.3
        scored.append((score, h))
    scored.sort(key=lambda x: -x[0])
    return [h for _, h in scored]
