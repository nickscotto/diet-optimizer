"""Which foods the planner may use: what a US Aldi store sells.

There is no complete public Aldi product list (Aldi's terms bar scraping its site, and the crowd-sourced Open Food
Facts data, data/aldi_off_products.json, holds only packaged goods with barcodes: fresh produce, meat and fish are
barely in it). So this is a hybrid:

  * TYPES is a plain list of generic Aldi foods, written by hand. Each USDA food is matched to the first type it fits
    and only matching foods are allowed. Of several USDA foods for the same type (broccoli, broccoli spears, chopped
    broccoli) the catalog keeps one, the simplest name.
  * The third field of a type is an Open Food Facts search term; `off_matches` counts Aldi products in that data
    matching it, as a sanity check for packaged foods. Fresh types have None.

Searched at a real store (zip 32940) and not found, so left out: peas and carrots (sold as separate peas and
carrots), arugula, lima beans. Confirmed by search: kale, cabbage, asparagus, bell peppers, mandarins, brown
rice, quinoa, lentils, tofu, sunflower seeds, almond butter, sauerkraut.

Edit TYPES to add or remove a food. Everything is held to it: catalog foods, config.yaml staples, and foods the user
adds from the USDA search.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
OFF_PATH = HERE / "data" / "aldi_off_products.json"

# (label, regex on "name description" lowercased, Open Food Facts search term or None for fresh foods)
TYPES = [
    # --- fresh vegetables (and the same frozen / canned)
    ("broccoli", r"^broccoli(?!.*(raab|chinese|leaves|stalk))", "broccoli"),
    ("cauliflower", r"^cauliflower(?!.*green)", "cauliflower"),
    ("carrots", r"^(baby )?carrots?\b", "carrots"),
    ("spinach", r"^spinach(?!.*mustard)|^baby spinach", "spinach"),
    ("kale", r"^kale", "kale"),
    ("lettuce", r"^lettuce, (cos or romaine|green leaf|iceberg)|^romaine|^lettuce$", "romaine"),
    ("cabbage", r"^cabbage, (common|red)|^cabbage$", "cabbage"),
    ("brussels sprouts", r"^brussels", "brussels"),
    ("asparagus", r"^asparagus", "asparagus"),
    ("green beans", r"^(snap|green) beans?, green|^green beans?", "green beans"),
    ("peas", r"^peas, green$|^peas, green\b(?!.*split)|^peas, edible-podded", "peas"),
    ("corn", r"^corn, sweet, (yellow|white)", "corn"),
    ("zucchini", r"^squash, (summer, zucchini|zucchini)|^zucchini", "zucchini"),
    ("yellow squash", r"^squash, summer, (all varieties|crookneck)", None),
    ("butternut squash", r"^squash, winter, butternut", "butternut"),
    ("bell peppers", r"^sweet peppers, (green|red|yellow)\b(?!.*(chopped|sauteed))|^bell pepper", "bell pepper"),
    ("jalapeno", r"^jalapeno", "jalapeno"),
    ("onions", r"^onions, (whole|sweet|spring)|^onions?$", "onion"),
    ("garlic", r"^garlic", "garlic"),
    ("mushrooms", r"^mushrooms?, (white|brown|portabella|baby)|^uv mushrooms|^mushroom, white", "mushroom"),
    ("tomatoes", r"^tomatoes, red", "tomato"),
    ("potatoes", r"^potatoes, (russet|red|white)", "potato"),
    ("sweet potatoes", r"^sweet potato\b", "sweet potato"),
    ("cucumber", r"^cucumber", "cucumber"),
    ("avocado", r"^avocado", "avocado"),
    ("beets", r"^beets", "beets"),
    # --- fruit
    ("apples", r"^apples?\b", "apple"),
    ("bananas", r"^bananas?", "banana"),
    ("oranges", r"^oranges, navels|^oranges$|^orange\b(?!.*juice)", "orange"),
    ("clementines", r"^clementines|^tangerines", "clementine"),
    ("grapefruit", r"^grapefruit(?!.*juice)", "grapefruit"),
    ("grapes", r"^grapes, red or green", "grapes"),
    ("strawberries", r"^strawberr", "strawberr"),
    ("blueberries", r"^blueberries(?!, wild)", "blueberr"),
    ("raspberries", r"^raspberr", "raspberr"),
    ("blackberries", r"^blackberr", "blackberr"),
    ("peaches", r"^peaches", "peach"),
    ("pears", r"^pears?(, (bartlett|green anjou|red anjou))?$", "pear"),
    ("pineapple", r"^pineapple, (all varieties|traditional)", "pineapple"),
    ("mango", r"^mangos?\b", "mango"),
    ("kiwi", r"^kiwifruit, green", "kiwi"),
    ("watermelon", r"^watermelon", None),
    ("cantaloupe", r"^melons, cantaloupe", None),
    ("cherries", r"^cherries, sweet", "cherr"),
    ("raisins", r"^raisins, dark", "raisins"),
    ("dates", r"^dates, medjool", "dates"),
    ("cranberries", r"^cranberries", "cranberr"),
    ("orange juice", r"orange juice", "orange juice"),
    # --- meat
    ("ground beef", r"^beef, ground|^lean ground beef", "ground beef"),
    ("beef steak", r"^beef, (top sirloin|loin, top sirloin|round, eye of round|chuck|ribeye|grass-fed, strip)", None),
    ("beef roast", r"^beef, (round, top round|shoulder pot roast|brisket)", None),
    ("pork loin", r"^pork, (loin, center loin|loin, tenderloin|enhanced, loin)", None),
    ("ground pork", r"^pork, ground", None),
    ("chicken breast", r"^chicken, broilers? or fryers?, breast|^chicken breast", "chicken breast"),
    ("rotisserie chicken", r"^chicken, broilers or fryers, rotisserie", None),
    ("chicken thigh", r"^chicken, broilers? or fryers?, (thigh|drumstick)", None),
    ("turkey breast", r"^turkey, retail parts, breast", "turkey breast"),
    ("ground turkey", r"^turkey, ground", "ground turkey"),
    # --- fish
    ("salmon", r"^salmon(?!, pink)|^fish, salmon", "salmon"),
    ("tilapia", r"^tilapia", None),
    ("cod", r"^cod, atlantic|^cod\b", None),
    ("haddock", r"^haddock", None),
    ("pollock", r"^pollock, alaska", None),
    ("catfish", r"^catfish, channel, farmed", None),
    ("rainbow trout", r"^trout, rainbow|^rainbow trout", None),
    ("shrimp", r"^shrimp", "shrimp"),
    ("canned tuna", r"^tuna, light, canned in water|^canned tuna", "tuna"),
    ("canned salmon", r"^salmon, pink", "pink salmon"),
    ("sardines", r"^sardines", "sardine"),
    # --- eggs and dairy
    ("eggs", r"^egg, whole$|^eggs$", "egg"),
    ("milk", r"^(nonfat |lowfat |reduced fat |whole )?milk\b(?!fish)", "milk"),
    ("soy milk", r"^soy ?milk|^soymilk, (original|nonfat|enhanced)", "soy"),
    ("cheddar", r"^cheddar cheese$", "cheddar"),
    ("mozzarella", r"^mozzarella cheese", "mozzarella"),
    ("swiss cheese", r"^swiss cheese$", None),
    ("parmesan", r"^parmesan", "parmesan"),
    ("cottage cheese", r"^cottage cheese", "cottage cheese"),
    ("cream cheese", r"^cream cheese", "cream cheese"),
    ("greek yogurt", r"^greek yogurt|^plain yogurt, (low fat|skim)", "greek yogurt"),
    ("kefir", r"^kefir", "kefir"),
    # --- beans, tofu
    ("black beans", r"^black beans", "black beans"),
    ("pinto beans", r"^pinto beans(?!, immature)(?!.*sprouted)", "pinto"),
    ("kidney beans", r"^kidney", "kidney"),
    ("navy beans", r"^(navy|small white|white) beans, mature seeds \(cooked\)", "navy"),
    ("chickpeas", r"^chickpeas, mature seeds \(cooked\)|^chickpeas$", "chickpea"),
    ("lentils", r"^lentils(?!, sprouted)", "lentil"),
    ("edamame", r"^edamame", "edamame"),
    ("tofu", r"^extra firm tofu|^tofu, (extra )?firm", "tofu"),
    ("hummus", r"^hummus, commercial", "hummus"),
    # --- grains, bread, cereal
    ("oats", r"^oats", "oats"),
    ("brown rice", r"^rice, brown, long-grain|^brown rice", "brown rice"),
    ("white rice", r"^rice, white, long-grain \(cooked\)$", "white rice"),
    ("quinoa", r"^quinoa", "quinoa"),
    ("pasta", r"^pasta, whole-wheat|^spaghetti", "pasta"),
    ("couscous", r"^couscous", "couscous"),
    ("whole wheat bread", r"^whole wheat bread", "whole wheat bread"),
    ("breakfast cereal", r"^fortified cereal", "cereal"),
    # --- nuts, seeds, spreads, oil
    ("almonds", r"^almonds$", "almonds"),
    ("walnuts", r"^walnuts", "walnut"),
    ("peanuts", r"^peanuts, all types$", "peanuts"),
    ("cashews", r"^cashew nuts", "cashew"),
    ("pecans", r"^pecans", "pecan"),
    ("pistachios", r"^pistachio", "pistachio"),
    ("sunflower seeds", r"^sunflower seed kernels", "sunflower"),
    ("pumpkin seeds", r"^pumpkin and squash seed kernels|^pumpkin seeds", "pumpkin seed"),
    ("peanut butter", r"^peanut butter", "peanut butter"),
    ("almond butter", r"^almond butter", "almond butter"),
    ("olive oil", r"^olive oil", "olive oil"),
    ("canola oil", r"^canola oil", None),
    # --- other
    ("protein powder", r"^whey protein", "whey protein"),
    ("sauerkraut", r"^sauerkraut", "sauerkraut"),
    ("salt", r"^iodized salt", "salt"),
]


# Everyday Aldi foods by base name, matched against the first part of the USDA description ("Tomatoes, grape, raw"
# -> "tomatoes"), so any variety or form of them is allowed. TYPES above is stricter: it picks the one catalog entry per
# kind of food. This list decides what a food you add yourself may be.
GENERIC = (
    r"jerky|tomato|onion|scallion|garlic|ginger|celery|radish|leek|shallot|lettuce|romaine|spinach|kale|chard|collard|arugula|cabbage|"
    r"coleslaw|broccoli|cauliflower|brussels|asparagus|green bean|snap bean|string bean|pea\b|peas\b|corn|zucchini|squash|pumpkin|"
    r"cucumber|pickle|pepper|jalapeno|eggplant|mushroom|carrot|beet|turnip|potato|yam|avocado|olive|artichoke|"
    r"apple|banana|orange|clementine|tangerine|mandarin|lemon|lime|grapefruit|grape|strawberr|blueberr|raspberr|blackberr|cranberr|"
    r"peach|nectarine|plum|pear|apricot|cherr|mango|pineapple|kiwi|papaya|melon|watermelon|cantaloupe|date|raisin|prune|fig\b|figs\b|"
    r"pomegranate|coconut|"
    r"beef|steak|pork|bacon|ham\b|sausage|chicken|turkey|lamb|veal|hot dog|frankfurter|salami|"
    r"salmon|tuna|tilapia|cod\b|haddock|pollock|catfish|trout|sardine|mackerel|herring|anchov|shrimp|crab|scallop|mussel|clam|"
    r"egg|milk|cream|butter\b|cheese|cheddar|mozzarella|parmesan|feta|swiss|provolone|yogurt|kefir|"
    r"bean|lentil|chickpea|garbanzo|hummus|tofu|tempeh|edamame|soy|"
    r"oat|rice|quinoa|barley|couscous|pasta|spaghetti|macaroni|noodle|bread|bagel|tortilla|pita|bun\b|roll\b|cereal|granola|flour|"
    r"cracker|popcorn|pancake|waffle|"
    r"almond|walnut|peanut|cashew|pecan|pistachio|hazelnut|macadamia|sunflower|pumpkin seed|sesame|flax|chia|hemp|nut\b|nuts\b|seed|"
    r"oil\b|vinegar|salt|sugar|honey|syrup|jam\b|jelly|ketchup|mustard|mayonnaise|salsa|sauce|broth|stock|soup|cocoa|chocolate|"
    r"juice|coffee|tea\b|sauerkraut|kimchi|protein|whey"
)
_GENERIC = re.compile(GENERIC, re.I)
# Not sold at Aldi, or not a normal grocery food, even when a base word above matches.
NOT_ALDI = re.compile(r"babyfood|baby food|infant|formula|restaurant|fast food|mcdonald|burger king|wendy|taco bell|"
                      r"school lunch|ns as to|usda commodity|game meat|bison|buffalo|elk|venison|moose|caribou|"
                      r"rabbit|squirrel|opossum|seal\b|whale|horse|emu|ostrich|raw,? wild|alcohol|beer|wine|liquor", re.I)


def _text(food):
    return f"{food.get('name', '')} {food.get('description', '')}".lower().strip()


def type_of(food):
    """Label of the Aldi type this food belongs to, or None when Aldi doesn't sell it."""
    name, text = food.get("name", "").lower().strip(), _text(food)
    for label, pattern, _ in TYPES:
        if re.search(pattern, name) or re.search(pattern, text):
            return label
    return None


def allowed(food):
    """Aldi sells it: one of the TYPES, or a common grocery food by base name (any variety)."""
    if type_of(food):
        return True
    text = _text(food)
    if NOT_ALDI.search(text):
        return False
    base = (food.get("description") or food.get("name") or "").lower().split(",")[0]
    return bool(re.search(r"protein powder|ginger|jerky", text) or _GENERIC.search(base) or _GENERIC.search(food.get("name", "").lower()))


def select(foods):
    """Aldi foods only, one per type: of several USDA foods for the same type, the one with the simplest name."""
    best = {}
    for f in foods:
        label = type_of(f)
        if label and (label not in best or len(f["name"]) < len(best[label]["name"])):
            best[label] = f
    keep = {id(f) for f in best.values()}
    return [f for f in foods if id(f) in keep]


def off_matches(term, limit=3):
    """(count, examples) of Open Food Facts Aldi products whose name contains the term."""
    if not OFF_PATH.exists() or not term:
        return 0, []
    names = json.loads(OFF_PATH.read_text(encoding="utf-8"))
    hits = [n for n in names if term in n]
    return len(hits), hits[:limit]
