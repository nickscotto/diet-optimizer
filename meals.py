"""Turn the planner's food-by-meal allocation into meals people can read.

Seasoning foods (salt) aren't part of any meal; they're reported "across meals".
Each meal gets a title built from its main foods, a few seasonings that
Epicure's recipe pairings rank highest for the meal's ingredients, and
cooking steps from recipes.py.
"""
import epicure
import recipes
from roles import MEALS

ROLE_ORDER = {"protein": 0, "starch": 1, "fruit": 2, "veg": 3, "drink": 4, "fat": 5, "other": 6}


def build(foods, alloc):
    """alloc[i][m] = servings of foods[i] in meal m."""
    ep = epicure.load()
    spread, meals = [], []
    for m, name in enumerate(MEALS):
        items = []
        for i, f in enumerate(foods):
            s = float(alloc[i][m])
            if s <= 0:
                continue
            item = _item(f, s)
            (spread if f["role"] == "seasoning" else items).append(item)
        meals.append(_meal(name, items, ep))
    return {"meals": meals, "spread": spread}


def _item(f, servings):
    return {"name": recipes.display_name(f["name"]), "key": f["name"], "servings": servings,
            "label": recipes.tidy_label(f["label"]), "role": f["role"], "cooked": recipes.cooked_basis(f),
            "grams": servings * f["serving_g"], "source": f["source"], "epicure": f.get("epicure"),
            "liked": bool(f.get("liked")), "kind": recipes.kind(f),
            "protein": f["per_serving"].get("protein", 0.0) * servings,
            "leucine": f["per_serving"].get("leu", 0.0) * servings,   # mg, as digested
            "kcal": f["per_serving"].get("energy", 0.0) * servings}


def _meal(name, items, ep):
    items.sort(key=lambda it: (ROLE_ORDER.get(it["role"], 9), -it["kcal"]))
    main = [it["name"] for it in items if it["role"] in ("protein", "starch", "fruit", "veg", "drink")][:3]
    title = main[0] if len(main) == 1 else ", ".join(main[:-1]) + " & " + main[-1] if main else ""
    seasonings = ep.seasonings([it["epicure"] for it in items if it["epicure"]])
    return {"name": name.capitalize(), "title": title, "items": items,
            "kcal": sum(it["kcal"] for it in items), "protein": sum(it["protein"] for it in items),
            "leucine_g": sum(it["leucine"] for it in items) / 1000,
            "seasonings": seasonings, "steps": recipes.steps(name, items, seasonings)}
