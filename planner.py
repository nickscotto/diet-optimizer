"""Pick foods AND their meals in one optimization.

Variables: u[i,m] half-servings of food i in meal m; y[i,m] = food i is in
meal m; w[i] = food i is used at all. Constraints and costs:

  * Daily nutrient tiers (RDA / goal / upper limit), summed across meals,
    plus a soft ceiling at 130% of each vitamin and mineral goal so the plan
    lands near the goal instead of drifting toward the upper limit.
  * Each food only goes in meals its role and Epicure sweet/savory score
    allow (roles.py): no lentils at breakfast, no oats at dinner.
  * Each meal has a shape: breakfast = a base (grain or fruit) with at least
    15 g protein; lunch and dinner = a protein main (15 g+), vegetables
    (150 g+) and a starch. Missing a piece costs points rather than making
    the plan impossible.
  * Each meal carries 25-40% of the day's calories and about a third of the
    protein, at most six foods, at most two protein foods and one drink,
    and no scrap portions (a protein, starch or fruit is at least one serving,
    except foods the user likes, which can be a half serving).
  * Fewest foods overall, preferring liked foods and foods with complete
    USDA data. Each food costs FOOD_WEIGHT penalty points, so one is dropped
    only when that costs less than a point of nutrient coverage.
"""
import re

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

import aminos
import epicure
import recipes
from nutrients import CONTAMINANT_KEYS
from roles import MEALS

M = len(MEALS)
MAX_PENALTY_MULT = 10     # exceeding a max is 10x worse than missing a min, per %
GOAL_PENALTY_MULT = 3.0   # missing the higher-end goal, per % (must outweigh the over-goal penalty)
OVER_PENALTY_MULT = 0.4   # going past OVER_FACTOR x goal, per %: aim for the goal, not the upper limit
OVER_FACTOR = 1.3
COST_PTS_PER_USD = 12.0   # low-cost mode: each dollar a day of food costs this many points (a dollar is worth about a 12% miss on one nutrient)
PUSH_PENALTY_MULT = 1.5   # a target with `push`: every % short of the push value costs this much, so it lands as high as it can
BODY_KEYS = ("energy", "protein", "fat")   # these have their own min/max, no over-goal band
# Contaminants (mercury, cadmium, lead, arsenic): penalty points per % of the daily limit, past 30% and, in addition, past 60%.
# Reaching the limit from 30% costs ~9.5 points, about a 10% miss on one weight-1 nutrient minimum; past the limit it is MAX_PENALTY_MULT.
SOFT_PENALTY_MULT = {"soft1": 0.4, "soft2": 1.0}
FOOD_WEIGHT = 0.8         # penalty points per distinct food
MISSING_DATA_COST = 0.15  # per tracked nutrient USDA has no value for

MAIN_G, VEG_G, BASE_G, STARCH_G = 15, 150, 120, 100   # shape of a meal
MAIN_PTS, VEG_PTS, BASE_PTS, STARCH_PTS = 2.0, 0.2, 0.2, 0.08   # penalty per gram short
KCAL_SHARE = (0.25, 0.40)
KCAL_PTS, PROT_DEV_PTS = 0.08, 0.15
MIP_GAP = 0.05
DIVE_DROP, DIVE_LOCK = 0.02, 2   # fraction of use below which the dive drops a food; foods locked in per round
POOL = 24                 # foods the exact solve searches (the rest are screened by the LP first)
CLASH_BELOW = 0.075       # Epicure cosine under this = the two foods don't go together (oats ~ mushroom is -0.002;
PAIR_ROLES = {"starch", "fruit", "veg", "protein"}   # egg ~ spinach, the weakest pair people eat, is 0.08)
MAX_PER_MEAL = 6
MAX_PROTEIN_FOODS = 2     # per meal; one main plus at most one more
MAX_FRUIT_FOODS, MAX_STARCH_FOODS = 2, 2
# Most of one food in a day, in grams (liked foods have a calorie cap too): stops 1 kg of broccoli.
DAY_GRAM_CAP = {"veg": 250, "fruit": 300, "fat": 60, "other": 150, "protein": 450, "drink": 500}
LEUCINE_PTS = 0.0008      # penalty per mg of leucine a meal falls short of aminos.MEAL_LEUCINE_MG (2.5 g = 2 points)
MIN_UNITS = {"protein": 2, "starch": 2, "fruit": 2}   # smallest portion, in half-servings (others: 1);
# a food can override with its own `min_units` (liked foods use 1: any amount is welcome)


