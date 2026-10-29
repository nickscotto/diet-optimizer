"""The built-in food catalog (data/catalog.json, made by build_catalog.py) as planner foods.

A few hundred foods is too many for one optimization, so `shortlist` keeps
the most useful ones per role for the person's targets. Foods they like and
the hand-picked staples in config.yaml always stay in.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

import aldi
import aminos
import basket
import contaminants
import epicure
import roles
from nutrients import CONTAMINANT_KEYS

PATH = Path(__file__).parent / "data" / "catalog.json"
PORK = re.compile(r"\b(pork|bacon|ham|prosciutto|pancetta|sausages?|bratwurst|lard|salami|pepperoni)\b", re.I)


WHEY = re.compile(r"\bwhey\b|protein (powder|isolate)", re.I)


def extra_tags(food):
    """Avoid-list tags the catalog build did not add."""
    text = f"{food.get('name', '')} {food.get('description', '')}"
    return (["pork"] if PORK.search(text) else []) + (["whey"] if WHEY.search(text) else [])
# How many catalog foods to keep per role (on top of staples and liked foods).
QUOTAS = {"protein": 22, "starch": 12, "veg": 24, "fruit": 12, "fat": 8, "drink": 3, "other": 6}
SKIP_NUTRIENTS = {"energy", "protein", "fat", "carbs", "sodium", "fiber"}


SOFT_CHEESE = re.compile(r"cottage|ricotta", re.I)


def _cheese_ounce(c):
    """USDA lists some cheeses by the cup (100 g of grated parmesan, 240 g of cream cheese). Portion cheese by
    the ounce instead, at most 3 oz a day (1.5 for parmesan)."""
    if "cheese" in c["name"].lower() and not SOFT_CHEESE.search(c["name"]) and c["serving_g"] > 40:
        return {**c, "serving_g": 28.35, "label": "1 oz", "max_servings": 1.5 if re.search("parmesan|romano", c["name"], re.I) else 3}
    return c


@lru_cache(maxsize=1)
def _load():
    """Catalog foods with roles; shared and read-only (copy before changing)."""
    if not PATH.exists():
        return []
    ep = epicure.load()
    foods = []
    for c in map(_cheese_ounce, json.loads(PATH.read_text(encoding="utf-8"))):
        scale = c["serving_g"] / 100.0
        food = {**c, "source": f"USDA {c['fdc_id']}"}
        food["contains"] = sorted(set(c["contains"]) | set(extra_tags(c)))
        food["per_serving"] = contaminants.add(aminos.digest({k: v * scale for k, v in c["per_100g"].items()}, food), food)
        foods.append(food)
    roles.annotate(foods, ep)
    return foods


def available(excluded_tags, skip_ids=(), skip_names=(), staples=()):
    """Catalog foods allowed by the diet / allergy filters and sold at Aldi (one per kind of food, so not three
    broccolis), minus ones already covered by a staple."""
    skip_names = {n.lower() for n in skip_names}
    out = []
    for f in _load():
        if excluded_tags & set(f["contains"]) or f["fdc_id"] in skip_ids or f["name"].lower() in skip_names:
            continue
        out.append(dict(f))
    covered = {aldi.type_of(f) for f in staples}
    return [f for f in aldi.select(out) if aldi.type_of(f) not in covered and basket.product(f)]


def _usefulness(f, targets):
    """How much of a day's nutrient goals this food covers per calorie, capped per nutrient."""
    kcal = f["per_serving"].get("energy", 0.0)
    if kcal <= 0:
        return 0.0
    kcal_goal = targets["energy"]["goal"]
    vals, burden = [], 0.0
    for k, t in targets.items():
        if k in CONTAMINANT_KEYS:    # a serving that uses up much of the day's limit ranks lower (at most -0.1 per contaminant)
            burden += 0.1 * min(1.0, f["per_serving"].get(k, 0.0) / t["max"])
            continue
        ref = t.get("goal", t.get("min"))
        if k in SKIP_NUTRIENTS or not ref:
            continue
        vals.append(min(1.5, f["per_serving"].get(k, 0.0) / kcal * kcal_goal / ref))
    score = (sum(vals) / len(vals) if vals else 0.0) - burden
    if f["role"] == "protein":
        score += f["per_serving"].get("protein", 0.0) * 4 / kcal    # lean protein sources first
    return score - 0.02 * len([k for k in f["missing"] if k in targets])


def shortlist(foods, targets):
    """Best QUOTAS[role] foods per role."""
    by_role = {}
    for f in foods:
        by_role.setdefault(f["role"], []).append(f)
    keep = []
    for role, items in by_role.items():
        items.sort(key=lambda f: _usefulness(f, targets), reverse=True)
        keep += items[:QUOTAS.get(role, 0)]
    return keep
