"""Imports this week's flyers from eTilbudsavis (Tjek), which publishes the
chains' kundeaviser with each offer already split out: heading, description,
price, before-price, size and pack count.

Prices and sizes are taken as Tjek gives them. Only the product names need
work, since they must match the names receipts get ("KJØTTDEIG 10 % FETT"
should become "Kjøttdeig"): Gemini does that in one text-only call per
flyer, or, without Gemini, names already in the database are matched in the
heading."""
import asyncio
import json
import logging
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.basket import CHAINS, chain_name
from app.config import FLYER_REFRESH_HOURS, GEMINI_MODEL
from app.database import SessionLocal
from app.extractor import DEFINITIONS, client, json_answer, known_names
from app.names import Names, normalize_name
from app.schema import Category
from app.worker import known_product_names, pieces_are_one_pack

logger = logging.getLogger("sjekka.flyers")

API = "https://squid-api.tjek.com/v2"
OSLO_POINT = (59.9139, 10.7522)
RADIUS_M = 20000
USER_AGENT = "sjekka (+https://sjekka.tischbein.dev)"
OSLO = ZoneInfo("Europe/Oslo")
PAGE = 100
UNITS = {"g": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000), "cl": ("ml", 10), "dl": ("ml", 100)}
KNOWN_CHAINS = {name for _, name in CHAINS}
# Same names, other trades: Obs BYGG and Spar Kjøp sell building supplies
NOT_GROCERY = re.compile(r"bygg|kjøp", re.IGNORECASE)


def grocery_catalogs(client: httpx.Client, lat: float, lng: float, radius_m: int) -> list[dict]:
    """The flyers near a point from the grocery chains Sjekka knows."""
    res = client.get(f"{API}/catalogs", params={"r_lat": lat, "r_lng": lng, "r_radius": radius_m, "limit": 100})
    res.raise_for_status()
    return [c for c in res.json()
            if chain_name(c.get("branding", {}).get("name")) in KNOWN_CHAINS
            and not NOT_GROCERY.search(c.get("branding", {}).get("name") or "")]


def catalog_offers(client: httpx.Client, catalog_id: str) -> list[dict]:
    offers, offset = [], 0
    while True:
        res = client.get(f"{API}/offers", params={"catalog_id": catalog_id, "limit": PAGE, "offset": offset})
        res.raise_for_status()
        page = res.json()
        offers += page
        if len(page) < PAGE:
            return offers
        offset += PAGE


def local_date(timestamp: str) -> date:
    """Tjek's times are UTC ("2026-10-18T22:00:00+0000" is Monday in Oslo)."""
    return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z").astimezone(OSLO).date()


def text_of(offer: dict) -> str:
    return " ".join(f"{offer.get('heading') or ''} {offer.get('description') or ''}".split())


# A "before" price more than this much above the offer is more likely a
# kilo price than a real discount ("Pr kg 149,00 Førpris 229,00" on a
# 14,90 item), so it's dropped
MAX_CLAIMED_DISCOUNT = 0.6


def before_price(offer: dict) -> float | None:
    """Tjek's pre_price, or the "Førpris 74,90" the description often has instead."""
    price = offer["pricing"].get("price")
    before = offer["pricing"].get("pre_price")
    if not before:
        m = re.search(r"førpris:?\s*(\d+(?:[.,]\d+)?)(?![\d,.]*\s*[-–])", offer.get("description") or "", re.IGNORECASE)
        before = float(m.group(1).replace(",", ".")) if m else None
    if not before or not price or before <= price or 1 - price / before > MAX_CLAIMED_DISCOUNT:
        return None
    return before


def size_of(offer: dict) -> tuple[float | None, str | None, int]:
    """(quantity of one item, unit, how many items the price is for)."""
    q = offer.get("quantity") or {}
    pieces = (q.get("pieces") or {}).get("from") or 1
    unit = ((q.get("unit") or {}).get("symbol") or "").lower()
    size = q.get("size") or {}
    if unit in UNITS and size.get("from"):
        base, factor = UNITS[unit]
        # A range ("320-370 g", several varieties) is priced at its middle
        quantity = (size["from"] + (size.get("to") or size["from"])) / 2 * factor
        return quantity, base, int(pieces)
    return None, "stk", int(pieces)


class OfferName(BaseModel):
    index: int
    canonical_name: str
    brand: str | None = None
    variant: str | None = None
    category: Category
    # Coop Mega and Obs flyers also sell flooring, tools and the like
    grocery: bool = True


class OfferNames(BaseModel):
    offers: list[OfferName]


NAMES_PROMPT = """
Norwegian grocery flyer offers follow, one per line as "index | text".
For each, give the generic product name, brand and category, so the offer
can be matched with the same product on receipts.

{definitions}
Skip nothing: return one entry per index, in order.

**grocery**: false for anything that is not food, drink, hygiene, cleaning,
baby care, paper goods or pet food: building supplies, flooring, tools,
lamps, electronics, batteries, furniture, clothing, toys, garden and kitchen
equipment. Those get category "Annet" and are left out of Sjekka.

{lines}
"""


