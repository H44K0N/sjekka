"""Dinner suggestions: whole meals priced from the same prices the shopping
list uses, receipts and this week's flyer offers alike.

Every ingredient is priced once, at every chain, by basket.price_list (so a
missing price is estimated and marked, a size is the usual one, and an offer
counts while it runs). A dinner is then arithmetic on those unit prices: its
share of each package, at each chain. The week plan picks dinners that share
packages (one bag of rice, one carton of cream), because buying packages whole
is what makes five dinners cost less than five single ones.

Nothing here is stored: it's recomputed from the current prices."""
import math
from dataclasses import dataclass

from app import basket
from app.recipes import BY_ID, RECIPES, Recipe

PORTIONS_MIN, PORTIONS_MAX = 1, 8
# A plan has at most this many dinners of one kind, so five days aren't five pastas
KIND_LIMITS = {"kjøtt": 1, "kylling": 2, "svin": 1, "fisk": 2, "vegetar": 1, "pølse": 1}
WEEKDAYS = ["Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag"]
# Dinners that belong on a Friday
FRIDAY = ("taco", "pizza", "burger")


@dataclass(frozen=True)
class Unit:
    """What one package of a product costs at one chain."""
    price: float
    estimated: bool
    offer_until: str | None
    deal: str | None
    brand: str | None
    size: str | None


def known_products(offers, deals) -> dict[str, int]:
    """Every product with a price, and at how many chains."""
    chains: dict[str, set[str]] = {}
    for product, chain in [*offers, *deals]:
        chains.setdefault(product, set()).add(chain)
    return {product: len(found) for product, found in chains.items()}


def choose(options: tuple[str, ...], known: dict[str, int]) -> str | None:
    """The listed product with the most prices behind it; the first listed on a tie."""
    found = [(-known[name], i, name) for i, name in enumerate(options) if name in known]
    return min(found)[2] if found else None


def resolve(recipe: Recipe, known: dict[str, int]) -> list[tuple] | None:
    """The recipe's ingredients with the product each is priced as; None when a
    main ingredient has no price at all. A minor one without a price is left out."""
    lines = []
    for ingredient in recipe.ingredients:
        product = choose(ingredient.options, known)
        if product is None and not ingredient.minor:
            return None
        lines.append((ingredient, product))
    return lines


def unit_prices(products: list[str], offers, deals) -> tuple[dict, dict]:
    """product -> chain -> Unit, with this week's offers and without them."""
    def priced(with_deals):
        result = basket.price_list([(name, 1) for name in products], offers, deals if with_deals else None)
        units: dict[str, dict[str, Unit]] = {}
        for line in result["items"]:
            units[line["name"]] = {
                chain: Unit(p["price"], p["estimated"], p["offer_until"], p["deal"], p["brand"], line["size"])
                for chain, p in line["prices"].items()}
        return units
    return priced(True), priced(False)


def money(value: float) -> float:
    return round(value, 2)


# A chain can only be named cheapest on prices that are mostly real: a total
# made of guesses is the lowest one often enough to win by luck
MOST_GUESSED = 0.5


# How many chains a dinner is compared at, cheapest first
COMPARED = 3


def solid_chains(totals: dict[str, float], guessed: dict[str, float]) -> list[str]:
    """The chains with mostly real prices, cheapest first."""
    return sorted((chain for chain in totals if guessed[chain] <= MOST_GUESSED), key=lambda c: (totals[c], c))


def pick_chain(totals: dict[str, float], guessed: dict[str, float]) -> str:
    """The cheapest chain among those with mostly real prices (any, if none has)."""
    return (solid_chains(totals, guessed) or sorted(totals, key=lambda c: (totals[c], c)))[0]


def compare(totals: dict[str, float], guessed: dict[str, float], divide: float) -> tuple[list[dict], int]:
    """What it costs at the best-known chains, and how many chains have enough
    prices to be compared at all: the cheapest is only the cheapest of those."""
    solid = solid_chains(totals, guessed)
    return [{"chain": c, "total": money(totals[c]), "per_portion": money(totals[c] / divide)}
            for c in solid[:COMPARED]], len(solid)


