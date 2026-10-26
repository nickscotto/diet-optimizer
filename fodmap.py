"""Low-FODMAP mode: only Aldi food types that Monash University rates low FODMAP, with daily gram caps on the ones
that turn moderate in bigger servings. The values are from memory of the Monash app, so check them against the
current app (it revises ratings) before relying on them for a medical elimination diet. Anything not listed
here, including foods you add yourself, is left out."""
import aldi

# type label -> most grams per day (None = no FODMAP-driven limit). Caps are about two Monash servings,
# one per sitting; where Monash gives one serving per sitting, the cap is below that.
LOW = {
    # vegetables
    "carrots": None, "spinach": 150, "kale": 150, "lettuce": None, "green beans": 150, "cucumber": None,
    "potatoes": None, "broccoli": 150, "bell peppers": 150, "zucchini": 130, "tomatoes": 200, "avocado": 60,
    # fruit
    "oranges": 260, "kiwi": 300, "strawberries": 130, "blueberries": 80, "raspberries": 120, "pineapple": 280,
    "cantaloupe": 240, "grapes": 150, "orange juice": 125,
    # meat, fish, eggs (no carbohydrate, so no FODMAPs when plain)
    "ground beef": None, "beef steak": None, "beef roast": None, "chicken breast": None, "chicken thigh": None,
    "turkey breast": None, "ground turkey": None, "pork loin": None, "ground pork": None, "salmon": None,
    "tilapia": None, "cod": None, "haddock": None, "pollock": None, "catfish": None, "rainbow trout": None,
    "shrimp": None, "canned tuna": None, "canned salmon": None, "sardines": None, "eggs": None,
    # dairy: only hard, nearly lactose-free cheeses, small amounts
    "cheddar": 80, "parmesan": 60, "swiss cheese": 80, "mozzarella": 80,
    # plant protein
    "tofu": 340, "edamame": 180, "chickpeas": 85, "lentils": 90,
    # grains
    "oats": 104, "brown rice": None, "white rice": None, "quinoa": None,
    # nuts, seeds, fats
    "almonds": 30, "walnuts": 60, "peanuts": 64, "pecans": 40, "sunflower seeds": 30, "pumpkin seeds": 46,
    "peanut butter": 64, "almond butter": 20, "olive oil": None, "canola oil": None, "salt": None,
}


def allowed(food):
    return aldi.type_of(food) in LOW


def cap_g(food):
    return LOW.get(aldi.type_of(food))


def apply(foods):
    """The foods rated low FODMAP, each carrying its daily gram cap as max_g."""
    out = []
    for f in foods:
        if allowed(f):
            f = dict(f)
            cap = cap_g(f)
            if cap:
                f["max_g"] = cap
            out.append(f)
    return out
