"""Design a daily diet for fat loss + muscle gain from your foods.

1. Calories, protein, fat and fiber are calculated from your body stats and
   goal (Mifflin-St Jeor maintenance, minus a deficit; protein per kg of lean
   body mass, measured from body fat % or estimated with the Boer formula).
2. Vitamins and minerals get three tiers: min (RDA), goal (higher end for
   training), max (safely under the upper limit).
3. A mixed-integer linear program (planner.py) picks half-serving portions
   AND which meal each goes in, so every meal is built right: a protein main
   with vegetables and a starch at lunch and dinner, a grain or fruit base at
   breakfast. Epicure's mode atlas tells sweet breakfast foods from savory
   dinner ones (roles.py). It then uses as few foods as it can.

Targets are soft: missing them costs a penalty, so the solver always returns
the best plan available and shows exactly what it couldn't meet.

Usage:
    python optimize.py
    python optimize.py --max-foods 8
    python optimize.py --exclude Salmon --require "Greek yogurt" Eggs
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import yaml

import aminos
import contaminants
import dri
from nutrients import NUTRIENTS

HERE = Path(__file__).parent
LB_PER_KG = 2.20462
ACTIVITY = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "very": 1.725, "extreme": 1.9}

GOAL_TOLERANCE = 0.98     # a goal counts as met within 2% (half-servings can't land exactly)


def load_config(path=HERE / "config.yaml"):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- targets ---
PANT_TAG_EXTRA_IN = 1.0   # a pant tag runs about an inch under the real waist (vanity sizing)
BODY_FAT_RANGE = (5.0, 55.0)


def waist_body_fat(p):
    """(body fat %, measured waist in) from waist and height: relative fat mass, Woolcott & Bergman 2018
    (Sci Rep 8:10980), fitted to DXA body fat in NHANES: 64 - 20 x height / waist + 12 x (1 if female).
    A pant-tag size gets PANT_TAG_EXTRA_IN added, a tape measure at the navel does not."""
    waist = float(p["waist_in"]) + (PANT_TAG_EXTRA_IN if p.get("waist_kind", "pants") == "pants" else 0.0)
    bf = 64 - 20 * p["height_in"] / waist + (12 if p["sex"] == "female" else 0)
    return min(max(bf, BODY_FAT_RANGE[0]), BODY_FAT_RANGE[1]), waist


def lean_mass_kg(p):
    """(kg, source). Body fat % if given, else estimated from waist and height, else the Boer (1984) formula."""
    kg = p["weight_lb"] / LB_PER_KG
    if p.get("body_fat_pct"):
        return kg * (1 - float(p["body_fat_pct"]) / 100), "from body fat %"
    if p.get("waist_in"):
        bf, waist = waist_body_fat(p)
        return kg * (1 - bf / 100), f"estimated from waist ({waist:.0f} in, about {bf:.0f}% body fat)"
    cm = p["height_in"] * 2.54
    if p["sex"] == "male":
        return 0.407 * kg + 0.267 * cm - 19.2, "estimated (Boer formula)"
    return 0.252 * kg + 0.473 * cm - 48.3, "estimated (Boer formula)"


def body_targets(profile, goal, doctor_limits=None):
    """Calories, protein, fat, fiber from profile + goal settings."""
    p, g = profile, goal
    kg = p["weight_lb"] / LB_PER_KG
    cm = p["height_in"] * 2.54
    bmr = 10 * kg + 6.25 * cm - 5 * p["age"] + (5 if p["sex"] == "male" else -161)
    tdee = bmr * ACTIVITY[p["activity"]]
    kcal = tdee * (1 - p["deficit_pct"] / 100)

    lbm, lbm_source = lean_mass_kg(p)
    pr = {k: v * lbm for k, v in g["protein_g_per_kg_lean_mass"].items()}
    dl = doctor_limits or {}
    capped = dl.get("enabled") and dl.get("protein_max_g")
    if capped:
        cap = dl["protein_max_g"]
        pr = {k: min(v, cap) for k, v in pr.items()}

    fat = g["fat_pct_of_calories"]
    fiber_ai = dri.fiber_ai(p["sex"], p["age"])
    targets = {
        "energy":  {"min": kcal * 0.96, "goal": kcal, "max": kcal, "weight": 5},   # land on the target, never over
        "protein": {**pr, "push": pr["max"], "weight": 5},                       # as high as the evidence range allows
        "fat":     {"min": kcal * fat["min"] / 100 / 9, "max": kcal * fat["max"] / 100 / 9, "weight": 2},
        "fiber":   {"min": fiber_ai, "goal": max(fiber_ai, kcal / 1000 * g["fiber_g_per_1000_kcal"])},   # AI: 38 g men / 25 g women under 51
    }
    info = {"bmr": bmr, "tdee": tdee, "kcal": kcal, "lb_per_week": (tdee - kcal) * 7 / 3500,
            "lbm_kg": lbm, "lbm_source": lbm_source, "protein_capped": bool(capped)}
    return targets, info


def all_targets(profile, cfg, diet="omnivore"):
    """Every daily target for one person: body (calories, protein, fat, fiber), vitamins and minerals
    by sex / age / diet (dri.py), and the essential amino acids (aminos.py)."""
    body, info = body_targets(profile, cfg["goal"], cfg.get("doctor_limits"))
    micro = dri.nutrient_targets(cfg["targets"], profile["sex"], profile["age"], diet)
    kg = profile["weight_lb"] / LB_PER_KG
    amino = aminos.targets(kg, body["protein"]["goal"])
    return {**body, **micro, **amino, **contaminants.targets(kg)}, info


# ------------------------------------------------------------------ foods ---
def load_foods(cfg, cache, exclude):
    """Return foods with nutrients per serving, plus a list of missing-data notes."""
    foods, problems = [], []
    for f in cfg["foods"]:
        name = f["name"]
        if name in exclude:
            continue
        if f.get("custom"):
            per_serving = {k: float(v) for k, v in f.get("per_serving", {}).items()}
            missing = [k for k in NUTRIENTS if k not in per_serving]
            source = "custom (label)"
        else:
            entry = cache.get(name)
            if entry is None:
                problems.append(f"{name}: no USDA data cached — run fetch_foods.py")
                continue
            scale = f["serving_g"] / 100.0
            per_serving = {k: v * scale for k, v in entry["per_100g"].items()}
            missing = entry.get("missing", [])
            source = f"USDA {entry['fdc_id']}"
        food = {**f, "description": "" if f.get("custom") else entry["description"]}
        food["per_serving"] = contaminants.add(aminos.digest(per_serving, food), food)   # amino acids count as digested (aminos.py)
        foods.append({**food, "missing": missing, "source": source})
    return foods, problems


# ----------------------------------------------------------------- report ---
def status(total, t):
    if "min" in t and total < t["min"] * 0.995:
        return "LOW"
    if "max" in t and total > t["max"] * 1.005:
        return "HIGH"
    if "goal" in t and total < t["goal"] * GOAL_TOLERANCE:
        return "below goal"
    return "ok"


def nutrient_rows(totals, targets):
    rows = []
    for k, (label, unit, _) in NUTRIENTS.items():
        t = targets.get(k, {})
        rows.append({"key": k, "label": label, "unit": unit, "amount": totals[k],
                     **{c: t[c] for c in ("min", "goal", "max") if c in t},
                     "status": status(totals[k], t) if t else ""})
    return rows


def data_gaps(used, totals, targets):
    """A nutrient may only *look* short because USDA lacks data for a food in the plan."""
    notes = []
    for k, t in targets.items():
        if status(totals[k], t) in ("LOW", "below goal"):
            gaps = [f["name"] for f, s in used if k in f["missing"] and not f.get("seasoning")]
            if gaps:
                notes.append(f"{NUTRIENTS[k][0]}: no data for {', '.join(gaps)} — real intake may be higher")
            elif k not in ("vit_b12", "vit_d") or status(totals[k], t) == "below goal":
                label, unit, _ = NUTRIENTS[k]
                want = t["min"] if status(totals[k], t) == "LOW" else t["goal"]
                notes.append(f"{label}: {fmt(totals[k])} of {fmt(want)} {unit} ({totals[k] / want:.0%}). This food list "
                             f"and calorie target don't go further; adding liked foods rich in it can close the gap.")
    if status(totals["vit_b12"], targets.get("vit_b12", {})) == "LOW":
        notes.append("Vitamin B12: plant foods don't provide it. Take a B12 supplement or use fortified foods.")
    if status(totals["vit_d"], targets.get("vit_d", {})) == "LOW":
        notes.append("Vitamin D: hard to get from food. Sun exposure or a supplement covers it; ask your doctor to test your level.")
    return notes


def fmt(x):
    return f"{x:,.0f}" if abs(x) >= 100 else f"{x:,.1f}" if abs(x) >= 10 else f"{x:.2f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=HERE / "config.yaml")
    ap.add_argument("--foods-data", default=HERE / "data" / "foods.json")
    ap.add_argument("--max-foods", type=int)
    ap.add_argument("--exclude", nargs="*", default=[], metavar="FOOD")
    ap.add_argument("--require", nargs="*", default=[], metavar="FOOD")
    args = ap.parse_args()

    import epicure, meals, planner, roles  # Epicure data loads lazily; keep out of module import

    cfg = load_config(args.config)
    cache_path = Path(args.foods_data)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    targets, info = all_targets(cfg["profile"], cfg)
    settings = cfg.get("settings", {})
    max_foods = args.max_foods or settings.get("max_foods")
    step = settings.get("serving_step", 0.5)

    names = {f["name"] for f in cfg["foods"]}
    for n in args.exclude + args.require:
        if n not in names:
            sys.exit(f"Unknown food '{n}'. Names in config: {', '.join(sorted(names))}")

    foods, problems = load_foods(cfg, cache, set(args.exclude))
    for p in problems:
        print(f"! {p}")
    if not foods:
        sys.exit("No foods with data. Run: python fetch_foods.py")

    print("\nYOUR NUMBERS\n" + "-" * 62)
    print(f"Maintenance ≈ {info['tdee']:,.0f} kcal/day (BMR {info['bmr']:,.0f} × activity)")
    print(f"Target      ≈ {info['kcal']:,.0f} kcal/day → about {info['lb_per_week']:.1f} lb fat loss/week")
    print(f"Lean mass   ≈ {info['lbm_kg']:.0f} kg ({info['lbm_kg'] * LB_PER_KG:.0f} lb), {info['lbm_source']}")
    pr = targets["protein"]
    cap_note = "  (capped by doctor_limits)" if info["protein_capped"] else ""
    print(f"Protein       {pr['min']:.0f}–{pr['max']:.0f} g, aiming for {pr['goal']:.0f} g{cap_note}")

    roles.annotate(foods, epicure.load())
    try:
        alloc, totals = planner.solve_day(foods, targets, step, set(args.require), max_foods)
    except RuntimeError as e:
        sys.exit(str(e))
    used = [(f, float(a.sum())) for f, a in zip(foods, alloc) if a.sum() > 0]

    day = meals.build(foods, alloc)
    print(f"\nMEALS  ({len(used)} foods)\n" + "-" * 62)
    for meal in day["meals"]:
        print(f"{meal['name'].upper():<10} {meal['kcal']:>5.0f} kcal  {meal['protein']:>4.0f} g protein")
        for item in meal["items"]:
            print(f"  {item['name']:<22} {item['servings']:>4g} × {item['label']:<18} {item['grams']:>5.0f} g")
        if meal["seasonings"]:
            tips = ", ".join(s["label"] + (" (salty)" if s["high_sodium"] else "") for s in meal["seasonings"])
            print(f"  season with: {tips}")
    for s in day["spread"]:
        print(f"Across meals: {s['servings']:g} × {s['label']} {s['name'].lower()}")

    print("\nNUTRIENTS\n" + "-" * 62)
    print(f"{'':<20} {'amount':>12}   {'min':>7} {'goal':>7} {'max':>7}")
    rows = nutrient_rows(totals, targets)
    for r in rows:
        cols = " ".join(f"{fmt(r[c]):>7}" if c in r else f"{'':>7}" for c in ("min", "goal", "max"))
        flag = {"LOW": "  ← low", "HIGH": "  ← over", "below goal": "  · under goal"}.get(r["status"], "")
        print(f"{r['label']:<20} {fmt(r['amount']):>7} {r['unit']:<4}   {cols}{flag}")

    notes = data_gaps(used, totals, targets)
    if notes:
        print("\nDATA GAPS\n" + "-" * 62)
        for n in notes:
            print(f"• {n}")

    print("\nTIPS\n" + "-" * 62)
    print("• Have one of the meals within a few hours of lifting.")
    print("• Weigh in a few mornings a week and update weight_lb every 2 weeks. If the weekly")
    print("  average stalls for 2-3 weeks, raise deficit_pct by ~3; if you lose more than ~1%")
    print("  of body weight a week or strength drops, lower it.")
    print("• Iodine isn't in the USDA data — iodized salt, eggs, and dairy cover it.")

    out = HERE / "output"
    out.mkdir(exist_ok=True)
    with open(out / "plan.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["meal", "food", "servings", "serving", "grams", "protein_g", "source"])
        for meal in day["meals"]:
            for item in meal["items"]:
                w.writerow([meal["name"], item["name"], item["servings"], item["label"], round(item["grams"]),
                            round(item["protein"], 1), item["source"]])
        for s in day["spread"]:
            w.writerow(["all meals", s["name"], s["servings"], s["label"], round(s["grams"]), 0, s["source"]])
    with open(out / "nutrients.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["nutrient", "unit", "amount", "min", "goal", "max", "status"])
        for r in rows:
            w.writerow([r["label"], r["unit"], round(r["amount"], 2),
                        *(round(r[c], 2) if c in r else "" for c in ("min", "goal", "max")), r["status"]])
    print("\nSaved output/plan.csv and output/nutrients.csv")


if __name__ == "__main__":
    main()
