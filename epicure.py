"""Recipe-pairing scores from the Epicure-Cooc ingredient embeddings.

Epicure-Cooc (Radzikowski & Chen 2026, arXiv:2605.22391, CC BY 4.0) gives a
300-d vector to each of 1,790 ingredients, learned from which ingredients
co-occur in 4.14M recipes. Cosine similarity answers "what do people cook
this with", which is what grouping foods into meals and picking seasonings
needs. There are no recipes or quantities in it.

The CSV (~5 MB) and the model repo's mode atlas (labeled ingredient clusters,
1.2 MB) are downloaded once from Hugging Face and cached under data/.

The mode atlas is what tells breakfast foods from dinner foods: the clusters
carry labels like "Sweet baking and dessert ingredients" (eggs, oats, yogurt,
bananas) or "Savory meal components" (salmon, lentils, vegetables).
"""
import json
import csv
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import requests

HERE = Path(__file__).parent
CSV_URL = ("https://huggingface.co/datasets/Kaikaku/epicure-corpus-resources/"
           "resolve/main/data/epicure_cooc.csv")
CACHE = HERE / "data" / "epicure_cooc.npz"
MODES_URL = "https://huggingface.co/Kaikaku/epicure-cooc/resolve/main/modes.json"
MODES_CACHE = HERE / "data" / "epicure_modes.json"

# Cluster-label words. Only the emergent factor clusters (F_*) and food-group
# clusters (fg_*) are used; the sensory/USDA clusters have unrelated labels.
SWEET_WORDS = ("sweet", "dessert", "confection", "baking", "pastry", "liqueur", "cocktail", "fruit")
SAVORY_WORDS = ("savory", "stir-fry", "hot pot", "broth", "salad", "supper", "deli", "comfort meal",
                "mains", "wok", "braising", "umami", "seafood", "tofu", "charcuterie", "vinaigrette",
                "condiment", "sauce", "cheeses", "tex-mex", "cajun")

# Low-calorie flavorings to suggest. Sodium-heavy ones are flagged in the UI.
SEASONINGS = [
    "garlic", "ginger", "scallion", "onion", "red_onion", "shallot", "tomato", "jalapeno",
    "lemon", "lime", "lemon_pepper", "black_pepper", "basil", "oregano", "thyme", "rosemary",
    "dill", "parsley", "chive", "mint", "cumin", "paprika", "smoked_paprika", "chili_powder",
    "curry_powder", "garam_masala", "turmeric", "sumac", "italian_seasoning", "cinnamon",
    "nutmeg", "cardamom", "vanilla", "pumpkin_pie_spice", "nutritional_yeast",
    "rice_vinegar", "balsamic_vinegar", "apple_cider_vinegar", "salsa", "hot_sauce",
    "sriracha", "mustard", "soy_sauce", "tamari", "miso", "fish_sauce", "worcestershire_sauce",
]
HIGH_SODIUM = {"soy_sauce", "tamari", "miso", "fish_sauce", "worcestershire_sauce",
               "hot_sauce", "sriracha", "salsa", "mustard", "lemon_pepper"}
SAME_FAMILY = [{"soy_sauce", "tamari"}, {"paprika", "smoked_paprika"},
               {"onion", "red_onion", "shallot"}, {"lemon", "lemon_pepper"}, {"hot_sauce", "sriracha"},
               {"rice_vinegar", "balsamic_vinegar", "apple_cider_vinegar"}]

# USDA category words that come before the actual food ("Cereals, oats, ...").
GENERIC_HEADS = {"cereal", "nut", "seed", "fish", "spice", "oil", "beverage", "snack",
                 "candy", "candie", "soup", "sauce", "bean", "cracker", "cookie", "fast"}
# USDA description starts whose generic parse picks the wrong vocabulary entry.
ALIASES = {"peppers, sweet": "bell_pepper", "peppers, hot chili": "chili_pepper",
           "cereals, oats": "oat", "beef, ground": "ground_beef", "yogurt, greek": "greek_yogurt"}