def plain_price(plain, units, product: str, chain: str) -> float:
    """The price without offers; a product only the flyers know has none, so no saving."""
    return plain.get(product, {}).get(chain, units[product][chain]).price


def price_recipe(recipe: Recipe, lines, portions: int, units, plain) -> dict:
    """One dinner at its cheapest chain."""
    scale = portions / 4
    chains = set.intersection(*(set(units[product]) for _, product in lines if product))
    totals = {chain: sum(i.share * scale * units[p][chain].price for i, p in lines if p) for chain in chains}
    n = sum(1 for _, p in lines if p)
    guessed = {c: sum(units[p][c].estimated for _, p in lines if p) / n for c in chains}
    chain = pick_chain(totals, guessed)
    at, compared = compare(totals, guessed, portions)
    ingredients, unpriced = [], []
    bought = 0.0
    for ingredient, product in lines:
        if product is None:
            unpriced.append(ingredient.label)
            continue
        unit = units[product][chain]
        count = max(1, math.ceil(ingredient.share * scale - 1e-9))
        bought += count * unit.price
        ingredients.append({
            "label": ingredient.label, "product": product, "count": count, "price": money(ingredient.share * scale * unit.price),
            "estimated": unit.estimated, "offer": bool(unit.offer_until), "offer_until": unit.offer_until,
            "deal": unit.deal, "brand": unit.brand,
        })
    total = totals[chain]
    saving = sum(i.share * scale * (plain_price(plain, units, p, chain) - units[p][chain].price) for i, p in lines if p)
    estimated = sum(1 for i in ingredients if i["estimated"])
    return {
        "id": recipe.id, "name": recipe.name, "blurb": recipe.blurb, "minutes": recipe.minutes, "kind": recipe.kind,
        "portions": portions, "chain": chain, "total": money(total), "per_portion": money(total / portions),
        "compare": at, "compared": compared,
        "buy_total": money(bought), "saving": money(max(saving, 0)),
        "offers": sum(1 for i in ingredients if i["offer"]),
        # Mostly guessed prices make the whole cost a guess
        "rough": estimated > len(ingredients) - estimated,
        "ingredients": ingredients, "unpriced": unpriced, "pantry": list(recipe.pantry), "steps": list(recipe.steps),
    }


def packages(selected: list[tuple], portions: int) -> dict[str, int]:
    """The whole packages a set of dinners needs: shares add up, then round up."""
    need: dict[str, float] = {}
    for lines in selected:
        for ingredient, product in lines:
            if product:
                need[product] = need.get(product, 0) + ingredient.share * portions / 4
    return {product: max(1, math.ceil(amount - 1e-9)) for product, amount in need.items()}


def buy_cost(counts: dict[str, int], chain: str, units) -> float:
    return sum(count * units[product][chain].price for product, count in counts.items())


def chain_totals(counts: dict[str, int], units) -> tuple[dict, dict]:
    """What the packages cost at each chain, and how much of that is guessed."""
    chains = set.intersection(*(set(units[product]) for product in counts))
    return ({chain: buy_cost(counts, chain, units) for chain in chains},
            {chain: sum(units[product][chain].estimated for product in counts) / len(counts) for chain in chains})


def cheapest_chain(counts: dict[str, int], units) -> str:
    return pick_chain(*chain_totals(counts, units))


