"""Build data/catalog.json: a few hundred whole foods for the planner to choose from.

Source: USDA FoodData Central "SR Legacy" bulk CSV download (about 7,800 lab-analyzed foods):
    https://fdc.nal.usda.gov/download-datasets   ->  SR Legacy -> CSV

    python build_catalog.py --usda-dir path/to/FoodData_Central_sr_legacy_food_csv_2018-04

Steps: keep plain, minimally processed foods from the food groups that make
meals; collapse the many variants of one food (grades, trims, cooking
methods) to a single entry; rank within each group by nutrient density;
keep the best N per group; tag each food for the diet and allergy filters.
Also writes data/catalog_review.txt, which lists every food with its tags so
the tagging can be checked by eye.
"""
import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import epicure
import optimize
import usda
from nutrients import CORE_KEYS, NUTRIENTS, extract_per_100g

HERE = Path(__file__).parent

# USDA food category id -> group, and how many foods to keep per group
CATEGORIES = {1: "dairy", 4: "fat", 5: "poultry", 8: "grain", 9: "fruit", 10: "pork", 11: "veg", 12: "nut",
              13: "beef", 14: "drink", 15: "fish", 16: "legume", 17: "game", 20: "grain"}
LIMITS = {"dairy": 40, "fat": 6, "poultry": 14, "grain": 36, "fruit": 70, "pork": 12, "veg": 150, "nut": 24,
          "beef": 14, "drink": 6, "fish": 40, "legume": 40, "game": 12}
FAMILY_CAP = {"veg": 6, "fruit": 4, "poultry": 3, "game": 3}   # entries kept per Epicure ingredient; 2 for everything else
ANIMAL_GROUPS ={"poultry", "pork", "beef", "game", "fish"}

# Variants of the same food differ in these; the plainest is kept.
NOISE = re.compile(
    r"^(raw|cooked|boiled|roasted|baked|broiled|grilled|braised|steamed|stewed|simmered|pan-broiled|pan-fried|microwaved|"
    r"drained|drained solids|without salt|fresh|year round average|unprepared|prepared|regular|commercially prepared|"
    r"separable lean and fat|separable lean only|separable fat|trimmed to .*|all grades|choice|select|prime|"
    r"usda commodity.*|cooked, .*|dry heat|moist heat|boiled, drained|boiled, drained, without salt|"
    r"drained, without salt|solids and liquids|with salt|not fortified|enriched|unenriched|frozen|canned|"
    r"all commercial varieties|with added .*|fortified|\d+% lean meat ?/ ?\d+% fat|meat only|meat and skin|skin only|"
    r"without added .*|protein fortified|calcium fortified|giblets|neck meat only|\d+ ?(inch|in).*|uncooked|dry|low sodium|no salt added|sodium added.*|"
    r"california|florida|arizona|texas|all areas|imported|.*valencias?|.*, all areas|california and arizona|"
    r"pink and red.*|water pack|with skin|without skin|solids and liquids?|total can contents)$")
