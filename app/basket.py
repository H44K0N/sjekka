"""Prices a whole shopping list in every chain, so people can see where the
list is cheapest, not just one product.

Receipts only cover what people happened to buy, so most chains are missing
some of the list. Those gaps are filled with an estimate: the product's
typical price, scaled by how expensive the chain is on the products where we
can compare. Estimates are always marked as such."""
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from statistics import median

# Gemini writes chain names loosely ("REMA 1000", "Rema", "Coop Extra
# Storo"), so they are grouped by pattern. Order matters: "Coop Mega" must not
# fall through to a looser match. Mirrors static/chains.js.
CHAINS = [
    (r"rema", "Rema 1000"),
    (r"kiwi", "Kiwi"),
    (r"meny", "Meny"),
    (r"prix", "Coop Prix"),
    (r"\bmega\b", "Coop Mega"),  # not Megaflis, a tile shop with flyers on Tjek
    (r"extra", "Extra"),
    (r"\bobs\b", "Obs"),
    (r"joker", "Joker"),
    (r"spar", "Spar"),
    (r"bunnpris", "Bunnpris"),
    (r"\boda\b", "Oda"),
]

# How many compared products count as much as the prior that a chain is
# average. Keeps one odd price from making a chain look cheap or expensive.
INDEX_PRIOR = 3
# A split across two stores is only worth suggesting when it saves at least
# this much, and this share of the list: a second stop costs time.
SPLIT_MIN_KR = 25
SPLIT_MIN_SHARE = 0.05


GROCERY_CHAINS = {name for _, name in CHAINS}


