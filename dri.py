"""Vitamin and mineral minimums and upper limits by sex and age.

Source: Dietary Reference Intakes, the joint US National Academies / Health Canada tables
(RDA, or AI where noted; UL = Tolerable Upper Intake Level), as published by Health Canada:
  https://www.canada.ca/en/health-canada/services/food-nutrition/healthy-eating/dietary-reference-intakes/tables/reference-values-elements.html
  https://www.canada.ca/en/health-canada/services/food-nutrition/healthy-eating/dietary-reference-intakes/tables/reference-values-vitamins-dietary-reference-intakes-tables-2005.html
  https://www.canada.ca/en/health-canada/services/food-nutrition/healthy-eating/dietary-reference-intakes/tables/reference-values-macronutrients-dietary-reference-intakes-tables-2005.html
Pulled 2026-10-24. Sodium and potassium are the 2019 values. Pregnancy and breastfeeding have their own
values, which this module does not cover.

Each row: (men by age band, women by age band, UL by age band or None). Age bands: 19-30, 31-50, 51-70, 71+.
The goal values in config.yaml are this project's own choices (higher-end targets for someone who trains),
scaled from the men's 19-30 value to each person's value.
"""

# key:         men RDA/AI,                women RDA/AI,               UL
TABLE = {
    "calcium":     ([1000, 1000, 1000, 1200], [1000, 1000, 1200, 1200], [2500, 2500, 2000, 2000]),
    "iron":        ([8, 8, 8, 8],             [18, 18, 8, 8],           [45, 45, 45, 45]),
    "magnesium":   ([400, 420, 420, 420],     [310, 320, 320, 320],     None),   # UL is for supplements only
    "phosphorus":  ([700, 700, 700, 700],     [700, 700, 700, 700],     [4000, 4000, 4000, 3000]),
    "potassium":   ([3400] * 4,               [2600] * 4,               None),   # AI; no UL
    "sodium":      ([1500] * 4,               [1500] * 4,               None),   # AI; no UL (2019 CDRR is 2300)
    "zinc":        ([11] * 4,                 [8] * 4,                  [40] * 4),
    "copper":      ([0.9] * 4,                [0.9] * 4,                [10] * 4),
    "manganese":   ([2.3] * 4,                [1.8] * 4,                [11] * 4),   # AI
    "selenium":    ([55] * 4,                 [55] * 4,                 [400] * 4),
    "vit_a":       ([900] * 4,                [700] * 4,                None),   # UL is for preformed vitamin A only
    "vit_c":       ([90] * 4,                 [75] * 4,                 [2000] * 4),
    "vit_d":       ([15, 15, 15, 20],         [15, 15, 15, 20],         [100] * 4),
    "vit_e":       ([15] * 4,                 [15] * 4,                 [1000] * 4),   # UL is for synthetic vitamin E
    "vit_k":       ([120] * 4,                [90] * 4,                 None),   # AI
    "thiamin":     ([1.2] * 4,                [1.1] * 4,                None),
    "riboflavin":  ([1.3] * 4,                [1.1] * 4,                None),
    "niacin":      ([16] * 4,                 [14] * 4,                 None),   # UL is for synthetic forms
    "pantothenic": ([5] * 4,                  [5] * 4,                  None),   # AI
    "vit_b6":      ([1.3, 1.3, 1.7, 1.7],     [1.3, 1.3, 1.5, 1.5],     [100] * 4),
    "folate":      ([400] * 4,                [400] * 4,                None),   # UL is for folic acid
    "vit_b12":     ([2.4] * 4,                [2.4] * 4,                None),
    "choline":     ([550] * 4,                [425] * 4,                [3500] * 4),   # AI
}
FIBER_AI = {"male": [38, 38, 30, 30], "female": [25, 25, 21, 21]}   # g/day, 14 g per 1000 kcal
VEGETARIAN_IRON_FACTOR = 1.8     # iron requirements are 1.8x higher for vegetarians (same source)
ABSOLUTE_GOAL = {"iron", "sodium"}   # goals that are not scaled from the men's value (iron differs a lot by sex)


def band(age):
    return 0 if age <= 30 else 1 if age <= 50 else 2 if age <= 70 else 3


def fiber_ai(sex, age):
    return FIBER_AI[sex][band(age)]


def nutrient_targets(base, sex, age, diet):
    """base = config.yaml targets (goal, weight, push per nutrient, written for a man aged 19-30).
    Returns {nutrient: {min, goal, [push], [max], weight}} for this person."""
    i = band(age)
    out = {}
    for key, cfg in base.items():
        men, women, ul = TABLE[key]
        rda = (men if sex == "male" else women)[i]
        if key == "iron" and diet in ("vegan", "vegetarian"):
            rda *= VEGETARIAN_IRON_FACTOR
        t = {k: v for k, v in cfg.items() if k in ("weight", "push")}
        t["min"] = rda
        if ul:
            t["max"] = ul[i]
        goal = cfg["goal"] if key in ABSOLUTE_GOAL else cfg["goal"] * rda / men[0]
        goal = max(goal, rda)
        if "max" in t:
            goal = min(goal, t["max"] * 0.9)
        t["goal"] = goal
        out[key] = t
    return out