EXCLUDE = re.compile(
    r"babyfood|baby food|fast food|restaurant|commodity|imitation|substitute|formula|dehydrated|freeze-dried|"
    r"powder|\bmix\b|flour|\bmeal\b|starch|\bbran\b|\bgerm\b|syrup|sweetened|candied|glazed|breaded|battered|fried|"
    r"fat added|\bsalted\b|with salt|pickled|marinated|stuffed|luncheon|sausage|bacon|jerky|\bsticks?\b|nugget|"
    r"\bcured\b|\bham\b|canned.*(sauce|gravy)|in (light |heavy )?syrup|packed in|oil roasted|honey|"
    r"dry roasted|ready-to-drink|alcoholic|\bbeer\b|\bwine\b|energy drink|carbonated|cocoa|chocolate|coffee|\btea\b|"
    r"liverwurst|pate|spread|dip\b|dressing|sauce|\bsoup\b|gravy|pie filling|juice|jam\b|jelly|puree|"
    r"cake|cookie|pastry|pudding|dessert|candy|croissant|biscuit|crackers?|chips?\b|pizza|sandwich|burrito|taco|"
    r"kit\b|nectar|cocktail|concentrate|from concentrate|dietetic|low calorie|flavored|^fats?\b|lard|tallow|"
    r"shortening|margarine|mayonnaise|oleo|bologna|salami|frankfurter|hot dog|corned|pastrami|pemmican|"
    r"scrapple|headcheese|spam|tamale|chili|\bstew\b|casserole|loaf|souffle|creamed|pickle|olives|relish|"
    r"usda|(?<!without )salt|variety meats|by-products|extender|meatless|industrial|new zealand|nutritional supplement|"
    r"\bdried\b|evaporated|condensed|\bwhey\b|yolk|\bduck\b|goose|quail|sugared|peel\b|frog|turtle|snail|"
    r"\bkidney|\bliver|\blung|spleen|brain|tripe|tongue|\bheart\b|giblet|\bgizzard|\bfeet\b|\bears\b|\btail\b|"
    r"isolate|potassium type|eggnog|^cream|scalloped|au gratin|home-prepared|hash brown|cooked in skin, skin|arrowhead|arrowroot|balsam|borage|burdock|celtuce|chrysanthemum|cress|epazote|fiddlehead|tree fern|"
    r"taro|salsify|lotus|acorn|ginkgo|breadfruit|malabar|amaranth leaves|hyacinth|winged bean|jicama|^vegetables, mixed|"
    r"cowpeas, young|grape leaves|pumpkin leaves|sweet potato leaves|mung beans?, mature|mungo|lemon grass|wasabi|chestnuts?, japanese|mechanically|\bbear\b|\bdove\b|\bemu\b|ostrich|pheasant|grouse|guinea hen|squab|\bhorse\b|^yogurt, (vanilla|fruit|strawberry)|greek, (strawberry|vanilla)|lowfat, strawberry|kefir.*(strawberry|flavor)|from concentrate", re.I)
COMMON = re.compile(r"broccoli|spinach|kale|carrot|tomato|cucumber|lettuce|cabbage|cauliflower|pepper|onion|garlic|mushroom|zucchini|squash|asparagus|celery|corn|peas?\b|snap bean|green bean|potato|beet|eggplant|brussels|radish|artichoke|leek|turnip|okra|pumpkin|avocado|arugula|collard|chard|bok choy|cabbage|fennel|parsnip|rutabaga|kohlrabi|watercress|apple|banana|orange|berr|grape|pear|peach|plum|cherr|melon|pineapple|mango|papaya|kiwi|grapefruit|lemon|lime|pomegranate|apricot|fig|dates?\b|tangerine|clementine|raisin|guava|persimmon|nectarine|prune|chicken|turkey|beef|pork|lamb|veal|bison|salmon|tuna|cod|tilapia|shrimp|haddock|halibut|trout|sardine|mackerel|crab|lobster|scallop|herring|pollock|catfish|snapper|bass|flounder|sole|swordfish|anchov|mussel|oyster|clam|squid|\begg|tofu|tempeh|natto|lentil|bean|chickpea|hummus|edamame|split pea|yogurt|kefir|milk|cheese|rice|oat|quinoa|pasta|spaghetti|macaroni|noodle|barley|bulgur|couscous|millet|buckwheat|amaranth|teff|farro|spelt|almond|walnut|cashew|pecan|pistachio|hazelnut|macadamia|peanut|sunflower|pumpkin and squash seed|flax|chia|sesame|hemp|olive|canola", re.I)   # familiar grocery foods only
FAT_KEEP = re.compile(r"olive|canola|avocado|flaxseed|sesame|walnut", re.I)
DRINK_KEEP = re.compile(r"(almond|rice|oat|coconut|soy|cashew|hemp)\b.*\bmilk|milk.*\b(almond|rice|oat|soy)", re.I)
OAT_KEEP = re.compile(r"^cereals, oats.*not fortified|^cereals, oats, regular and quick and instant, not fortified", re.I)
# Cooked states for foods eaten cooked; everything else is taken raw.
COOKED_PRODUCE = re.compile(r"potato|\byam\b|cassava|plantain|corn, sweet|squash, winter|pumpkin|beet|turnip|"
                            r"parsnip|rutabaga|artichoke|brussels|eggplant|asparagus|collards|mustard greens|"
                            r"turnip greens|chard|cauliflower|broccoli", re.I)
