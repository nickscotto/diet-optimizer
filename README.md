# Diet Optimizer: fat loss + muscle gain

Designs a day of eating (breakfast, lunch, dinner) from Aldi foods, using USDA
FoodData Central nutrient data. You give it your stats, diet and the foods you
like. It returns portions that hit your nutrient targets.

## What it works out

1. **Calories.** Maintenance from your stats (Mifflin-St Jeor times activity),
   minus a deficit sized to lose fat while keeping muscle.
2. **Lean mass.** From body fat % if you give it, otherwise from your waist
   (see below), otherwise from the Boer formula.
3. **Protein.** 1.6-2.2 g per kg of goal weight. Fat 20-35% of calories. Fiber
   scales with calories.
4. **Vitamins and minerals.** Canadian DRI values by age and sex (`dri.py`):
   a minimum you must hit, a higher goal for someone training, and a ceiling
   under the upper limit.
5. **Amino acids.** WHO/FAO targets per kg of body weight (`aminos.py`), plus
   about 2.5 g of leucine per meal for muscle building.
6. **Contaminants.** Mercury, cadmium, lead and inorganic arsenic. A soft
   penalty rises as a plan uses more of a daily safety limit (see below).

Targets are soft, so you always get the best plan available, with each
nutrient marked ok, below goal, LOW or HIGH.

## Setup and run

```bash
pip install -r requirements.txt
python app.py --key-file path/to/usda_api_key.txt     # http://127.0.0.1:8000
```

A free USDA API key takes a minute: https://fdc.nal.usda.gov/api-key-signup
(or set `FDC_API_KEY`). Without one it uses `DEMO_KEY`, which is rate limited.

The command-line version still works: `python fetch_foods.py` then
`python optimize.py`. It uses only the staples in `config.yaml`.

## How a plan is built

- **Food pool.** The 41 staples in `config.yaml` plus a shortlist from the
  built-in catalog (`data/catalog.json`, about 400 USDA foods). The shortlist
  keeps the most useful foods per role for your targets.
- **Aldi only.** Every food must be one Aldi sells (`aldi.py`). See below.
- **Solver.** A mixed-integer program (SciPy / HiGHS) picks which
  foods go in which meal, then sets each amount to the gram. `planner.py` first solves a fast
  relaxed version to narrow the pool to 24 foods, then a quick "dive" picks
  foods and meals. That takes about a second. If the quick plan misses a
  target, it runs the slower exact search too and keeps the better plan.
- **Meal shape.** A protein main plus vegetables and a starch at lunch and
  dinner, a grain or fruit base at breakfast, at most six foods per meal.
  Savory proteins and mushrooms stay out of breakfast.
- **Pairing.** Two foods never share a meal if the Epicure recipe data scores
  them below 0.075 (oats and mushroom, for example). `roles.py` decides each
  food's role and which meals fit it, using the
  [Epicure-Cooc](https://huggingface.co/Kaikaku/epicure-cooc) embeddings.
- **Liked foods.** Foods you add with the heart are a small tie-breaker only.
  Nutrient targets come first, so a liked food is left out if using it would
  cost coverage. The notes say which ones were left out.

## Body fat from waist

If you enter a waist size, body fat is estimated with the Relative Fat Mass
equation (Woolcott and Bergman 2018): `64 - 20 x height / waist`, plus 12 for
women, kept between 5% and 55%. A pants tag size gets 1 inch added, since tags
run small (rough figure; `PANT_TAG_EXTRA_IN` in `optimize.py`). Choose "tape at
navel" to use your number as typed.

## Contaminants

`build_contaminants.py` reads FDA Total Diet Study data (2018-2020 means) into
`data/contaminants.json`. `contaminants.py` maps each food to a TDS entry and
sets daily limits per kg of body weight: mercury 0.1 ug (EPA), cadmium 0.357 ug
(EFSA), lead 0.5 ug (EFSA), inorganic arsenic 0.3 ug (EFSA). The penalty is
free below 30% of a limit and climbs steeply after that (`SOFT_PENALTY_MULT` in
`planner.py`).

Limits of the data: values are averages, and non-detects count as zero, so lead
in particular is probably understated. Inorganic arsenic is estimated from
total arsenic. Treat these as a nudge, not a measurement.

## The Aldi list

There is no public Aldi product list, and Aldi's terms bar scraping its site.
So `aldi.py` is hybrid:

- `TYPES` is a hand-written list of about 110 everyday Aldi foods. The catalog
  keeps one USDA entry per type (no three broccolis).
- `GENERIC` is a wider list of base names (tomato, salmon, oat...) that decides
  what a food you add yourself may be. It rejects things like bison or fast
  food.
- Checked against real Aldi data for zip 32940: Open Food Facts product names
  (`data/aldi_off_products.json`), a full-store pull of 1,853 titles
  (`data/aldi_store_titles.json`, partial: it lacks bananas, lentils and tofu),
  and per-item searches. Peas and carrots, arugula and lima beans were not
  found and are out. Other stores or zips may differ.

To add or drop a food, edit `TYPES` in `aldi.py`.

## Grocery basket

Each plan comes with an Aldi basket for the number of days you choose (default
4). `basket.py` adds up each food over the days, rounds up to whole packages,
prices them, and shows the leftover and a total. Prices are in
`data/aldi_prices.json`: one product per food type, taken from Aldi searches for
zip 32940 (`price_add.py` adds rows from a text table). Foods sold by the pound
are priced by weight. Weights for items sold "each" (a head of cauliflower, an
avocado) are estimates, and plan weights are USDA raw or cooked weights, so
the total is an estimate. Foods with no price on file are listed under the
basket. Prices change; rerun the searches to refresh them.

## Options on the page

- **Low FODMAP.** `fodmap.py` lists the Aldi food types rated low FODMAP, with daily gram caps on the borderline
  ones. Anything not on the list, including foods you add, is left out. The ratings are from memory of the Monash
  app, so check them there before using this for a real elimination diet.
- **Lowest cost.** Adds each food's Aldi price per gram to what the solver minimizes (`COST_PTS_PER_USD` in
  `planner.py`). Nutrient targets still come first. Foods with no price count as the median price.
