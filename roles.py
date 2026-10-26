"""What each food does in a meal, and which meals it belongs in.

Role (what it is on the plate) comes from USDA macros plus the Epicure
food-group clusters. Meal fit comes from the Epicure mode atlas: ingredients
whose clusters read "sweet baking and dessert" belong at breakfast, ones in
"savory meal" clusters belong at lunch and dinner.

    protein  eggs, yogurt, fish, meat, tofu, legumes, whey
    starch   grains, bread, cereal, potatoes
    fruit    fruit and juice
    veg      vegetables and mushrooms
    fat      nuts, seeds, oils, avocado
    drink    milk and plant milks
    other
"""
import re

import recipes

MEALS = ["breakfast", "lunch", "dinner"]

FAT_SHARE = 0.55      # kcal share from fat
PROTEIN_SHARE = 0.24  # kcal share from protein (and >= 7 g / 100 g)
CARB_SHARE = 0.65

# A sweet/savory score this far from a meal's flavor is allowed but costs;
# past the hard limit the food can't go in that meal at all.
BREAKFAST_SAVORY_BAN = 0.4    # savory proteins (lentils, tofu, fish, chicken) never at breakfast
DINNER_SWEET_BAN = 0.85       # sweet grains, drinks and sweet "other" (oats, soy milk) never at lunch/dinner
BREAKFAST_VEG = re.compile(r"spinach|kale|tomato|pepper|onion|scallion|chard|zucchini", re.I)   # the vegetables people put in breakfast
SAVORY_KINDS = {"legume", "tofu", "fish", "canned_fish", "meat", "poultry"}
FIT_COST = 5.0                # penalty points per unit of flavor mismatch
DINNER_SWEET_FACTOR = 1.5     # a sweet food is a worse fit at dinner than at lunch


def classify(f, ep):
    """Role + meal rules for one food (config entry with per_serving)."""
    ps, grams = f["per_serving"], f["serving_g"]
    kcal = ps.get("energy", 0.0)
    protein, fat, carbs = (ps.get(k, 0.0) for k in ("protein", "fat", "carbs"))
    per100 = lambda x: x / grams * 100
    share = lambda g, kcal_per_g: g * kcal_per_g / kcal if kcal > 0 else 0.0
    groups = ep.food_groups(f["epicure"]) if f.get("epicure") else set()
    sweet = ep.sweetness(f["epicure"]) if f.get("epicure") else None

    if f.get("seasoning"):
        role = "seasoning"
    elif share(fat, 9) >= FAT_SHARE and share(protein, 4) < PROTEIN_SHARE:
        role = "fat"
    elif per100(protein) >= 7 and share(protein, 4) >= PROTEIN_SHARE:
        role = "protein"
    elif "Fruit" in groups or (share(carbs, 4) >= 0.85 and per100(kcal) < 70 and (sweet or 0) >= 0.9):
        role = "fruit"
    elif grams >= 180 and per100(kcal) < 70 and "Vegetable" not in groups:
        role = "drink"
    elif "Vegetable" in groups or per100(kcal) < 50:
        role = "veg"
    elif share(carbs, 4) >= CARB_SHARE and per100(kcal) >= 70:
        role = "starch"
    else:
        role = "other"

    if role == "fruit" and f.get("group") == "veg":
        role = "veg"                                  # pumpkin flowers, tomatoes: Epicure calls them fruit, USDA says vegetable
    allowed = [True, True, True]
    fit = [0.0, 0.0, 0.0]
    if role != "seasoning" and sweet is not None:
        fit[0] = FIT_COST * (1 - sweet)               # savory foods are worse at breakfast
        fit[1] = FIT_COST * sweet                     # sweet foods are worse at lunch / dinner
        fit[2] = FIT_COST * sweet * DINNER_SWEET_FACTOR
        if role == "protein" and sweet <= BREAKFAST_SAVORY_BAN:
            allowed[0] = False
        if role in ("starch", "drink", "other") and sweet >= DINNER_SWEET_BAN:
            allowed[1] = allowed[2] = False
    if role != "seasoning" and not f.get("liked") and recipes.kind({**f, "role": role}) in SAVORY_KINDS:
        allowed[0] = False                            # beans, tofu, fish and meat are lunch / dinner foods
    if role == "veg" and not BREAKFAST_VEG.search(f["name"]):
        allowed[0] = False
    if role == "seasoning":
        allowed = [True, False, False]                # one variable, reported "across meals"
    return {"role": role, "sweet": sweet, "allowed": allowed, "fit": fit}


def annotate(foods, ep):
    for f in foods:
        f.update(classify(f, ep))
    return foods
