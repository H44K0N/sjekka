"""Imports this week's flyers from eTilbudsavis (Tjek), which publishes the
chains' kundeaviser with each offer already split out: heading, description,
price, before-price, size and pack count.

Prices and sizes are taken as Tjek gives them. Only the product names need
work, since they must match the names receipts get ("KJØTTDEIG 10 % FETT"
should become "Kjøttdeig"): Gemini does that in one text-only call per
flyer, or, without Gemini, names already in the database are matched in the
heading."""
import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
from google.genai import types
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.basket import CHAINS, chain_name
from app.config import GEMINI_MODEL
from app.extractor import DEFINITIONS, client
from app.schema import Category
from app.worker import normalize_name

API = "https://squid-api.tjek.com/v2"
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


class OfferNames(BaseModel):
    offers: list[OfferName]


NAMES_PROMPT = """
Norwegian grocery flyer offers follow, one per line as "index | text".
For each, give the generic product name, brand and category, so the offer
can be matched with the same product on receipts.

{definitions}
Skip nothing: return one entry per index, in order. Non-grocery items get
category "Annet".

{lines}
"""


def gemini_names(offers: list[dict]) -> tuple[dict[int, OfferName], str]:
    """One text-only Gemini call naming every offer. Returns names by index
    and the raw answer."""
    lines = "\n".join(f"{i} | {text_of(o)}" for i, o in enumerate(offers))
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[NAMES_PROMPT.format(definitions=DEFINITIONS, lines=lines)],
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=OfferNames),
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
    for i, o in enumerate(priced):
        name = names.get(i)
        if not name:
            continue
        quantity, unit, pack = size_of(o)
        price = o["pricing"]["price"]
        each = price / pack
        flyer.offers.append(models.FlyerOffer(
            raw_name=text_of(o)[:300],
            canonical_name=normalize_name(name.canonical_name),
            brand=name.brand, variant=name.variant, category=name.category,
            quantity=quantity, unit=unit, pack_size=pack, total_price=price,
            price_per_unit=round(each / quantity * 100, 2) if quantity else round(each, 2),
            pre_price=before_price(o),
            deal=f"{pack} for {price:g}" if pack > 1 else None,
        ))
    db.add(flyer)
    db.commit()
    return flyer