PREFER_COOKED = {"poultry", "pork", "beef", "game", "fish", "legume", "grain"}
LEAD_CLASS = {"crustaceans", "mollusks", "fish", "nuts", "seeds", "cereals", "game meat", "legumes", "vegetables", "fruit"}
TAIL_CLASS = {"sauce", "soup", "cheese", "oil", "milk", "yogurt", "bread", "butter", "beans", "peppers"}

TREE_NUTS = re.compile(r"almond|walnut|cashew|pecan|pistachio|hazelnut|filbert|brazil nut|macadamia|pine nut|"
                       r"chestnut|ginkgo|beechnut|hickory", re.I)
GLUTEN = re.compile(r"(?<!buck)wheat|barley|\brye\b|bulgur|couscous|farro|spelt|semolina|kamut|triticale|seitan|pasta|"
                    r"spaghetti|macaroni|noodle|\bbread\b|\bmalt|orzo|freekeh|durum", re.I)
SOY = re.compile(r"\bsoy|tofu|tempeh|edamame|natto|\bmiso\b|soybean", re.I)
PLANT_MILK = re.compile(r"\b(almond|soy|soya|rice|oat|coconut|cashew|hemp|flax)\b", re.I)


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usda-dir", required=True, help="folder holding food.csv, food_nutrient.csv ... from the SR Legacy CSV zip")
    ap.add_argument("--out", default=HERE / "data" / "catalog.json")
    return ap.parse_args()


def read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        yield from csv.DictReader(fh)


def tags(desc, category):
    d = desc.lower()
    t = set()
    if category in (5, 10, 13, 17):
        t.add("meat")
    if category == 15:
        t.add("fish")
    if category == 1:
        t.add("egg" if re.search(r"\begg", d) else "dairy")
    elif re.search(r"\b(whey|casein|ghee)\b", d):
        t.add("dairy")
    elif category == 14 and "milk" in d and not PLANT_MILK.search(d):
        t.add("dairy")
    if re.search(r"\begg(?!plant)", d):
        t.add("egg")
    if SOY.search(d):
        t.add("soy")
    if GLUTEN.search(d) and not (re.search(r"\brice\b|corn|quinoa", d) and not re.search(r"wheat", d)):
        t.add("gluten")
    if re.search(r"peanut", d):
        t.add("peanut")
    if TREE_NUTS.search(d) or (category == 12 and re.search(r"coconut", d) is None and not re.search(
            r"seed|peanut|flax|chia|sesame|sunflower|pumpkin|hemp|lotus|watermelon|squash|safflower|breadnut", d)):
        t.add("tree_nut")
    return sorted(t)


def segments(desc):
    return [s.strip() for s in re.sub(r"\([^)]*\)", "", desc).split(",") if s.strip()]


def group_key(desc):
    keep = [s.lower() for s in segments(desc) if not NOISE.match(s.lower())]
    return "|".join(keep)


def state(desc):
    d = desc.lower()
    if re.search(r"cooked|boiled|roasted|baked|broiled|grilled|braised|steamed|stewed|simmered|pan-broiled|microwaved", d):
        return "cooked"
    return "raw" if "raw" in d else "other"


