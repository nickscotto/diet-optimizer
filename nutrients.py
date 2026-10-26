"""Nutrient definitions and USDA FoodData Central nutrient-number mapping.

Each nutrient lists the FDC "nutrient number" codes to look for, in order of
preference, with a factor that converts that code's unit into ours.
FDC values for Foundation and SR Legacy foods are per 100 g.
"""

NUTRIENTS = {
    # key:           (display name,        unit,  [(fdc number, factor), ...])
    "energy":        ("Calories",          "kcal", [("208", 1), ("958", 1), ("957", 1)]),
    "protein":       ("Protein",           "g",    [("203", 1)]),
    "fat":           ("Fat",               "g",    [("204", 1)]),
    "carbs":         ("Carbohydrate",      "g",    [("205", 1)]),
    "fiber":         ("Fiber",             "g",    [("291", 1)]),
    "calcium":       ("Calcium",           "mg",   [("301", 1)]),
    "iron":          ("Iron",              "mg",   [("303", 1)]),
    "magnesium":     ("Magnesium",         "mg",   [("304", 1)]),
    "phosphorus":    ("Phosphorus",        "mg",   [("305", 1)]),
    "potassium":     ("Potassium",         "mg",   [("306", 1)]),
    "sodium":        ("Sodium",            "mg",   [("307", 1)]),
    "zinc":          ("Zinc",              "mg",   [("309", 1)]),
    "copper":        ("Copper",            "mg",   [("312", 1)]),
    "manganese":     ("Manganese",         "mg",   [("315", 1)]),
    "selenium":      ("Selenium",          "µg",   [("317", 1)]),
    "vit_a":         ("Vitamin A (RAE)",   "µg",   [("320", 1)]),
    "vit_e":         ("Vitamin E",         "mg",   [("323", 1)]),
    "vit_d":         ("Vitamin D",         "µg",   [("328", 1), ("324", 0.025)]),  # IU -> µg
    "vit_c":         ("Vitamin C",         "mg",   [("401", 1)]),
    "thiamin":       ("Thiamin (B1)",      "mg",   [("404", 1)]),
    "riboflavin":    ("Riboflavin (B2)",   "mg",   [("405", 1)]),
    "niacin":        ("Niacin (B3)",       "mg",   [("406", 1)]),
    "pantothenic":   ("Pantothenic (B5)",  "mg",   [("410", 1)]),
    "vit_b6":        ("Vitamin B6",        "mg",   [("415", 1)]),
    "folate":        ("Folate (DFE)",      "µg",   [("435", 1), ("417", 1)]),
    "vit_b12":       ("Vitamin B12",       "µg",   [("418", 1)]),
    "vit_k":         ("Vitamin K",         "µg",   [("430", 1)]),
    "choline":       ("Choline",           "mg",   [("421", 1)]),
    # Indispensable amino acids, USDA g/100 g -> mg. Sulfur and aromatic pairs are summed (SUMS below),
    # because the WHO requirement is for methionine + cysteine and phenylalanine + tyrosine together.
    "his":           ("Histidine",         "mg",   [("512", 1000)]),
    "ile":           ("Isoleucine",        "mg",   [("503", 1000)]),
    "leu":           ("Leucine",           "mg",   [("504", 1000)]),
    "lys":           ("Lysine",            "mg",   [("505", 1000)]),
    "saa":           ("Methionine + cysteine", "mg", []),
    "aaa":           ("Phenylalanine + tyrosine", "mg", []),
    "thr":           ("Threonine",         "mg",   [("502", 1000)]),
    "trp":           ("Tryptophan",        "mg",   [("501", 1000)]),
    "val":           ("Valine",            "mg",   [("510", 1000)]),
    # Contaminants, µg per serving: not in USDA data, estimated from FDA Total Diet Study means (contaminants.py).
    "c_hg":          ("Mercury",           "µg",   []),
    "c_cd":          ("Cadmium",           "µg",   []),
    "c_pb":          ("Lead",              "µg",   []),
    "c_as":          ("Inorganic arsenic", "µg",   []),
}
AMINO_KEYS = ("his", "ile", "leu", "lys", "saa", "aaa", "thr", "trp", "val")
CONTAMINANT_KEYS = ("c_hg", "c_cd", "c_pb", "c_as")
SUMS = {"saa": ("506", "507"), "aaa": ("508", "509")}   # methionine + cystine, phenylalanine + tyrosine
CORE_KEYS = [k for k in NUTRIENTS if k not in AMINO_KEYS + CONTAMINANT_KEYS]   # the nutrients every USDA food is expected to have


def extract_per_100g(food_nutrients):
    """Turn an FDC foodNutrients list into {key: value per 100 g}.

    Handles both response shapes:
      search results:  {"nutrientNumber": "203", "value": 10.2, ...}
      food details:    {"nutrient": {"number": "203", ...}, "amount": 10.2}
    Nutrients the food has no data for are left out (not set to 0), so the
    optimizer can warn about missing data instead of silently trusting a zero.
    """
    by_number = {}
    for item in food_nutrients or []:
        if "nutrient" in item:
            number = str(item["nutrient"].get("number", ""))
            value = item.get("amount")
        else:
            number = str(item.get("nutrientNumber", ""))
            value = item.get("value")
        if number and value is not None:
            by_number.setdefault(number, float(value))

    out = {}
    for key, (_, _, codes) in NUTRIENTS.items():
        for number, factor in codes:
            if number in by_number:
                out[key] = by_number[number] * factor
                break
    for key, numbers in SUMS.items():
        if all(n in by_number for n in numbers):
            out[key] = sum(by_number[n] for n in numbers) * 1000
    return out