def _download():
    CACHE.parent.mkdir(exist_ok=True)
    r = requests.get(CSV_URL, timeout=120)
    r.raise_for_status()
    rows = list(csv.reader(r.text.splitlines()))
    names = [row[1] for row in rows[1:]]
    vecs = np.array([[float(x) for x in row[2:]] for row in rows[1:]], dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    np.savez_compressed(CACHE, names=np.array(names), vecs=vecs.astype(np.float16))


def _download_modes():
    CACHE.parent.mkdir(exist_ok=True)
    r = requests.get(MODES_URL, timeout=120)
    r.raise_for_status()
    slim = [{"id": m["mode_id"], "property": m["property"], "label": m["label"], "members": m["members"]}
            for m in r.json() if m["property"].startswith(("F_", "fg_"))]
    MODES_CACHE.write_text(json.dumps(slim), encoding="utf-8")


class Epicure:
    def __init__(self):
        if not CACHE.exists():
            _download()
        if not MODES_CACHE.exists():
            _download_modes()
        self._modes = json.loads(MODES_CACHE.read_text(encoding="utf-8"))
        data = np.load(CACHE)
        self.names = [str(n) for n in data["names"]]
        self.vecs = data["vecs"].astype(np.float32)
        self.index = {n: i for i, n in enumerate(self.names)}

    @lru_cache(maxsize=None)
    def _labels(self, name):
        return tuple((m["label"].lower(), m["property"]) for m in self._modes if name in m["members"])

    @lru_cache(maxsize=None)
    def sweetness(self, name):
        """0 = savory meal ingredient, 1 = sweet/baking/breakfast ingredient, None if unknown.

        The share of the ingredient's mode-atlas clusters whose labels read sweet vs savory;
        ingredients in no such cluster take the average of their nearest neighbors."""
        score = self._label_score(name)
        if score is None and name in self.index:       # in no cluster: average its nearest neighbors
            sims = self.vecs @ self.vecs[self.index[name]]
            near = [self._label_score(self.names[j]) for j in np.argsort(-sims)[1:25]]
            near = [s for s in near if s is not None][:8]
            score = sum(near) / len(near) if near else None
        return score

    def _label_score(self, name):
        labels = [l for l, _ in self._labels(name)]
        sweet = sum(any(w in l for w in SWEET_WORDS) for l in labels)
        savory = sum(any(w in l for w in SAVORY_WORDS) for l in labels)
        return sweet / (sweet + savory) if sweet + savory else None

    def food_groups(self, name):
        """Food-group clusters (Fruit, Vegetable, Grain, Dairy...) the ingredient sits in."""
        return {p[3:] for _, p in self._labels(name) if p.startswith("fg_")}

    def has(self, name):
        return name in self.index

    def similarity(self, a, b):
        """Cosine in [-1, 1]; 0 when either food isn't in the vocabulary."""
        if a not in self.index or b not in self.index:
            return 0.0
        return float(self.vecs[self.index[a]] @ self.vecs[self.index[b]])

    def match(self, description):
        """Best vocabulary name for a USDA description like 'Chicken, broilers..., thigh, ...'."""
        plain = re.sub(r"\([^)]*\)", " ", description.lower())   # "Chickpeas (garbanzo beans)"
        segments = [re.findall(r"[a-z]+", s) for s in plain.split(",")][:4]
        segments = [[_singular(w) for w in s] for s in segments if s]
        if not segments:
            return None
        text = description.lower()
        for phrase, name in ALIASES.items():
            if text.startswith(phrase) and name in self.index:
                return name
        head, rest = segments[0], [w for s in segments[1:] for w in s]
        candidates = []  # (priority, name)
        for w in rest:                                   # "cheese, cheddar" -> cheddar_cheese
            candidates += [(6, f"{w}_{h}") for h in head] + [(6, f"{h}_{w}") for h in head]
        candidates.append((5, "_".join(head)))
        if set(head) & GENERIC_HEADS:                    # "nuts, cashew" -> cashew, not nut
            candidates += [(5.5, w) for w in rest[:2]]
        for a, b in zip(rest, rest[1:]):
            candidates.append((3, f"{a}_{b}"))
        candidates += [(2, h) for h in reversed(head)]   # last head word is usually the noun
        candidates += [(1, w) for w in rest]
        for _, name in sorted(candidates, key=lambda c: -c[0]):
            if name in self.index:
                return name
        return None

    def seasonings(self, foods, k=4):
        """Top flavorings for a meal: cosine to the mean of its ingredient vectors."""
        idx = [self.index[f] for f in foods if f in self.index]
        if not idx:
            return []
        centroid = self.vecs[idx].mean(axis=0)
        centroid /= np.linalg.norm(centroid)
        scored = sorted(((float(self.vecs[self.index[s]] @ centroid), s)
                         for s in SEASONINGS if s in self.index and s not in foods), reverse=True)
        picked = []
        for score, s in scored:
            if any(s in fam and fam & {p["name"] for p in picked} for fam in SAME_FAMILY):
                continue
            picked.append({"name": s, "label": s.replace("_", " "), "score": round(score, 3),
                           "high_sodium": s in HIGH_SODIUM})
            if len(picked) == k:
                break
        return picked


def _singular(w):
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("oes", "ches", "shes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us")) and len(w) > 3:
        return w[:-1]
    return w


@lru_cache(maxsize=1)
def load():
    return Epicure()
