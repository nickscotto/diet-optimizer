"""Mercury, arsenic, lead and cadmium in each food, and the daily limits they are measured against.

Concentrations: FDA Total Diet Study FY2018-2020 (data/contaminants.json, build_contaminants.py), as eaten, µg per
100 g. Each food is matched to the nearest TDS food(s) by name (RULES, first match wins; several TDS foods are averaged).
Foods nothing matches use the average of their food group (GROUP_FALLBACK) and are listed in `fallbacks()`.

Mercury in fish and shellfish comes from FDA's species table instead (Mercury Levels in Commercial Fish and Shellfish
1990-2012, mean ppm), because TDS only samples a few fish. Mercury elsewhere is TDS.

Arsenic: the TDS value is total arsenic, most of which in fish and shellfish is the harmless organic form. The limit
below is for inorganic arsenic, so it is estimated: 70% of total in other foods (EFSA 2014 best average for foods other
than fish and seafood), and fixed at 30 ppb in fish and 100 ppb in shellfish (EFSA 2014).

Daily limits, per kg body weight (LIMITS):
  mercury  0.1 µg   EPA IRIS reference dose for methylmercury, 1e-4 mg/kg-day
  cadmium  0.357 µg EFSA tolerable weekly intake 2.5 µg/kg/week (2009, reaffirmed 2011)
  lead     0.5 µg   EFSA BMDL01 for developmental neurotoxicity (2010); no safe level exists
  arsenic  0.3 µg   EFSA BMDL01 for inorganic arsenic, low end of its 0.3-8 µg/kg/day range (2009)
Lead and arsenic have no threshold below which intake is risk-free; these are the reference points the regulators use.

Limits and concentrations are averages: a given fillet, rice crop or can varies several-fold.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).parent / "data" / "contaminants.json"

LIMITS = {"c_hg": 0.1, "c_cd": 2.5 / 7, "c_pb": 0.5, "c_as": 0.3}   # µg per kg body weight per day
KEYS = tuple(LIMITS)
SOFT = (0.15, 0.35)       # the penalty starts at 15% of the limit and steepens at 35%
INORGANIC_AS = 0.7
FISH_INORGANIC_AS, SHELLFISH_INORGANIC_AS = 3.0, 10.0    # µg per 100 g (30 and 100 ppb)

# FDA mean mercury, ppm (= µg/g; x100 for µg per 100 g). (pattern, ppm)
MERCURY_PPM = [
    (r"tuna.*(?:white|albacore)|(?:white|albacore).*tuna", 0.350), (r"tuna.*yellowfin", 0.354), (r"tuna", 0.126),
    (r"swordfish", 0.995), (r"king mackerel|mackerel, king", 0.73), (r"orange roughy|roughy", 0.571),
    (r"halibut", 0.241), (r"snapper", 0.166), (r"cod\b|lingcod", 0.111), (r"haddock", 0.055),
    (r"herring", 0.078), (r"pollock", 0.031), (r"catfish", 0.024), (r"tilapia", 0.013), (r"sardine", 0.013),
    (r"anchov", 0.016), (r"salmon", 0.018), (r"trout", 0.071), (r"mackerel", 0.050), (r"milkfish", 0.05),
    (r"sunfish|perch|bass", 0.15), (r"shrimp", 0.009), (r"crab", 0.065), (r"lobster", 0.093),
    (r"clam", 0.009), (r"oyster", 0.012), (r"mussel", 0.012), (r"squid", 0.024), (r"scallop", 0.003),
]
GENERIC_FISH_PPM = 0.10    # a fish nothing above matches

SHELLFISH = r"shrimp|crab|lobster|clam|oyster|mussel|squid|scallop"
FISH = r"salmon|trout|\bcod\b|lingcod|tilapia|catfish|tuna|haddock|halibut|herring|pollock|mackerel|sardine|anchov|snapper|swordfish|roughy|milkfish|sunfish|\bfish\b"

# (pattern on "name description", [TDS food names]); first match wins.
RULES = [
    (r"rice noodle", ["Pasta, rice noodles, cooked"]),
    (r"noodle|pasta|spaghetti|macaroni|couscous|soba|somen", ["Spaghetti, enriched, boiled", "Pasta, whole wheat, cooked"]),
    (r"\begg\b|\beggs\b", ["Eggs, hard-boiled"]),
    (r"tuna", ["Tuna, canned in water, drained"]),
    (r"salmon|trout|herring|mackerel|sardine|anchov", ["Salmon, steaks/fillets, baked"]),
    (r"cod\b|lingcod|haddock|halibut|pollock|snapper|swordfish|roughy|milkfish|sunfish|perch|bass",
     ["Cod, baked", "Tilapia, baked"]),
    (r"tilapia", ["Tilapia, baked"]),
    (r"catfish", ["Catfish, pan-cooked with oil"]),
    (SHELLFISH, ["Shrimp, pre-cooked, shells removed, no tails"]),
    (r"soy ?milk|soymilk", ["Beverage, soy (non-dairy)"]),
    (r"almond milk|coconut milk", ["Beverage, almond (non-dairy)"]),
    (r"cottage", ["Cottage cheese, creamed, reduced fat"]),
    (r"cream cheese", ["Cream cheese"]),
    (r"cheese|ricotta", ["Cheese, cheddar (sharp/mild)", "Cheese, Swiss", "Cheese, mozzarella"]),
    (r"yogurt|yoghurt|kefir", ["Yogurt, lowfat, vanilla", "Yogurt, lowfat, fruit-flavored"]),
    (r"\bmilk\b", ["Milk, skim, fluid", "Milk, whole, fluid"]),
    (r"whey|protein (?:powder|isolate)", ["Powder, protein"]),
    (r"chicken", ["Chicken breast, oven-roasted, skin removed", "Chicken thigh, oven-roasted, skin removed"]),
    (r"turkey", ["Turkey breast, oven-roasted", "Turkey, ground, pan-cooked"]),
    (r"pork|ham\b|bacon", ["Pork chop, pan-cooked with oil"]),
    (r"\blamb\b|goat|mutton", ["Lamb chop, pan-cooked with oil"]),
    (r"beef|bison|veal|steak", ["Beef, ground, pan-cooked", "Beef steak, loin/sirloin, oven-roasted"]),
    (r"tofu|tempeh|natto|edamame|soybean", ["Tofu, firm, plain, drained"]),
    (r"hummus|chickpea|garbanzo", ["Beans, garbanzo (chickpeas), canned, drained solids"]),
    (r"lentil", ["Lentils, dry, cooked"]),
    (r"lima", ["Lima beans, immature, frozen, boiled"]),
    (r"snap bean|green bean|yardlong|yellow bean(?!s, mature)", ["Green beans, fresh/frozen, boiled"]),
    (r"black bean|pinto|navy|white bean|kidney|cranberry bean|pink bean|bean", ["Beans, black, canned, drained solids",
     "Beans, kidney, canned, drained solids", "Beans, pinto, canned, drained solids", "Beans, white, canned, drained solids"]),
    (r"\bpeas?\b|pigeon|cowpea", ["Peas, green, frozen, boiled"]),
    (r"peanut butter", ["Peanut butter, creamy"]),
    (r"peanut", ["Peanuts, dry roasted, salted"]),
    (r"almond", ["Almonds, shelled"]),
    (r"walnut", ["Walnuts, shelled"]),
    (r"cashew", ["Cashews, salted"]),
    (r"hazelnut|filbert|pistachio|pecan|macadamia|brazil nut", ["Almonds, shelled", "Walnuts, shelled", "Cashews, salted"]),
    (r"beet|radish|okra|fennel|artichoke|turnip(?! greens)|parsnip|rutabaga", ["Carrot, baby, raw", "Cabbage, raw", "Potato, peeled, boiled"]),
    (r"pomegranate|persimmon", ["Apple, red, with peel, raw", "Pear, with peel, raw", "Peach, raw/frozen"]),
    (r"sunflower|pumpkin and squash seed|pumpkin seed|sesame|flax|hemp|chia", ["Seeds, sunflower, shelled, salted, roasted"]),
    (r"oil\b|olive", ["Oil, olive", "Oil, vegetable"]),
    (r"oats?\b|oatmeal", ["Oatmeal, plain, quick, cooked"]),
    (r"quinoa", ["Quinoa, cooked"]),
    (r"rice", ["Rice, brown, cooked", "Rice, white, enriched, cooked"]),
    (r"cereal|cheerios|granola", ["Cereal, oat ring", "Cereal, corn flakes"]),
    (r"bread|bagel|tortilla|toast", ["Bread, whole wheat, pre-sliced"]),
    (r"sweet potato|yam", ["Sweet potato, baked, peel removed"]),
    (r"potato", ["Potato, with peel, baked", "Potato, peeled, boiled"]),
    (r"corn", ["Corn, frozen, boiled"]),
    (r"barley|bulgur|buckwheat|millet|spelt|teff|amaranth|farro|grits", ["Cereal, whole wheat, cooked", "Oatmeal, plain, quick, cooked"]),
    (r"spinach|mustard spinach|vinespinach|cornsalad|arugula", ["Spinach, raw"]),
    (r"kale", ["Kale, fresh, pan-cooked"]),
    (r"collard|turnip greens|chard|lambsquarters", ["Collards, fresh/frozen, boiled"]),
    (r"lettuce|romaine", ["Lettuce, leaf, raw", "Lettuce, iceberg, raw"]),
    (r"broccoli", ["Broccoli, fresh/frozen, boiled"]),
    (r"brussels", ["Brussels sprouts, fresh/frozen, boiled"]),
    (r"cauliflower", ["Cauliflower, fresh/frozen, boiled"]),
    (r"cabbage|kohlrabi|sauerkraut", ["Cabbage, raw"]),
    (r"asparagus", ["Asparagus, fresh/frozen, boiled"]),
    (r"carrot", ["Carrot, baby, raw"]),
    (r"tomato|salsa", ["Tomato, raw"]),
    (r"pepper", ["Pepper, bell, green, raw"]),
    (r"onion|leek|scallion|garlic|shallot", ["Onion, mature, raw", "Garlic, raw"]),
    (r"cucumber|pickle", ["Cucumber, peeled, raw"]),
    (r"zucchini|summer squash|squash, summer", ["Zucchini, fresh/frozen, boiled"]),
    (r"squash|pumpkin", ["Squash, winter, fresh/frozen, boiled"]),
    (r"eggplant", ["Eggplant, fresh, peeled, boiled"]),
    (r"mushroom", ["Mushrooms, raw"]),
    (r"celery", ["Celery, raw"]),
    (r"avocado", ["Avocado, raw"]),
    (r"banana", ["Banana, raw"]),
    (r"pineapple|papaya|guava|kiwi", ["Pineapple, raw/frozen"]),
    (r"apple", ["Apple, red, with peel, raw"]),
    (r"orange|tangerine|clementine|grapefruit|lime|lemon", ["Orange, raw", "Grapefruit, raw"]),
    (r"strawberr", ["Strawberry, raw/frozen"]),
    (r"berr|cherr|currant", ["Blueberries, raw", "Strawberry, raw/frozen"]),
    (r"grape", ["Grapes, seedless, red/green, raw"]),
    (r"raisin|\bdates?\b|\bfigs?\b|prune", ["Raisins"]),
    (r"peach|apricot|plum|nectarine", ["Peach, raw/frozen"]),
    (r"pear", ["Pear, with peel, raw"]),
    (r"melon|cantaloupe|watermelon", ["Cantaloupe, raw/frozen", "Watermelon, raw/frozen"]),
    (r"mango", ["Mango, fresh/frozen"]),
    (r"salt", ["Salt, iodized"]),
    (r"juice", ["Juice, orange, bottled/cartoned"]),
]
# Foods nothing above matches: the average of a food group.
GROUP_FALLBACK = {
    "veg": ["Broccoli, fresh/frozen, boiled", "Green beans, fresh/frozen, boiled", "Tomato, raw", "Cabbage, raw", "Cucumber, peeled, raw"],
    "fruit": ["Apple, red, with peel, raw", "Orange, raw", "Banana, raw", "Peach, raw/frozen", "Pear, with peel, raw"],
    "other": ["Apple, red, with peel, raw", "Broccoli, fresh/frozen, boiled", "Rice, white, enriched, cooked", "Oatmeal, plain, quick, cooked"],
}
FALLBACK_DEFAULT = GROUP_FALLBACK["other"]


@lru_cache(maxsize=1)
def _tds():
    return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}


def _mean(names):
    tds = _tds()
    rows = [tds[n] for n in names if n in tds]
    return {k: sum(r.get(k, 0.0) for r in rows) / len(rows) for k in ("as", "cd", "pb", "hg")} if rows else None


def _text(food):
    text = f"{food.get('name', '')} {food.get('description', '')}".lower()
    if "mushroom" in text:
        text = text.replace("oyster", "")          # oyster mushrooms are not shellfish
    if "squash" in text:
        text = text.replace("scallop", "")         # nor is scallop squash
    return text


def match(food):
    """(TDS food names, matched?) for a food."""
    text = _text(food)
    for pattern, names in RULES:
        if re.search(pattern, text):
            return names, True
    return GROUP_FALLBACK.get(food.get("group"), FALLBACK_DEFAULT), False


def per_100g(food):
    """{c_hg, c_cd, c_pb, c_as}: µg per 100 g of this food as eaten."""
    names, _ = match(food)
    conc = _mean(names) or {"as": 0.0, "cd": 0.0, "pb": 0.0, "hg": 0.0}
    text = _text(food)
    total_as = conc["as"]
    hg = conc["hg"]
    if re.search(SHELLFISH, text):
        inorganic_as = SHELLFISH_INORGANIC_AS
    elif re.search(FISH, text):
        inorganic_as = FISH_INORGANIC_AS
    else:
        inorganic_as = total_as * INORGANIC_AS
    if re.search(SHELLFISH, text) or re.search(FISH, text):
        ppm = next((v for p, v in MERCURY_PPM if re.search(p, text)), GENERIC_FISH_PPM)
        hg = ppm * 100
        inorganic_as = min(inorganic_as, total_as)
    return {"c_hg": hg, "c_cd": conc["cd"], "c_pb": conc["pb"], "c_as": inorganic_as}


def add(per_serving, food):
    """Copy of per_serving plus the four contaminants (µg per serving)."""
    scale = food["serving_g"] / 100.0
    return {**per_serving, **{k: v * scale for k, v in per_100g(food).items()}}


def targets(weight_kg):
    """Daily soft limits: free below 30% of the limit, a growing penalty after, steepest past 60% and past the limit."""
    return {k: {"soft1": SOFT[0] * per_kg * weight_kg, "soft2": SOFT[1] * per_kg * weight_kg,
                "max": per_kg * weight_kg, "weight": 1.0} for k, per_kg in LIMITS.items()}


def fallbacks(foods):
    """Names of foods that matched no rule (they use their food group's average)."""
    return [f["name"] for f in foods if not match(f)[1]]
