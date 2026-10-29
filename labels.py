"""Aldi label nutrition (data/aldi_labels.json, made by build_aldi_labels.py) laid over the USDA numbers.

Only calories, protein, fat, carbs, fiber and sodium come from the label, and only for products whose label agrees
with the USDA food. Everything else (vitamins, minerals, amino acids) stays USDA, since labels don't list it.
"""
import json
from functools import lru_cache
from pathlib import Path

import aldi

PATH = Path(__file__).parent / "data" / "aldi_labels.json"


@lru_cache(maxsize=1)
def table():
    if not PATH.exists():
        return {}
    return {k: v for k, v in json.loads(PATH.read_text(encoding="utf-8")).items() if v}


def apply(foods):
    """Swap in label numbers where the Aldi product has them. Copies per_serving: the catalog's dicts are shared."""
    rows = table()
    for f in foods:
        row = rows.get(aldi.type_of(f))
        if not row:
            continue
        scale = f["serving_g"] / 100.0
        f["per_serving"] = {**f["per_serving"], **{k: v * scale for k, v in row["per_100g"].items()}}
        f["from_label"] = row["off_name"]
