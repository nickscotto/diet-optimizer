"""Cooking steps for each meal, from templates keyed by what kind of food it is.

The planner decides foods and amounts; this only says how to prepare exactly
those foods. It doesn't change a single gram, so the nutrients stay as solved.
Kind comes from the food's name / USDA description, with the role as fallback.
"""
import re

# (kind, pattern on "name description"), first match wins.
KINDS = [
    ("egg", r"\begg(?!plant)"),
    ("canned_fish", r"sardine|tuna|anchov|canned.*(?:fish|salmon)|(?:fish|salmon).*canned"),
    ("fish", r"salmon|trout|\bfish\b|\bcod\b|tilapia|shrimp|crab|lobster|scallop|haddock|halibut|mackerel"),
    ("poultry", r"chicken|turkey|\bduck\b"),
    ("meat", r"beef|pork|lamb|steak|veal|bison|\bham\b|bacon|venison"),
    ("tofu", r"tofu|tempeh|seitan"),
    ("legume", r"lentil|bean|chickpea|garbanzo|edamame|\bpeas?\b|cowpea|pigeonpea|dal\b"),
    ("oats", r"\boats?\b|oatmeal"),
    ("pasta", r"pasta|spaghetti|macaroni|noodle"),
    ("grain", r"\brice\b|quinoa|barley|couscous|bulgur|farro|millet|buckwheat|grits|polenta"),
    ("bread", r"bread|bagel|tortilla|toast|muffin|\bpita\b"),
    ("cereal", r"cereal|cheerios|granola|muesli"),
    ("potato", r"potato|\byam\b"),
    ("yogurt", r"yogurt|yoghurt|kefir|cottage"),
    ("cheese", r"cheese"),
    ("whey", r"whey|protein powder|protein isolate"),
    ("juice", r"juice"),
    ("milk", r"\bmilk\b|soymilk"),
    ("nut_butter", r"(?:peanut|almond|nut|seed) butter"),
    ("nuts", r"\bnuts?\b|almond|walnut|cashew|pistachio|pecan|seeds?\b"),
    ("oil", r"\boil\b|olive|\bbutter\b|\bghee\b"),
    ("avocado", r"avocado"),
    ("pickled", r"sauerkraut|kimchi|pickle"),
    ("mushroom", r"mushroom"),
    ("leafy", r"spinach|kale|lettuce|arugula|chard|collard|romaine"),
]

STEP = {
    "egg": "Scramble, fry or soft-boil {x} until the whites are set.",
    "canned_fish": "Drain {x} and flake it with a fork.",
    "fish": "Bake {x} at 400°F (200°C) for 10–15 minutes, or pan-sear, until opaque and it flakes easily.",
    "poultry": "Bake, grill or pan-sear {x} until cooked through (165°F / 74°C inside), then slice.",
    "meat": "Grill or pan-sear {x} to your liking (ground meat to 160°F / 71°C) and rest it for 3 minutes.",
    "tofu": "Press, cube and pan-fry or bake {x} until golden.",
    "legume": "Warm {x} in a pot or the microwave (rinse first if canned).",
    "oats": "Simmer {x} with water or milk for about 5 minutes, stirring.",
    "pasta": "Boil {x} as the package directs and drain.",
    "grain": "Cook {x} as the package directs (or reheat it if already cooked).",
    "bread": "Toast {x}.",
    "cereal": "Pour {x} into a bowl.",
    "potato": "Bake {x} at 425°F (220°C) for about 45 minutes, or microwave it until soft.",
    "yogurt": "Spoon {x} into a bowl.",
    "cheese": "Slice, grate or melt {x}.",
    "whey": "Shake or whisk {x} into water or milk until smooth.",
    "juice": "Pour {x}.",
    "milk": "Pour {x}.",
    "nut_butter": "Spread or stir in {x}.",
    "nuts": "Sprinkle {x} on top.",
    "oil": "Use {x} to cook the vegetables or drizzle on top.",
    "avocado": "Slice {x}.",
    "pickled": "Serve {x} on the side.",
    "mushroom": "Slice {x} and sauté until browned, 5–7 minutes.",
    "leafy": "Wilt {x} in a hot pan with a splash of water or oil for about 2 minutes.",
    "veg": "Steam, roast (425°F / 220°C, 20 minutes) or sauté {x} until tender-crisp.",
    "fruit": "Wash and slice {x}.",
    "protein": "Cook {x} until done, or heat it through if it is ready to eat.",
    "starch": "Prepare {x} as the package directs.",
    "drink": "Pour {x}.",
    "fat": "Add {x} on top.",
    "other": "Add {x}.",
}

# Longest cooking first, cold and finishing items last.
ORDER = ["potato", "grain", "pasta", "oats", "legume", "meat", "poultry", "fish", "tofu", "egg", "veg",
         "mushroom", "leafy", "bread", "canned_fish", "protein", "starch", "yogurt", "cereal", "whey", "cheese",
         "fruit", "pickled", "avocado", "nuts", "nut_butter", "oil", "fat", "other", "milk", "juice", "drink"]


# Plant foods whose USDA weight is the cooked weight: the step says so, instead of "cook it" with no amount.
COOKED_BASIS = {"legume", "grain", "pasta", "potato", "veg", "leafy", "mushroom"}


def display_name(name):
    """'White Beans, mature seeds (cooked)' -> 'White Beans'; 'Tofu, soft, prepared with calcium sulfate' -> 'Tofu, soft'."""
    name = re.sub(r"\s*\((?:cooked|raw)\)", "", name)
    name = re.sub(r",\s*(?:im)?mature(?: seeds| cooked)?\b", "", name)
    name = re.split(r",? (?:prepared|made) with", name)[0]
    return name.strip(" ,")


def tidy_label(label):
    return re.sub(r"\s+yields?", "", label)


def cooked_basis(food):
    text = f"{food.get('name', '')} {food.get('description', '')}".lower()
    return kind(food) in COOKED_BASIS and bool(re.search(r"cooked|boiled|steamed", text))


def kind(food):
    text = f"{food.get('name', '')} {food.get('description', '')} {food.get('query', '')}".lower()
    for k, pattern in KINDS:
        if re.search(pattern, text):
            return k
    return food.get("role", "other")


def steps(meal_name, items, seasonings):
    """items need name, grams, kind (see kind()). Returns a list of instruction strings."""
    out = []
    for it in sorted(items, key=lambda it: ORDER.index(it["kind"]) if it["kind"] in ORDER else len(ORDER)):
        grams = int(it["grams"] + 0.5)   # round half up, same as the page
        x = f"{it['name']} ({grams} g{' cooked' if it.get('cooked') else ''})"
        out.append(STEP.get(it["kind"], STEP["other"]).format(x=x))
    if items:
        out.append("Put everything on one plate or in one bowl." if meal_name.lower() != "breakfast"
                   else "Combine in a bowl or on a plate.")
    if seasonings:
        tips = ", ".join(s["label"] for s in seasonings)
        out.append(f"Season to taste: {tips}.")
    return out