HOT_PEPPER = re.compile(r"^(jalape|serrano|habanero|chile|chili|cayenne)", re.I)
HOT_PEPPER_G = 30        # a garnish: the most hot pepper in a day, in grams


def _max_servings(f, step):
    if HOT_PEPPER.search(f["name"]):
        return HOT_PEPPER_G / f["serving_g"]
    servings = f.get("max_servings", 3)
    if f.get("max_g"):
        servings = min(servings, f["max_g"] / f["serving_g"])
    grams = DAY_GRAM_CAP.get(f["role"])
    if grams:
        servings = min(servings, max(step, (grams / f["serving_g"]) // step * step))
    return servings


def _solve(foods, targets, step, required, max_foods, time_limit, extra, relax=False, fast=True):
    """One optimization over `foods`. relax=True drops the whole-number requirements (and the pairing bans) and
    returns the raw solution vector; otherwise returns (servings[n][M], totals-by-nutrient-key)."""
    from nutrients import NUTRIENTS
    n = len(foods)
    keys = list(targets)
    A = np.array([[f["per_serving"].get(k, 0.0) * step for f in foods] for k in keys])
    prot = np.array([f["per_serving"].get("protein", 0.0) * step for f in foods])
    kcal = np.array([f["per_serving"].get("energy", 0.0) * step for f in foods])
    leu = np.array([f["per_serving"].get("leu", 0.0) * step for f in foods])
    gram = np.array([f["serving_g"] * step for f in foods])
    role = [f["role"] for f in foods]
    seasoning = [r == "seasoning" for r in role]
    cap = [_max_servings(f, step) / step for f in foods]
    kind = [recipes.kind(f) for f in foods]
    P_goal = targets["protein"].get("push", targets["protein"]["goal"])
    K_goal = targets["energy"]["goal"]

    def tier_value(t, tier):
        return t["goal"] * OVER_FACTOR if tier == "over" else t[tier]

    tiers = [(tier, j, k) for tier in ("min", "goal", "push", "over", "soft1", "soft2", "max") for j, k in enumerate(keys)
             if (tier in targets[k] and tier != "over")
             or (tier == "over" and "goal" in targets[k] and "push" not in targets[k] and k not in BODY_KEYS)]
    # variable layout
    U, Y = 0, n * M
    W = 2 * n * M
    T = W + n
    S = T + len(tiers)                    # per-meal structure slacks, 9 each
    whole = [i for i, f in enumerate(foods) if f.get("whole")]    # foods that come in whole servings (eggs): u = q / step, q integer
    Q0 = S + 9 * M
    n_vars = Q0 + len(whole) * M
    q = lambda k, m: Q0 + k * M + m
    u = lambda i, m: U + i * M + m
    y = lambda i, m: Y + i * M + m
    s = lambda m, k: S + m * 9 + k        # 0 main, 1 veg, 2 base, 3 starch, 4 kcal low, 5 kcal high, 6/7 protein dev, 8 leucine low

    clash = [] if relax else _clashes(foods) + _juice_clashes(foods)
    slack_cost = np.zeros(n_vars)
    rows, lo, hi = [], [], []

    def add(coefs, low, high):
        row = np.zeros(n_vars)
        for idx, v in coefs:
            row[idx] += v
        rows.append(row); lo.append(low); hi.append(high)

    # --- daily nutrient tiers
    for r, (tier, j, k) in enumerate(tiers):
        t = targets[k]
        val, w = tier_value(t, tier), t.get("weight", 1.0)
        coefs = [(u(i, m), A[j][i]) for i in range(n) for m in range(M) if A[j][i]]
        if tier in ("max", "over", "soft1", "soft2"):
            add(coefs + [(T + r, -1)], -np.inf, val)
            if tier in SOFT_PENALTY_MULT:    # contaminants: cost per % of the daily limit, from 30% and from 60% of it
                slack_cost[T + r] = SOFT_PENALTY_MULT[tier] * w * 100.0 / t["max"]
            else:
                slack_cost[T + r] = (MAX_PENALTY_MULT if tier == "max" else OVER_PENALTY_MULT) * w * 100.0 / val
        else:
            add(coefs + [(T + r, 1)], val, np.inf)
            mult = {"goal": GOAL_PENALTY_MULT, "push": PUSH_PENALTY_MULT}.get(tier, 1.0)
            slack_cost[T + r] = mult * w * 100.0 / val

    # --- linking foods, meals and totals
    for i in range(n):
        add([(u(i, m), 1) for m in range(M)], 0, cap[i])
        add([(y(i, m), 1) for m in range(M)] + [(W + i, -1)], 0, np.inf)
        for m in range(M):
            add([(u(i, m), 1), (y(i, m), -cap[i])], -np.inf, 0)
            add([(u(i, m), 1), (y(i, m), -foods[i].get("min_units", MIN_UNITS.get(role[i], 1)))], 0, np.inf)
            add([(y(i, m), 1), (W + i, -1)], -np.inf, 0)
            slack_cost[y(i, m)] = foods[i]["fit"][m]
    if max_foods:
        add([(W + i, 1) for i in range(n) if not seasoning[i]], 0, max_foods)

    # --- shape of each meal
    for m in range(M):
        real = [i for i in range(n) if not seasoning[i]]
        add([(y(i, m), 1) for i in real], 0, MAX_PER_MEAL + extra)
        add([(y(i, m), 1) for i in real], 2, np.inf)
        add([(y(i, m), 1) for i in real if role[i] == "protein"], 0, MAX_PROTEIN_FOODS + extra)
        add([(y(i, m), 1) for i in real if role[i] == "drink"], 0, 1)
        add([(y(i, m), 1) for i in real if role[i] == "fruit"], 0, MAX_FRUIT_FOODS + extra)
        add([(y(i, m), 1) for i in real if role[i] == "starch"], 0, MAX_STARCH_FOODS + extra)
        same = {}   # two tofus, two bean dishes or two oils in one meal read as a mistake
        for i in real:
            if kind[i] != role[i]:
                same.setdefault((kind[i], role[i]), []).append(i)
        for group in same.values():
            if len(group) > 1:
                add([(y(i, m), 1) for i in group], 0, 1 + extra)
        if not relax:   # foods the recipe data says don't go together (oats and mushrooms) never share a meal
            for i, j in clash:
                if foods[i]["allowed"][m] and foods[j]["allowed"][m]:
                    add([(y(i, m), 1), (y(j, m), 1)], 0, 1)
        add([(u(i, m), prot[i]) for i in real] + [(s(m, 6), -1), (s(m, 7), 1)], P_goal / 3, P_goal / 3)
        if "leu" in targets:   # each meal reaches a muscle-building leucine dose (digestible leucine)
            add([(u(i, m), leu[i]) for i in real] + [(s(m, 8), 1)], aminos.MEAL_LEUCINE_MG, np.inf)
            slack_cost[s(m, 8)] = LEUCINE_PTS
        add([(u(i, m), kcal[i]) for i in real] + [(s(m, 4), 1)], K_goal * KCAL_SHARE[0], np.inf)
        add([(u(i, m), kcal[i]) for i in real] + [(s(m, 5), -1)], -np.inf, K_goal * KCAL_SHARE[1])
        if m == 0:   # breakfast: protein from anything, plus a grain or fruit base
            add([(u(i, m), prot[i]) for i in real] + [(s(m, 0), 1)], MAIN_G, np.inf)
            add([(u(i, m), gram[i]) for i in real if role[i] in ("starch", "fruit")] + [(s(m, 2), 1)], BASE_G, np.inf)
        else:        # lunch / dinner: protein main, vegetables, starch
            add([(u(i, m), prot[i]) for i in real if role[i] == "protein"] + [(s(m, 0), 1)], MAIN_G, np.inf)
            add([(u(i, m), gram[i]) for i in real if role[i] == "veg"] + [(s(m, 1), 1)], VEG_G, np.inf)
            add([(u(i, m), gram[i]) for i in real if role[i] == "starch"] + [(s(m, 3), 1)], STARCH_G, np.inf)
        for k, pts in enumerate((MAIN_PTS, VEG_PTS, BASE_PTS, STARCH_PTS, KCAL_PTS, KCAL_PTS)):
            slack_cost[s(m, k)] = pts
        slack_cost[s(m, 6)] = slack_cost[s(m, 7)] = PROT_DEV_PTS

    lb, ub = np.zeros(n_vars), np.full(n_vars, np.inf)
    for i, f in enumerate(foods):
        ub[W + i] = 1
        if f.get("always") or f.get("force") or f["name"] in required:
            lb[W + i] = 1
        for m in range(M):
            ub[u(i, m)] = cap[i] if f["allowed"][m] else 0
            ub[y(i, m)] = 1 if f["allowed"][m] else 0
    for k, i in enumerate(whole):
        for m in range(M):
            ub[q(k, m)] = int(ub[u(i, m)] * step + 1e-9)
            add([(u(i, m), 1.0), (q(k, m), -1.0 / step)], 0, 0)
    integrality = np.zeros(n_vars)
    if not relax:
        integrality[Y:T] = 1
        integrality[Q0:] = 1       # which foods go in which meals; portions are made whole in the last pass

    for i, f in enumerate(foods):    # low-cost mode: price per serving, set by the caller
        if f.get("price_per_serving"):
            for m in range(M):
                slack_cost[u(i, m)] += COST_PTS_PER_USD * f["price_per_serving"] * step
    food_cost = np.zeros(n_vars)
    for i, f in enumerate(foods):
        tracked = [k for k in f["missing"] if k in targets and k not in CONTAMINANT_KEYS]
        food_cost[W + i] = f.get("cost", 1.0) + MISSING_DATA_COST * len(tracked)

    cost, cons = slack_cost + FOOD_WEIGHT * food_cost, LinearConstraint(np.array(rows), lo, hi)
    lp = lambda lo_, hi_: milp(cost, constraints=cons, integrality=np.zeros(n_vars), bounds=Bounds(lo_, hi_))
    if relax:
        res = lp(lb, ub)
        if res.x is None:
            raise RuntimeError(f"Solver failed: {res.message} (check required foods / max_foods)")
        return res.x, W
    fail = lambda r: RuntimeError(f"Solver failed: {r.message} (check required foods / max_foods)")
    if fast:
        # Pass 1, a dive: solve the relaxed problem, drop the foods it barely uses, lock in the ones it uses most, repeat.
        # A lock-in that makes the next relaxed problem infeasible (say a seventh protein food) is undone and those foods dropped.
        lo1, ub1, open_ = lb.copy(), ub.copy(), {i for i in range(n) if lb[W + i] < 1}
        saved, locked, res = None, [], None
        for _ in range(3 * n):
            trial = lp(lo1, ub1)
            if trial.x is None:
                if saved is None:
                    raise fail(trial)
                lo1, ub1, open_ = saved
                for i in locked:
                    ub1[W + i] = 0; open_.discard(i)
                saved, locked = None, []
                continue
            res = trial
            w = res.x[W:W + n]
            saved = (lo1.copy(), ub1.copy(), set(open_))
            for i in [i for i in open_ if w[i] < DIVE_DROP]:
                ub1[W + i] = 0; open_.discard(i)
            locked = sorted(open_, key=lambda i: -w[i])[:DIVE_LOCK]
            for i in locked:
                lo1[W + i] = 1; open_.discard(i)
            if not open_:
                break
        x = res.x.copy()
        x[W:T] = lo1[W:T]
        # Pass 2: with those foods fixed, choose each one's meals. Pass 3: with the meals fixed, round to half-servings.
        passes = (((W, T), (Y, W)), ((Y, T), (0, Y)))
    else:
        # Exact search over foods and meals (slow: runs to the time limit), then pass 3.
        res = milp(cost, constraints=cons, integrality=integrality, bounds=Bounds(lb, ub),
                   options={"time_limit": time_limit, "mip_rel_gap": MIP_GAP})
        if res.x is None:
            raise fail(res)
        x, passes = res.x, (((Y, T), (0, Y)),)
    for k, (fixed, whole) in enumerate(passes):
        lb2, ub2 = lb.copy(), ub.copy()
        lb2[fixed[0]:fixed[1]] = ub2[fixed[0]:fixed[1]] = np.round(x[fixed[0]:fixed[1]])
        integ2 = np.zeros(n_vars)
        integ2[Q0:] = 1                        # whole-serving foods stay whole in every pass
        if k < len(passes) - 1:
            integ2[whole[0]:whole[1]] = 1      # the last pass leaves the amounts continuous: any number of grams
        res2 = milp(cost, constraints=cons, integrality=integ2, bounds=Bounds(lb2, ub2),
                    options={"time_limit": time_limit, "mip_rel_gap": MIP_GAP})
        if res2.x is None:
            if k == 0 and len(passes) == 2:
                raise fail(res2)
            break
        x = res2.x
    alloc = np.clip(x[:n * M], 0, None).reshape(n, M) * step
    grams = np.round(alloc * np.array([[f["serving_g"]] for f in foods]))      # whole grams; under 1 g is none
    alloc = grams / np.array([[f["serving_g"]] for f in foods])
    totals = {k: float(sum(f["per_serving"].get(k, 0.0) * alloc[i].sum() for i, f in enumerate(foods)))
              for k in NUTRIENTS}
    return alloc, totals


def _clashes(foods):
    """Index pairs of foods that never share a meal: the Epicure recipe similarity is under CLASH_BELOW."""
    ep = epicure.load()
    idx = [i for i, f in enumerate(foods)
           if f.get("epicure") and ep.has(f["epicure"]) and f["role"] in PAIR_ROLES]
    return [(i, j) for a, i in enumerate(idx) for j in idx[a + 1:]
            if ep.similarity(foods[i]["epicure"], foods[j]["epicure"]) < CLASH_BELOW]


def _fruit_word(name):
    return name.lower().split()[0].strip(",").rstrip("s") if name.split() else ""


def _juice_clashes(foods):
    """Index pairs (a juice, the fruit it is made from) that never share a meal: orange juice with oranges."""
    juices = [i for i, f in enumerate(foods) if "juice" in f["name"].lower() and f["role"] == "fruit"]
    return [(i, j) for i in juices for j, g in enumerate(foods)
            if j != i and g["role"] == "fruit" and "juice" not in g["name"].lower()
            and _fruit_word(g["name"]) == _fruit_word(foods[i]["name"])]


def _pooled(foods, targets, step, required, max_foods, time_limit, extra, fast):
    if len(foods) <= POOL:
        return _solve(foods, targets, step, required, max_foods, time_limit, extra, fast=fast)
    x, W = _solve(foods, targets, step, required, max_foods, time_limit, extra, relax=True)
    n = len(foods)
    amount = x[:n * M].reshape(n, M).sum(axis=1) / np.array([max(1, _max_servings(f, step) / step) for f in foods])
    wanted = amount + x[W:W + n] * 1e-3                       # servings used, as a share of the cap; ties by food-used
    must = {i for i, f in enumerate(foods) if f.get("always") or f.get("force") or f["name"] in required}
    keep = sorted(must | set(np.argsort(-wanted)[:POOL]))
    alloc_sub, _ = _solve([foods[i] for i in keep], targets, step, required, max_foods, time_limit, extra, fast=fast)
    alloc = np.zeros((n, M))
    alloc[keep] = alloc_sub
    from nutrients import NUTRIENTS
    totals = {k: float(sum(f["per_serving"].get(k, 0.0) * alloc[i].sum() for i, f in enumerate(foods)))
              for k in NUTRIENTS}
    return alloc, totals


def _misses(totals, targets):
    """How many nutrients are under their minimum or goal, or over their maximum."""
    return sum(1 for k, t in targets.items()
               if ("min" in t and totals[k] < t["min"] * 0.995) or ("max" in t and totals[k] > t["max"] * 1.005)
               or ("goal" in t and totals[k] < t["goal"] * 0.98))


def solve_day(foods, targets, step=0.5, required=(), max_foods=None, time_limit=10, extra=0):
    """foods need roles.annotate() fields. Returns (servings[n][M], totals-by-nutrient-key).
    `extra` loosens the per-meal food-count limits, so a required food that can't fit has room.

    Hundreds of candidate foods make the exact search crawl, so a large list is first solved as a plain LP
    (milliseconds): the foods it leans on, plus the next best by how much of each it wanted, become the pool.
    The pool is solved with a fast dive (about a second). When that leaves a target missed, the exact search
    runs too (several seconds) and the plan with fewer misses is kept."""
    try:
        fast = _pooled(foods, targets, step, required, max_foods, time_limit, extra, True)
    except RuntimeError:
        return _pooled(foods, targets, step, required, max_foods, time_limit, extra, False)
    if _misses(fast[1], targets) == 0:
        return fast
    try:
        exact = _pooled(foods, targets, step, required, max_foods, time_limit, extra, False)
    except RuntimeError:
        return fast
    return exact if _misses(exact[1], targets) <= _misses(fast[1], targets) else fast