- **Monthly cost.** The basket panel also shows a 30-day basket, in whole packages, assuming one plan every day.

## Customize (`config.yaml`)

- **profile**: age, height, weight, goal weight, activity, deficit %, optional
  `waist_in` and `waist_kind` (`pants` or `tape`).
- **goal**: protein, fat and fiber settings.
- **doctor_limits**: medical caps from your own doctors that override the rest
  (an example 130 g protein ceiling, off by default). Turn off only with their OK.
- **targets**: overrides for `min` / `goal` / `max` per nutrient.
- **foods**: the staples. Each has a USDA `query` (or pinned `fdc_id`),
  `serving_g`, a `label`, `max_servings`, and optionally `always: true`. For
  packaged products, `custom: true` with the label in `per_serving` (update the
  whey entry with your tub's numbers).

## Files

| File | What it does |
|---|---|
| `app.py` | Web server and API: profile in, plan out; USDA search; liked foods |
| `docs/index.html`, `docs/config.js` | The page (also what GitHub Pages serves) and the server address it calls |
| `planner.py` | The solver: foods, portions, meals, clash rules, speed-ups |
| `optimize.py` | Targets from your stats, body fat from waist, command-line plan |
| `roles.py` | Food roles and which meals suit each, from Epicure |
| `epicure.py` | Loads the Epicure embeddings and food pairing scores |
| `meals.py` | Turns the solution into breakfast, lunch and dinner |
| `recipes.py` | Recipe-style serving and food-kind rules |
| `catalog.py` | Built-in food catalog, Aldi filter and shortlist |
| `build_catalog.py` | Builds `data/catalog.json` and `data/catalog_review.txt` from USDA |
| `aldi.py` | What Aldi sells (hand list plus checks) |
| `fodmap.py` | Low-FODMAP food list and daily caps |
| `basket.py`, `price_add.py` | Grocery basket with prices; adds rows to `data/aldi_prices.json` |
| `dri.py` | Canadian DRI vitamin and mineral targets |
| `aminos.py` | Amino acid targets and digestibility |
| `contaminants.py`, `build_contaminants.py` | Safety limits and FDA contaminant data |
| `nutrients.py` | The 41 nutrients tracked and their USDA codes |
| `usda.py`, `fetch_foods.py` | USDA FoodData Central API and the staple cache |

Everything in `data/` is generated or downloaded: USDA caches, the catalog,
contaminant values, Epicure files, and the Aldi name lists.

## Notes

- **Data gaps.** A nutrient can look low only because USDA has no value for a
  food in the plan. The plan flags those.
- Iodine isn't tracked by USDA; iodized salt, eggs and dairy cover it.
- Vitamin A and K running far above goal is normal from vegetables.
- More iron isn't better for men; it's capped.
- Not covered: pregnancy and lactation targets.

This is a planning tool, not medical advice. Run the plan past your doctor or
dietitian, especially protein, sodium, iron and potassium.
