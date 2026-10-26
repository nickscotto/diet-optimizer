"""Web app: anyone enters their stats, diet filters and foods they like, and
gets a day of breakfast / lunch / dinner that hits their nutrient targets.

Run:
    python app.py --key-file path/to/usda_api_key.txt     # or set FDC_API_KEY
    then open http://127.0.0.1:8000

The USDA key stays on the server. Liked foods are looked up in USDA
FoodData Central and cached in data/usda_cache.json; staples come from
config.yaml (data/foods.json, filled by fetch_foods.py).
"""
import argparse
import json
import math
import os
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import aldi
import basket
import aminos
import catalog
import contaminants
import epicure
import meals
import optimize
import planner
import roles
import usda
from nutrients import NUTRIENTS

HERE = Path(__file__).parent
USDA_CACHE = HERE / "data" / "usda_cache.json"
# Staples cost 1 per food in the planner (x FOOD_WEIGHT). A liked food earns a small reward, about 2 penalty
# points (a 2% miss on one nutrient minimum), so it is a tie-breaker: it goes in when it costs no nutrient
# coverage and is never forced in.
LIKED_COST = -2.0

DIET_EXCLUDES = {
    "omnivore": set(),
    "pescatarian": {"meat"},
    "vegetarian": {"meat", "fish"},
    "vegan": {"meat", "fish", "dairy", "egg"},
}

