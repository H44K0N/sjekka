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


# "EGG FRITTGÅENDE 18STK", "6PK", "12 store egg": the pieces in one pack
PIECES = re.compile(r"(?<![\d,.x×])(\d{1,3})\s*-?\s*(?:stk|pk|pakk\w*|pack)\b|(?<![\d,.])(\d{1,3})\s+(?:\w+\s+)?egg\b",
                    re.IGNORECASE)


def pieces_named(raw_name: str | None) -> float | None:
    """The pieces in one pack, when the name says two or more."""
    match = PIECES.search(raw_name or "")
    count = int(match[1] or match[2]) if match else 0
    return float(count) if count >= 2 else None


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
    # A multi-buy ("8 for 100") is that price only when this many are bought
    needs: int = 1
    # The pieces the name says a pack of something weighed holds: a flyer
    # gives "COOP FROKOSTEGG 12 PK … 816 g" by weight, other eggs by count
    pieces: float | None = None
    # The receipt line or flyer offer this is, and its receipt or flyer
    line: object = None
    source: object = None
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
        sized = item.unit in SIZED and item.quantity and item.price_per_unit
        lines.setdefault((item.canonical_name, chain), []).append(Offer(
            product=item.canonical_name, chain=chain,
            each=item.total_price / max(item.pack_size or 1, 1),
            per_unit=item.price_per_unit if sized else None,
            unit=item.unit, quantity=item.quantity, brand=item.brand, date=receipt.purchase_date,
            pieces=pieces_named(item.raw_name) if item.unit != "stk" else None, line=item, source=receipt,
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


# Units a size is given in: weight, volume, or the pieces in a pack (12 eggs)
SIZED = ("g", "ml", "stk")
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
    fine: one weighed apple is a price by the kilo.) A product counted in
    pieces also fits a pack sold by weight whose name says how many it
    holds. A chain with no line that fits has no price. Otherwise the price of one item is used.
    Returns the pricing function, the size it prices, if any, and the line
    to price an offer by (see cheapest_choice; None: none fits)."""
    sized = [o for o in offers if o.unit in SIZED and o.quantity and o.per_unit]
    if sized:
        unit, n = Counter(o.unit for o in sized).most_common(1)[0]
        if n * 2 > len(offers):
            def size_of(o: Offer) -> float | None:
                """The line's size in the unit, or its pieces when the
                product is counted and this pack was weighed."""
                if o.unit == unit and o.quantity and o.per_unit:
                    return o.quantity
                return o.pieces if unit == "stk" else None
            # A flyer offer has only its own size
            size = typical_size([q for o in offers for h in o.history or [o] if (q := size_of(h))])

            def fits(o: Offer) -> bool:
                return bool(q := size_of(o)) and q <= size * SIZE_RATIO
            # From the price paid, not the rounded per_unit, so a store selling
            # exactly this size shows exactly what it charged
            def price_of(o: Offer) -> float:
                return o.each / size_of(o) * size
            return price_of, f"{size:g} {unit}", (lambda o: cheapest_choice(o, fits, price_of))
    return (lambda o: o.each), None, (lambda o: cheapest_choice(o, lambda h: True, lambda h: h.each))


# How far back a chain's other brands count as still on its shelf
CHOICE_DAYS = 180


def cheapest_choice(offer: Offer, fits: Callable[[Offer], bool], price_of: Callable[[Offer], float]) -> Offer | None:
    """The line to price a product at a chain by. A product is generic
    ("Pizza") and a chain sells several brands of it, so it's the cheapest
    a shopper could pick there: each brand's latest price, from the last
    CHOICE_DAYS before the newest line, cheapest by the comparable price.
    Lines that don't fit (see scale) aren't a choice."""
    lines = [h for h in offer.history or [offer] if fits(h)]
    if not lines:
        return None
    newest = lines[0].date
    latest: dict[str, Offer] = {}
    for line in lines:  # newest first, so the first per brand is its latest
        if newest and line.date and (newest - line.date).days > CHOICE_DAYS:
            continue
        latest.setdefault((line.brand or "").lower(), line)
    return min(latest.values(), key=price_of)


def choices(chains: dict[str, Offer]) -> tuple[Callable[[Offer], float], str | None, dict[str, Offer]]:
    """The line each chain is priced by (see scale and cheapest_choice),
    with the pricing function and size."""
    price, size, pick = scale(list(chains.values()))
    return price, size, {c: line for c, o in chains.items() if (line := pick(o))}


def line_prices(chains: dict[str, Offer]) -> tuple[dict[str, float], str | None]:
    """What one product costs at each chain, made comparable (see scale)."""
    price, size, lines = choices(chains)
    return {c: price(line) for c, line in lines.items()}, size


def flyer_deals(rows) -> dict[tuple[str, str], Offer]:
    """rows: (FlyerOffer, Flyer) pairs valid today. Keeps the cheapest offer
    per product and chain."""
    deals: dict[tuple[str, str], Offer] = {}
    for item, flyer in rows:
        if item.total_price is None:
            continue
        sized = item.unit in SIZED and item.quantity and item.price_per_unit
        offer = Offer(
            product=item.canonical_name, chain=flyer.chain,
            each=item.total_price / max(item.pack_size or 1, 1),
            per_unit=item.price_per_unit if sized else None,
            unit=item.unit, quantity=item.quantity, brand=item.brand, date=flyer.valid_from,
            until=flyer.valid_to, deal=item.deal,
            needs=max(item.pack_size or 1, 1) if item.deal else 1,
            pieces=pieces_named(item.raw_name) if item.unit != "stk" else None, line=item, source=flyer,
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
        deal_used, multi_buys = {}, []
        for chain, o in deal.items():
            # Without the offer: the receipt price, or the chain's estimate
            usual = prices.get(chain, typical * index.get(chain, 1.0))
            if chain in prices and price_of(o) >= usual:
                continue
            if count < o.needs:
                # "8 for 100" doesn't make one can cost 12.50: say what taking eight would cost
                if price_of(o) < usual:
                    multi_buys.append({"chain": chain, "deal": o.deal, "needs": o.needs,
                                       "each": round(price_of(o), 2), "until": str(o.until)})
                continue
            # The pieces beyond the last whole multi-buy cost what they usually do
            bought = count - count % o.needs
            prices[chain] = (bought * price_of(o) + (count - bought) * usual) / count
            deal_used[chain] = o

        line = {"name": name, "count": count, "size": size, "prices": {}, "multi_buys": multi_buys}
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