def chain_name(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    for pattern, name in CHAINS:
        if re.search(pattern, raw, re.IGNORECASE):
            return name
    return " ".join(raw.split()).title()


@dataclass
class Offer:
    """One product's latest known price at one chain."""
    product: str
    chain: str
    each: float                 # price of one item as bought
    per_unit: float | None      # per 100 g/ml, set only when the size is known
    unit: str | None
    quantity: float | None
    brand: str | None
    date: date | None
    until: date | None = None   # set for flyer offers: the last day they apply
    deal: str | None = None
    # Every priced line of the product at the chain, newest first (this one
    # among them): a product's typical size is judged from all it was bought
    # in, and a chain whose latest is a multipack still has a price for one
    history: list["Offer"] = field(default_factory=list)


def latest_offers(rows) -> dict[tuple[str, str], Offer]:
    """rows: (ReceiptItem, Receipt) pairs in any order. Keeps the newest
    priced line per product and chain."""
    lines: dict[tuple[str, str], list[Offer]] = {}
    for item, receipt in rows:
        chain = chain_name(receipt.store_chain)
        # Trumf also covers florists and the like (Mester Grønn): not a grocery store to compare
        if chain not in GROCERY_CHAINS or item.total_price is None or not item.canonical_name:
            continue
        sized = item.unit in ("g", "ml") and item.quantity and item.price_per_unit
        lines.setdefault((item.canonical_name, chain), []).append(Offer(
            product=item.canonical_name, chain=chain,
            each=item.total_price / max(item.pack_size or 1, 1),
            per_unit=item.price_per_unit if sized else None,
            unit=item.unit, quantity=item.quantity, brand=item.brand, date=receipt.purchase_date,
        ))
    offers = {}
    for key, history in lines.items():
        # Newest first; an undated line counts as older than any dated one
        history.sort(key=lambda o: o.date or date.min, reverse=True)
        offers[key] = history[0]
        history[0].history = history
    return offers


def by_product(offers: dict[tuple[str, str], Offer]) -> dict[str, dict[str, Offer]]:
    grouped: dict[str, dict[str, Offer]] = {}
    for (product, chain), offer in offers.items():
        grouped.setdefault(product, {})[chain] = offer
    return grouped


# A line more than twice the typical size isn't priced from (see scale);
# flyer verdicts call sizes this far apart different products
SIZE_RATIO = 2


def typical_size(quantities: list[float]) -> float:
    """The size to price a product at: the most common one, so the list
    says "for 1 l" rather than "for 1083 ml"; with no size more common than
    another, the median, rounded to two figures."""
    size, n = Counter(quantities).most_common(1)[0]
    return size if n > 1 else float(f"{median(quantities):.2g}")


def scale(offers: list[Offer]) -> tuple[Callable[[Offer], float], str | None, Callable[[Offer], Offer | None]]:
    """How to make one product's prices comparable.

    When most offers have a size in the same unit, prices are scaled to the
    product's typical size (so 0.5 l at one store and 1 l at another compare
    fairly), and offers without that kind of size can't be compared: a
    6-pack of apples against apples by the kilo. Nor is a price scaled down
    from a pack much bigger than that: a 9-litre multipack says little about
    a half-litre bottle, and would make its store look cheap. (Smaller is
    fine: one weighed apple is a price by the kilo.) So at each chain the
    newest line that fits is used, and a chain with none has no price.
    Otherwise the price of one item is used. Returns the pricing function, the size it
    prices, if any, and the line to price an offer by (None: none fits)."""
    sized = [o for o in offers if o.unit in ("g", "ml") and o.quantity and o.per_unit]
    if sized:
        unit, n = Counter(o.unit for o in sized).most_common(1)[0]
        if n * 2 > len(offers):
            # A flyer offer has only its own size
            size = typical_size([h.quantity for o in sized for h in o.history or [o] if h.unit == unit and h.quantity])

            def fits(o: Offer) -> bool:
                return o.unit == unit and bool(o.quantity and o.per_unit) and o.quantity <= size * SIZE_RATIO
            # From the price paid, not the rounded per_unit, so a store selling
            # exactly this size shows exactly what it charged
            return ((lambda o: o.each / o.quantity * size), f"{size:g} {unit}",
                    (lambda o: next((h for h in o.history or [o] if fits(h)), None)))
    return (lambda o: o.each), None, (lambda o: o)


def line_prices(chains: dict[str, Offer]) -> tuple[dict[str, float], str | None]:
    """What one product costs at each chain, made comparable (see scale)."""
    price, size, pick = scale(list(chains.values()))
    return {c: price(line) for c, o in chains.items() if (line := pick(o))}, size


def flyer_deals(rows) -> dict[tuple[str, str], Offer]:
    """rows: (FlyerOffer, Flyer) pairs valid today. Keeps the cheapest offer
    per product and chain."""
    deals: dict[tuple[str, str], Offer] = {}
    for item, flyer in rows:
        if item.total_price is None:
            continue
        sized = item.unit in ("g", "ml") and item.quantity and item.price_per_unit
        offer = Offer(
            product=item.canonical_name, chain=flyer.chain,
            each=item.total_price / max(item.pack_size or 1, 1),
            per_unit=item.price_per_unit if sized else None,
            unit=item.unit, quantity=item.quantity, brand=item.brand, date=flyer.valid_from,
            until=flyer.valid_to, deal=item.deal,
        )
        key = (offer.product, offer.chain)
        if key not in deals or offer.each < deals[key].each:
            deals[key] = offer
    return deals


def chain_index(products: dict[str, dict[str, float]]) -> dict[str, float]:
    """How expensive each chain is relative to the typical price, as a
    factor (0.9 = 10% cheaper). Only products sold at two or more chains say
    anything about this; the estimate leans towards 1 when there are few."""
    logs: dict[str, list[float]] = {}
    for prices in products.values():
        if len(prices) < 2:
            continue
        typical = median(prices.values())
        for chain, price in prices.items():
            if price > 0 and typical > 0:
                logs.setdefault(chain, []).append(math.log(price / typical))
    return {chain: math.exp(sum(values) / (len(values) + INDEX_PRIOR)) for chain, values in logs.items()}


def price_list(wanted: list[tuple[str, int]], offers: dict[tuple[str, str], Offer],
               deals: dict[tuple[str, str], Offer] | None = None) -> dict:
    """Prices `wanted` (product name, count) at every chain that has a known
    price for at least one of them. `deals` are flyer offers valid today:
    they're used where they beat the receipt price, but never count towards
    how expensive a chain usually is."""
    grouped = by_product(offers)
    on_offer = by_product(deals or {})
    index = chain_index({p: line_prices(chains)[0] for p, chains in grouped.items()})

    known = set(grouped) | set(on_offer)
    listed = [(name, count) for name, count in wanted if name in known]
    unknown = [name for name, _ in wanted if name not in known]
    # Each product's prices that can be compared (see scale)
    comparable = {}
    for name, _ in listed:
        regular, deal = grouped.get(name, {}), on_offer.get(name, {})
        price_of, size, pick = scale([*regular.values(), *deal.values()])
        comparable[name] = (price_of, size, {c: line for c, o in regular.items() if (line := pick(o))},
                            {c: line for c, o in deal.items() if (line := pick(o))})
    chains = sorted({c for _, _, regular, deal in comparable.values() for c in [*regular, *deal]})

    items = []
    # cost[chain][product] = (price for `count` items, estimated?)
    cost: dict[str, dict[str, tuple[float, bool]]] = {c: {} for c in chains}
    for name, count in listed:
        price_of, size, regular, deal = comparable[name]
        prices = {c: price_of(o) for c, o in regular.items()}
        # The typical price comes from receipts; offers only when there's nothing else
        typical = median(prices.values()) if prices else median(price_of(o) for o in deal.values())
        deal_used = {}
        for chain, o in deal.items():
            if chain not in prices or price_of(o) < prices[chain]:
                prices[chain] = price_of(o)
                deal_used[chain] = o

        line = {"name": name, "count": count, "size": size, "prices": {}}
        for chain in chains:
            is_known = chain in prices
            price = prices[chain] if is_known else typical * index.get(chain, 1.0)
            cost[chain][name] = (price * count, not is_known)
            offer = deal_used.get(chain) or regular.get(chain)
            line["prices"][chain] = {
                "price": round(price * count, 2),
                "estimated": not is_known,
                "date": str(offer.date) if offer and offer.date and not offer.until else None,
                "brand": offer.brand if offer else None,
                "offer_until": str(offer.until) if offer and offer.until else None,
                "deal": offer.deal if offer else None,
            }
        line["cheapest"] = min(prices, key=prices.get)
        items.append(line)

    stores = []
    for chain in chains:
        lines = cost[chain].values()
        stores.append({
            "chain": chain,
            "total": round(sum(p for p, _ in lines), 2),
            "known": sum(1 for _, est in lines if not est),
            "estimated": sum(1 for _, est in lines if est),
            "offers": sum(1 for line in items if line["prices"][chain]["offer_until"]),
            "index": round(index.get(chain, 1.0), 3),
        })
    stores.sort(key=lambda s: (s["total"], -s["known"]))

    return {
        "items": items,
        "unknown": unknown,
        "stores": stores,
        "split": best_split(cost, stores),
    }


def pick(cost: dict[str, dict[str, tuple[float, bool]]], name: str, a: str, b: str) -> str:
    """Which of two chains to buy a product at: where its price is known
    rather than estimated (a split shouldn't send anyone to a store on a
    guess), else the cheaper."""
    (price_a, guess_a), (price_b, guess_b) = cost[a][name], cost[b][name]
    if guess_a != guess_b:
        return b if guess_a else a
    return a if price_a <= price_b else b


def best_split(cost: dict[str, dict[str, tuple[float, bool]]], stores: list[dict]) -> dict | None:
    """The cheapest way to buy the list across two chains, when it saves
    enough over the cheapest single chain to be worth a second stop."""
    if len(stores) < 2:
        return None
    best_single = stores[0]["total"]
    chains = [s["chain"] for s in stores]
    best = None
    for i, a in enumerate(chains):
        for b in chains[i + 1:]:
            plan = {name: pick(cost, name, a, b) for name in cost[a]}
            if len(set(plan.values())) < 2:
                continue
            total = sum(cost[chain][name][0] for name, chain in plan.items())
            if best is None or total < best["total"]:
                best = {"chains": [a, b], "total": total, "plan": plan}
    if best is None:
        return None
    saving = best_single - best["total"]
    if saving < SPLIT_MIN_KR or saving < best_single * SPLIT_MIN_SHARE:
        return None
    best["total"] = round(best["total"], 2)
    best["saving"] = round(saving, 2)
    return best