def plan_week(priced: dict[str, dict], resolved: dict[str, list], units, plain, portions: int,
              chosen: list[str] | None = None, days: int = len(WEEKDAYS)) -> dict | None:
    """Five dinners that share packages, bought at the one cheapest chain."""
    available = [r.id for r in RECIPES if r.id in priced]
    if chosen is None:
        chosen = []
        kinds: dict[str, int] = {}
        while len(chosen) < days:
            best = None
            for rid in available:
                kind = priced[rid]["kind"]
                if rid in chosen or kinds.get(kind, 0) >= KIND_LIMITS.get(kind, 1):
                    continue
                counts = packages([resolved[i] for i in [*chosen, rid]], portions)
                cost = buy_cost(counts, cheapest_chain(counts, units), units)
                if best is None or cost < best[0]:
                    best = (cost, rid)
            if best is None:
                break
            chosen.append(best[1])
            kinds[priced[best[1]]["kind"]] = kinds.get(priced[best[1]]["kind"], 0) + 1
    chosen = [rid for rid in chosen if rid in priced][:days]
    if not chosen:
        return None
    # A Friday dinner last
    chosen.sort(key=lambda rid: rid in FRIDAY)

    counts = packages([resolved[rid] for rid in chosen], portions)
    totals, guessed = chain_totals(counts, units)
    chain = pick_chain(totals, guessed)
    total = totals[chain]
    at, compared = compare(totals, guessed, portions * len(chosen))
    apart = sum(buy_cost(packages([resolved[rid]], portions), chain, units) for rid in chosen)
    without_offers = sum(count * plain_price(plain, units, product, chain) for product, count in counts.items())
    # The packages are bought whole; the dinners use only their share of them
    used = sum(i.share * portions / 4 * units[p][chain].price for rid in chosen for i, p in resolved[rid] if p)
    used_in: dict[str, list[str]] = {}
    for rid in chosen:
        for _, product in resolved[rid]:
            if product and BY_ID[rid].name not in used_in.setdefault(product, []):
                used_in[product].append(BY_ID[rid].name)
    shopping = []
    for product, count in sorted(counts.items(), key=lambda kv: -units[kv[0]][chain].price * kv[1]):
        unit = units[product][chain]
        shopping.append({"product": product, "count": count, "price": money(count * unit.price), "size": unit.size,
                         "estimated": unit.estimated, "offer": bool(unit.offer_until), "used_in": used_in[product]})
    others = [rid for rid in sorted(available, key=lambda r: priced[r]["per_portion"]) if rid not in chosen]
    estimated = sum(1 for s in shopping if s["estimated"])
    return {
        "portions": portions, "chain": chain, "total": money(total),
        "per_portion": money(total / (portions * len(chosen))), "compare": at, "compared": compared,
        "used_total": money(used), "used_per_portion": money(used / (portions * len(chosen))),
        "shared_saving": money(max(apart - total, 0)), "offer_saving": money(max(without_offers - total, 0)),
        "rough": estimated > len(shopping) - estimated,
        "days": [{"day": WEEKDAYS[i], "id": rid, "name": priced[rid]["name"], "minutes": priced[rid]["minutes"],
                  "kind": priced[rid]["kind"], "per_portion": priced[rid]["per_portion"],
                  "offers": priced[rid]["offers"], "alternatives": others[:6]}
                 for i, rid in enumerate(chosen)],
        "shopping": shopping,
    }


def suggest(offers, deals, portions: int = 4, chosen: list[str] | None = None, days: int = len(WEEKDAYS)) -> dict:
    """Every dinner that can be priced, cheapest first, and a plan for the week."""
    portions = max(PORTIONS_MIN, min(portions, PORTIONS_MAX))
    days = max(1, min(days, len(WEEKDAYS)))
    known = known_products(offers, deals)
    resolved = {recipe.id: lines for recipe in RECIPES if (lines := resolve(recipe, known))}
    products = sorted({product for lines in resolved.values() for _, product in lines if product})
    if not products:
        return {"portions": portions, "recipes": [], "plan": None}
    units, plain = unit_prices(products, offers, deals)
    priced = {recipe.id: price_recipe(recipe, resolved[recipe.id], portions, units, plain)
              for recipe in RECIPES if recipe.id in resolved}
    recipes = sorted(priced.values(), key=lambda r: (r["per_portion"], r["name"]))
    return {"portions": portions, "recipes": recipes,
            "plan": plan_week(priced, resolved, units, plain, portions, chosen, days)}
