"""Indispensable (essential) amino acids: daily targets, protein digestibility, leucine per meal.

Requirements: WHO/FAO/UNU 2007, Protein and amino acid requirements in human nutrition (TRS 935),
Table 23 (adult mean requirement) and section 8.4 (the safe level is 24% above the mean). The report is at
https://iris.who.int/handle/10665/43411 . Amounts are mg per kg body weight per day, and mg per g protein.

Digestibility: TRS 935 Table 5, true protein digestibility measured in humans, by food. It is protein
(nitrogen) digestibility applied to every amino acid of the food, not amino acid by amino acid (the FAO 2013
DIAAS report prefers ileal digestibility but publishes no per-food table). Foods the table doesn't list
use the nearest listed food, or DEFAULT_PLANT / DEFAULT_ANIMAL; those are this project's assumptions.

Leucine per meal: ISSN position stand 2017 (Jager et al., JISSN 14:20): "Acute protein doses should strive
to contain 700-3000 mg of leucine" and about 1-3 g of leucine per meal. 2.5 g is this project's choice in that range.
"""
import re

import recipes
from nutrients import AMINO_KEYS

REQUIREMENT = {"his": 10, "ile": 20, "leu": 39, "lys": 30, "saa": 15, "aaa": 25, "thr": 15, "trp": 4, "val": 26}   # mg/kg/day
PATTERN = {"his": 15, "ile": 30, "leu": 59, "lys": 45, "saa": 22, "aaa": 38, "thr": 23, "trp": 6, "val": 39}       # mg/g protein
SAFE_FACTOR = 1.24
MEAL_LEUCINE_MG = 2500
DEFAULT_PLANT, DEFAULT_ANIMAL = 0.85, 0.94

# (pattern on "name description", digestibility); first match wins, then by recipes.kind, then the default.
BY_TEXT = [
    (r"whey|casein", 0.95),                                  # milk, cheese
    (r"soy.*isolate|isolate.*soy", 0.95),                    # soy protein isolate
    (r"soy|tofu|tempeh|edamame|natto|miso", 0.86),           # soy flour (nearest listed whole-soy food)
    (r"peanut", 0.94),                                       # peanuts, peanut butter
    (r"sunflower|pumpkin.*seed|squash.*seed|sesame|flax|chia|hemp", 0.90),   # sunflower seed flour
    (r"\bmillet\b", 0.79),
    (r"\bcorn\b|maize|grits|polenta|hominy", 0.85),
    (r"\boats?\b|oatmeal", 0.86),
    (r"\bpeas?\b|cowpea|pigeonpea", 0.88),                   # mature peas
    (r"bread|bagel|muffin|pita|tortilla", None),             # whole wheat 0.86, white 0.96 (below)
]
BY_KIND = {"egg": 0.97, "fish": 0.94, "canned_fish": 0.94, "meat": 0.94, "poultry": 0.94,
           "yogurt": 0.95, "cheese": 0.95, "milk": 0.95, "whey": 0.95,
           "legume": 0.78, "pasta": 0.96, "grain": 0.88, "cereal": 0.75, "nuts": 0.94, "nut_butter": 0.94}


def digestibility(food):
    text = f"{food.get('name', '')} {food.get('description', '')}".lower()
    for pattern, value in BY_TEXT:
        if re.search(pattern, text):
            if value is None:
                return 0.86 if "whole" in text else 0.96
            return value
    k = recipes.kind(food)
    if k in BY_KIND:
        return BY_KIND[k]
    animal = set(food.get("contains", [])) & {"meat", "fish", "egg", "dairy"}
    return DEFAULT_ANIMAL if animal else DEFAULT_PLANT


def digest(per_serving, food):
    """Copy of per_serving with each amino acid reduced to what is digested; the factor is stored in food."""
    d = digestibility(food)
    food["digestibility"] = d
    return {k: (v * d if k in AMINO_KEYS else v) for k, v in per_serving.items()}


def targets(weight_kg, protein_goal_g):
    """Daily minimum = safe level for this body weight; goal = the requirement pattern at the protein goal."""
    return {k: {"min": REQUIREMENT[k] * SAFE_FACTOR * weight_kg, "goal": PATTERN[k] * protein_goal_g}
            for k in AMINO_KEYS}