def display_name(desc, group, st):
    segs = [s for s in segments(desc) if not NOISE.match(s.lower())]
    if segs and segs[0].lower() in LEAD_CLASS and len(segs) > 1:
        segs = segs[1:]
    elif len(segs) > 1 and segs[0].lower() in TAIL_CLASS:
        segs = [f"{segs[1]} {segs[0]}"] + segs[2:]
    name = ", ".join(segs[:3]) or segments(desc)[0]
    name = name[:1].upper() + name[1:]
    if st == "cooked" and group in PREFER_COOKED | {"veg"} and "cook" not in name.lower():
        name += " (cooked)"
    elif st == "raw" and group in ANIMAL_GROUPS and "raw" not in name.lower():
        name += " (raw)"
    return name[:60]


def completeness(per100):
    return sum(1 for k in CORE_KEYS if k in per100)


def build(usda_dir):
    usda_dir = Path(usda_dir)
    number = {r["id"]: r["nutrient_nbr"] for r in read(usda_dir / "nutrient.csv")}
    foods = {r["fdc_id"]: r for r in read(usda_dir / "food.csv")}
    nutrients = defaultdict(list)
    for r in read(usda_dir / "food_nutrient.csv"):
        if r["fdc_id"] in foods and r["amount"] != "" and r["nutrient_id"] in number:
            nutrients[r["fdc_id"]].append({"nutrient": {"number": number[r["nutrient_id"]]}, "amount": float(r["amount"])})
    portions = defaultdict(list)
    for r in read(usda_dir / "food_portion.csv"):
        if r["gram_weight"]:
            label = f"{float(r['amount'] or 1):g} {(r['modifier'] or r['portion_description']).strip()}".strip()
            portions[r["fdc_id"]].append({"grams": float(r["gram_weight"]), "label": label})

    ep = epicure.load()
    cfg = optimize.load_config()
    refs = {k: v.get("goal", v.get("min")) for k, v in cfg["targets"].items() if v.get("goal", v.get("min"))}

    def density(per100):
        """Mean of (nutrient per 100 kcal, scaled to a 2000 kcal day) / daily goal, each capped at 1.5."""
        kcal = per100.get("energy", 0.0)
        if kcal <= 0:
            return 0.0
        vals = [min(1.5, per100.get(k, 0.0) / kcal * 2000 / ref) for k, ref in refs.items()
                if k not in ("sodium", "energy", "protein", "fat")]
        return sum(vals) / len(vals)

    candidates = defaultdict(dict)       # group -> key -> best entry
    for fid, f in foods.items():
        category = int(f["food_category_id"] or 0)
        if category not in CATEGORIES:
            continue
        desc = f["description"]
        group = CATEGORIES[category]
        if group == "fat" and not FAT_KEEP.search(desc):
            continue
        if group == "drink" and not DRINK_KEEP.search(desc):
            continue
        if group in ("fruit", "veg") and re.search(r"canned|water pack", desc, re.I):
            continue
        if category == 8 and not OAT_KEEP.search(desc):
            continue
        if category == 20 and re.search(r"cereals?\b|\bbread\b|spelt flour|\bdry\b.*\bmix|prepared from|noodles, egg, enriched, dry", desc, re.I):
            continue
        if not COMMON.search(desc):
            continue
        plain = re.sub(r"\([^)]*\)", "", desc)
        if EXCLUDE.search(plain.replace("without salt", "")) and not (category == 8):
            continue
        per100 = extract_per_100g(nutrients[fid])
        if not all(k in per100 for k in ("energy", "protein", "fat", "carbs")):
            continue
        kcal = per100["energy"]
        if kcal <= 0 and group != "drink":
            continue
        sodium = per100.get("sodium", 0.0)
        if sodium > (1000 if group == "dairy" else 600) or (kcal > 0 and sodium / kcal * 100 > 400):
            continue
        known = ep.match(desc) is not None      # recipe-known foods may have thinner USDA data
        if completeness(per100) < (12 if known else len(CORE_KEYS) - 9):
            continue
        st = state(desc)
        if group == "grain" and st != "cooked" and category != 8:
            continue                              # dry grains and pasta: the cooked entry is the one to eat
        want = "cooked" if (group in PREFER_COOKED or (group == "veg" and COOKED_PRODUCE.search(desc))) else "raw"
        if group == "grain" and category == 8:
            want = "cooked"
        entry = {"fdc_id": int(fid), "description": desc, "category": category, "group": group, "state": st,
                 "per_100g": per100, "rank": (st == want, "without salt" in desc.lower() or "salt" not in desc.lower(),
                                              per100["protein"] / max(kcal, 1),
                                              "lean only" in desc.lower() or "meat only" in desc.lower(),
                                              completeness(per100), -len(desc))}
        key = group_key(desc)
        best = candidates[group].get(key)
        if best is None or entry["rank"] > best["rank"]:
            candidates[group][key] = entry

    kept = []
    for group, limit in LIMITS.items():
        if group in ANIMAL_GROUPS:     # meat and fish: lean and protein-dense first
            score = lambda e: e["per_100g"]["protein"] * 4 / max(e["per_100g"]["energy"], 1) + 0.3 * density(e["per_100g"])
        else:
            score = lambda e: density(e["per_100g"])
        # foods Epicure knows from recipes are the ones people actually cook, so they come first
        ranked = sorted(candidates[group].values(), key=lambda e: (ep.match(e["description"]) is not None, score(e)),
                        reverse=True)
        per_family = defaultdict(int)             # at most a few entries per food, so 12 turkey cuts can't crowd out chicken
        for e in ranked:
            family = ep.match(e["description"]) or " ".join(segments(e["description"])[:2]).lower()
            if group in ("beef", "pork"):          # one family per cut: ground, round, loin, chuck ...
                family += "|" + (group_key(e["description"]).split("|") + [""])[1]
            if per_family[family] < FAMILY_CAP.get(group, 2) and len([k for k in kept if k["group"] == group]) < limit:
                per_family[family] += 1
                kept.append(e)
    return kept, portions