app = FastAPI(title="Diet optimizer")
# The page can live on GitHub Pages while this server runs elsewhere: CORS_ORIGINS="https://you.github.io".
app.add_middleware(CORSMiddleware, allow_origins=[o for o in os.environ.get("CORS_ORIGINS", "").split(",") if o],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
_cache_lock = threading.Lock()
_solve_slots = threading.BoundedSemaphore(2)   # solves are CPU and memory heavy; extra requests wait their turn
_plans = {}                                     # finished plans by request, newest last
PLAN_CACHE = 64


# ------------------------------------------------------------------ models ---
class Profile(BaseModel):
    sex: Literal["male", "female"]
    age: int = Field(ge=18, le=100)
    height_in: float = Field(ge=48, le=90)
    weight_lb: float = Field(ge=80, le=600)
    body_fat_pct: Optional[float] = Field(default=None, ge=3, le=70)
    waist_in: Optional[float] = Field(default=None, ge=20, le=70)   # estimates body fat when body_fat_pct is blank
    waist_kind: Literal["pants", "tape"] = "pants"                  # pant-tag size or tape-measured waist
    activity: Literal["sedentary", "light", "moderate", "very", "extreme"]
    deficit_pct: float = Field(ge=-15, le=30)


class Liked(BaseModel):
    fdc_id: int
    name: Optional[str] = Field(default=None, max_length=60)


class PlanRequest(BaseModel):
    profile: Profile
    diet: Literal["omnivore", "pescatarian", "vegetarian", "vegan"] = "omnivore"
    avoid: list[Literal["dairy", "egg", "fish", "pork", "soy", "gluten", "tree_nut", "peanut"]] = []
    liked: list[Liked] = Field(default=[], max_length=30)
    rotate: list[str] = Field(default=[], max_length=80)   # foods in the previous plan: use different ones this time
    days: int = Field(default=4, ge=1, le=14)               # how many days the grocery basket covers


# ------------------------------------------------------------------- data ---
@lru_cache(maxsize=1)
def config():
    return optimize.load_config()


@lru_cache(maxsize=1)
def staple_cache():
    return json.loads((HERE / "data" / "foods.json").read_text(encoding="utf-8"))


def usda_food(fdc_id):
    with _cache_lock:
        cache = json.loads(USDA_CACHE.read_text(encoding="utf-8")) if USDA_CACHE.exists() else {}
    if cache.get(str(fdc_id), {}).get("amino"):
        return cache[str(fdc_id)]
    food = {**usda.food(fdc_id), "amino": True}   # entries cached before amino acids were tracked are fetched again
    with _cache_lock:
        cache = json.loads(USDA_CACHE.read_text(encoding="utf-8")) if USDA_CACHE.exists() else {}
        cache[str(fdc_id)] = food
        USDA_CACHE.parent.mkdir(exist_ok=True)
        USDA_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return food


LEAD_CLASS = {"crustaceans", "mollusks", "fish", "nuts", "seeds", "cereals", "beef", "pork", "lamb", "veal", "game meat"}
TAIL_CLASS = {"sauce", "soup", "cheese", "oil", "juice", "milk", "yogurt", "bread", "butter", "beans"}


def short_name(description):
    """'Crustaceans, shrimp, cooked' -> 'Shrimp'; 'Sauce, pizza, canned' -> 'Pizza sauce';
    'Chicken, broilers or fryers, thigh, meat only' -> 'Chicken'."""
    parts = [p.strip() for p in re.sub(r"\s*\([^)]*\)", "", description).split(",")]
    parts = [p for p in parts if p]
    first = parts[0].lower()
    second = parts[1] if len(parts) > 1 else ""
    if second and first in LEAD_CLASS:
        name = second
    elif second and first in TAIL_CLASS:
        name = f"{second} {first}"
    else:
        name = parts[0]
    return (name[:1].upper() + name[1:])[:60]


def liked_food(item, ep):
    f = usda_food(item.fdc_id)
    kcal100 = f["per_100g"].get("energy", 0.0)
    grams, label = usda.pick_serving(f.get("portions", []), kcal100)
    scale = grams / 100.0
    serving_kcal = kcal100 * scale
    food = {
        "name": short_name(f["description"]),
        "fdc_id": f["fdc_id"], "description": f["description"], "serving_g": grams, "label": label,
        # at most ~600 kcal of any one liked food a day
        "max_servings": max(1, min(4, round(600 / serving_kcal))) if serving_kcal > 0 else 4,
        "missing": [k for k in NUTRIENTS if k not in f["per_100g"]],
        "source": f"USDA {f['fdc_id']}", "epicure": ep.match(f["description"]),
        "cost": LIKED_COST, "liked": True, "min_units": 1,
    }
    food["contains"] = catalog.extra_tags(food)
    food["per_serving"] = contaminants.add(aminos.digest({k: v * scale for k, v in f["per_100g"].items()}, food), food)
    return food


ROTATE_COST = 3.0   # extra food cost for a staple that was in the previous plan (catalog foods from it are left out)
SEVERITY = {"ok": 0, "below goal": 1, "LOW": 2, "HIGH": 2}


def solve_with_liked(foods, liked, targets, step):
    """Solve once with liked foods as a mild preference. Nutrient targets come first, so a liked food the
    plan can do without is left out, and the notes say so."""
    limit = min(25, 8 + 1.5 * len(liked))
    try:
        alloc, totals = planner.solve_day(foods, targets, step, time_limit=limit)
    except RuntimeError as e:
        raise HTTPException(422, str(e))
    used = {id(f) for f, a in zip(foods, alloc) if a.sum() > 0}
    left_out = [f["name"] for f in liked if id(f) not in used and any(f["allowed"])]
    notes = [f"{', '.join(left_out)} left out: using {'it' if len(left_out) == 1 else 'them'} would have cost "
             f"nutrient coverage or broken a meal's shape."] if left_out else []
    return alloc, totals, notes


# ----------------------------------------------------------------- routes ---
@app.get("/")
def index():
    return FileResponse(HERE / "docs" / "index.html")


@app.get("/config.js")
def page_config():
    return FileResponse(HERE / "docs" / "config.js", media_type="text/javascript")


# USDA files these under a different name ("Cereals, oats, ... cooked").
SEARCH_SYNONYMS = {"oatmeal": "oats", "porridge": "oats", "garbanzo": "chickpeas", "garbanzos": "chickpeas",
                   "yoghurt": "yogurt", "aubergine": "eggplant", "courgette": "zucchini"}


@lru_cache(maxsize=512)
def _search(q):
    q = " ".join(SEARCH_SYNONYMS.get(w, w) for w in q.split())
    hits = usda.rank(q, usda.search(q, page_size=50))[:15]
    return [{"fdc_id": h["fdcId"], "description": h["description"], "data_type": h.get("dataType", "")}
            for h in hits]


@app.get("/api/search")
def search(q: str = Query(min_length=2, max_length=80)):
    try:
        return _search(usda.clean_query(q).lower())
    except usda.USDAError as e:
        raise HTTPException(502, str(e))


@app.get("/api/staples")
def staples():
    return [{"name": f["name"], "label": f["label"], "contains": f.get("contains", []),
             "meals": f.get("meals", [])} for f in config()["foods"]]


@app.post("/api/plan")
def plan(req: PlanRequest):
    key = req.model_dump_json()
    with _cache_lock:
        if key in _plans:
            return _plans[key]
    result = build_plan(req)
    with _cache_lock:
        _plans[key] = result
        while len(_plans) > PLAN_CACHE:
            _plans.pop(next(iter(_plans)))
    return result


def build_plan(req):
    cfg, ep = config(), epicure.load()
    profile = req.profile.model_dump()
    targets, info = optimize.all_targets(profile, cfg, req.diet)
    if req.diet in ("omnivore", "pescatarian"):
        # Pushing iron toward the upper limit is for plant-based eaters: non-heme iron is absorbed less
        # as stores fill. Meat and fish add heme iron, which isn't regulated that way.
        targets["iron"] = {k: v for k, v in targets["iron"].items() if k not in ("push", "weight")}

    try:
        liked = [liked_food(item, ep) for item in req.liked]
    except usda.USDAError as e:
        raise HTTPException(502, str(e))
    not_aldi = [f["name"] for f in liked if not aldi.allowed(f)]
    liked = [f for f in liked if aldi.allowed(f)]
    excluded = DIET_EXCLUDES[req.diet] | set(req.avoid)
    avoided = [f["name"] for f in liked if excluded & set(f["contains"])]
    liked = [f for f in liked if not excluded & set(f["contains"])]
    liked_ids = {f["fdc_id"] for f in liked}
    staple_cfg = {**cfg, "foods": [f for f in cfg["foods"]
                                   if not excluded & set(f.get("contains", [])) and aldi.allowed(f)
                                   and f.get("fdc_id") not in liked_ids]}
    staples_, _ = optimize.load_foods(staple_cfg, staple_cache(), set())
    staples_ = [f for f in staples_ if staple_cache().get(f["name"], {}).get("fdc_id") not in liked_ids]
    liked_names = {f["name"].lower() for f in liked}
    staples_ = [f for f in staples_ if f["name"].lower() not in liked_names]   # "Broccoli" liked replaces the Broccoli staple
    for f in staples_:
        if f["name"] in req.rotate and not f.get("always"):
            f["cost"] = f.get("cost", 1.0) + ROTATE_COST
    roles.annotate(liked + staples_, ep)
    core_ids = {v.get("fdc_id") for v in staple_cache().values()}
    extra = catalog.shortlist(catalog.available(excluded, liked_ids | core_ids, {f["name"] for f in cfg["foods"]} | set(req.rotate), staples_),
                              targets)
    foods = liked + staples_ + extra
    step = cfg.get("settings", {}).get("serving_step", 0.5)
    with _solve_slots:
        alloc, totals, forced = solve_with_liked(foods, liked, targets, step)
        if avoided:
            forced = [f"{', '.join(avoided)} not used: on your avoid list."] + forced
        if not_aldi:
            forced = [f"{', '.join(not_aldi)} not used: Aldi doesn't sell {'it' if len(not_aldi) == 1 else 'them'}."] + forced
    used = [(f, float(a.sum())) for f, a in zip(foods, alloc) if a.sum() > 0]
    day = meals.build(foods, alloc)
    grocery = basket.build(foods, alloc, req.days)

    return {
        "numbers": {"maintenance": info["tdee"], "bmr": info["bmr"], "kcal": info["kcal"],
                    "lb_per_week": info["lb_per_week"], "lbm_lb": info["lbm_kg"] * optimize.LB_PER_KG,
                    "lbm_source": info["lbm_source"], "protein": targets["protein"]},
        "meals": day["meals"], "spread": day["spread"], "basket": grocery,
        "nutrients": optimize.nutrient_rows(totals, targets),
        "gaps": optimize.data_gaps(used, totals, targets),
        "liked": [{"fdc_id": f["fdc_id"], "name": f["name"], "description": f["description"],
                   "serving": f"{f['label']} ({f['serving_g']:.0f} g)", "pairs_as": f["epicure"],
                   "used": any(f is g for g, _ in used), "forced": bool(f.get("added"))} for f in liked],
        "food_count": len(used),
        "forced": forced,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", help="text file holding your USDA FoodData Central API key")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))      # 0.0.0.0 in the Docker image
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    args = ap.parse_args()
    if args.key_file:
        os.environ["FDC_API_KEY_FILE"] = str(Path(args.key_file).resolve())
    if usda.api_key() == "DEMO_KEY":
        print("No USDA key set; using DEMO_KEY (about 30 requests/hour). Pass --key-file.")
    epicure.load()  # download / warm the embeddings before the first request
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
