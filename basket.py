"""Turn a day's plan into an Aldi grocery basket: whole packages, prices and a total.

Prices live in data/aldi_prices.json (one row per Aldi type, see price_add.py). Foods sold by the pound are priced by
weight; everything else is bought in whole packages, rounded up, and the leftover is reported.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

import aldi

PATH = Path(__file__).parent / "data" / "aldi_prices.json"
ROUND_SLACK = 0.03        # a plan needing 1.02 packages still buys 1


@lru_cache(maxsize=1)
def prices():
    return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}


def per_gram(food):
    """Shelf price per gram of this food's Aldi type, or None when there is no price on file."""
    row = prices().get(aldi.type_of(food) or food["name"].lower())
    if not row:
        return None
    return row["price"] / (453.6 if row["mode"] == "lb" else row["pack_g"])


def median_per_gram():
    vals = sorted(r["price"] / (453.6 if r["mode"] == "lb" else r["pack_g"]) for r in prices().values())
    return vals[len(vals) // 2] if vals else 0.0


def build(foods, alloc, days):
    """alloc[i] = servings of foods[i] per meal. Returns the basket for `days` days."""
    table, need, rows, unpriced = prices(), {}, [], []
    for f, a in zip(foods, alloc):
        grams = float(a.sum()) * f["serving_g"] * days
        if grams < 1:
            continue
        label = aldi.type_of(f) or f["name"].lower()
        slot = need.setdefault(label, {"name": f["name"], "grams": 0.0})
        slot["grams"] += grams
    for label, slot in sorted(need.items(), key=lambda kv: kv[1]["name"]):
        row = table.get(label)
        if not row:
            unpriced.append(slot["name"])
            continue
        grams = slot["grams"]
        if row["mode"] == "lb":
            lb = math.ceil(grams / 453.6 * 10) / 10                 # to a tenth of a pound
            rows.append({"name": slot["name"], "need_g": round(grams), "product": row["title"], "buy": f"{lb:.1f} lb",
                         "unit_price": row["price"], "cost": round(lb * row["price"], 2), "leftover_g": 0})
        else:
            packs = max(1, math.ceil(grams / row["pack_g"] - ROUND_SLACK))
            rows.append({"name": slot["name"], "need_g": round(grams), "product": row["title"], "buy": f"{packs} pack{'s' if packs > 1 else ''}",
                         "unit_price": row["price"], "cost": round(packs * row["price"], 2),
                         "leftover_g": max(0, round(packs * row["pack_g"] - grams))})
    return {"days": days, "items": rows, "unpriced": unpriced, "total": round(sum(r["cost"] for r in rows), 2),
            "priced_on": max((r["date"] for r in table.values()), default=None)}