def finish(kept, portions):
    ep = epicure.load()
    out, seen = [], set()
    for e in kept:
        group = e["group"]
        name = display_name(e["description"], group, e["state"])
        base, n = name, 2
        while name.lower() in seen:
            name = f"{base[:55]} {n}"
            n += 1
        seen.add(name.lower())
        per100 = e["per_100g"]
        grams, label = usda.pick_serving(portions.get(str(e["fdc_id"]), []), per100["energy"])
        kcal = per100["energy"] * grams / 100
        cap = 4 if kcal <= 0 else max(1, min(4, round(600 / kcal)))
        out.append({"name": name, "fdc_id": e["fdc_id"], "description": e["description"], "group": group,
                    "serving_g": grams, "label": label, "max_servings": cap,
                    "per_100g": per100, "missing": [k for k in NUTRIENTS if k not in per100],
                    "contains": tags(e["description"], e["category"]), "epicure": ep.match(e["description"])})
    return out


def main():
    args = parse_args()
    kept, portions = build(args.usda_dir)
    catalog = finish(kept, portions)
    out = Path(args.out)
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    review = HERE / "data" / "catalog_review.txt"
    with open(review, "w", encoding="utf-8") as fh:
        for g in sorted({c["group"] for c in catalog}):
            fh.write(f"\n== {g} ==\n")
            for c in sorted((c for c in catalog if c["group"] == g), key=lambda c: c["name"]):
                fh.write(f"{c['name']:<60} {','.join(c['contains']) or '-':<22} {c['fdc_id']}  {c['description'][:70]}\n")
    counts = defaultdict(int)
    for c in catalog:
        counts[c["group"]] += 1
    print(f"{len(catalog)} foods -> {out}")
    print(dict(counts))
    print(f"Review tags in {review}")


if __name__ == "__main__":
    main()