def gemini_names(offers: list[dict], known=()) -> tuple[dict[int, OfferName], str]:
    """One text-only Gemini call naming every offer. Returns names by index
    and the raw answer."""
    lines = "\n".join(f"{i} | {text_of(o)}" for i, o in enumerate(offers))
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[NAMES_PROMPT.format(definitions=DEFINITIONS, lines=lines) + known_names(known)],
        config=json_answer(OfferNames),
    )
    raw = response.text
    return {n.index: n for n in OfferNames.model_validate_json(raw).offers}, raw


def matched_names(offers: list[dict], known: list[str]) -> tuple[dict[int, OfferName], str]:
    """Without Gemini: the longest product name we already have that appears
    as a word in the heading, else the heading itself."""
    by_length = sorted(known, key=len, reverse=True)
    names = {}
    for i, o in enumerate(offers):
        heading = (o.get("heading") or "").strip(" *")
        hit = next((k for k in by_length if re.search(rf"(?<!\w){re.escape(k.lower())}(?!\w)", heading.lower())), None)
        names[i] = OfferName(index=i, canonical_name=hit or normalize_name(heading) or "Ukjent", category="Annet")
    return names, json.dumps({"matched_without_gemini": True})


def import_catalog(db: Session, catalog: dict, offers: list[dict], namer) -> models.Flyer | None:
    """Stores one Tjek catalog as a flyer, replacing an earlier import of it.
    `namer(offers)` returns (names by index, raw answer)."""
    chain = chain_name(catalog["branding"]["name"])
    source = f"tjek:{catalog['id']}"
    for old in db.query(models.Flyer).filter_by(source=source):
        db.delete(old)
    priced = [o for o in offers if (o.get("pricing") or {}).get("price")]
    if not priced:
        db.commit()
        return None
    names, raw = namer(priced)

    flyer = models.Flyer(
        chain=chain, valid_from=local_date(catalog["run_from"]), valid_to=local_date(catalog["run_till"]),
        source=source, model=GEMINI_MODEL if namer is gemini_names else None,
        raw_response=json.dumps({"tjek": priced, "names": raw}, ensure_ascii=False),
    )
    in_use = Names(db)
    for i, o in enumerate(priced):
        name = names.get(i)
        if not name or not name.grocery:
            continue
        quantity, unit, pack = size_of(o)
        # A 4-pack is one thing bought, of four times the size, not "4 for 20"
        if pieces_are_one_pack(text_of(o), pack):
            quantity, pack = quantity * pack if quantity else None, 1
        price = o["pricing"]["price"]
        each = price / pack
        tidied = in_use.tidy(name.canonical_name, name.brand, name.variant)
        flyer.offers.append(models.FlyerOffer(
            raw_name=text_of(o)[:300],
            canonical_name=tidied[0], brand=tidied[1], variant=tidied[2], category=name.category,
            quantity=quantity, unit=unit, pack_size=pack, total_price=price,
            price_per_unit=round(each / quantity * 100, 2) if quantity else round(each, 2),
            pre_price=before_price(o),
            deal=f"{pack} for {price:g}" if pack > 1 else None,
        ))
    db.add(flyer)
    db.commit()
    return flyer


def refresh(db: Session, client: httpx.Client, namer, lat: float = OSLO_POINT[0], lng: float = OSLO_POINT[1],
            radius_m: int = RADIUS_M, chains: set[str] | None = None, only_new: bool = False):
    """Imports the flyers near a point, one at a time, yielding (label, flyer
    or None, error or None). With `only_new`, flyers imported before are
    skipped, so each one costs a single Gemini call."""
    done = {s for (s,) in db.query(models.Flyer.source)} if only_new else set()
    for catalog in grocery_catalogs(client, lat, lng, radius_m):
        chain = chain_name(catalog["branding"]["name"])
        if (chains and chain not in chains) or f"tjek:{catalog['id']}" in done:
            continue
        label = f"{chain} «{catalog.get('label') or catalog['id']}»"
        try:
            yield label, import_catalog(db, catalog, catalog_offers(client, catalog["id"]), namer), None
        except Exception as e:  # report it and go on with the next flyer
            db.rollback()
            yield label, None, e


def refresh_new_flyers(client: httpx.Client | None = None) -> int:
    """One pass of the background refresh. Returns how many flyers it imported."""
    client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
    imported = 0
    with SessionLocal() as db:
        # Offers named like the receipts and flyers already in, so they meet
        known = known_product_names(db)
        for label, flyer, error in refresh(db, client, lambda offers: gemini_names(offers, known), only_new=True):
            if error:
                logger.error(f"Flyer {label}: {error}")
            elif flyer:
                imported += 1
                logger.info(f"Flyer {label}: {len(flyer.offers)} offers, {flyer.valid_from} to {flyer.valid_to}")
    return imported


async def run_refresh():
    """Looks for new flyers every FLYER_REFRESH_HOURS. The chains start their
    weeks on different days, so it checks more often than weekly; flyers it
    has already imported cost nothing."""
    while True:
        try:
            await asyncio.to_thread(refresh_new_flyers)
        except Exception as e:  # Tjek unreachable and the like: try again next time
            logger.error(f"Flyer refresh failed: {e}")
        await asyncio.sleep(FLYER_REFRESH_HOURS * 3600)
