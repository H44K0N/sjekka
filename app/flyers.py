"""Offers from the chains' weekly flyers (kundeavis): importing them, and
judging whether an offer is actually cheaper than usual.

Flyers are imported by hand with util/import_flyer.py: a PDF or page images
go to Gemini, and the offers are stored with the dates they apply. Receipts
stay the source of truth for what things normally cost; offers are kept
apart and only count while they're valid."""
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app import models
from app.basket import chain_name
from app.config import GEMINI_MODEL
from app.extractor import extract_flyer
from app.schema import ExtractedFlyer
from app.worker import is_retryable, normalize_name, parse_date, unit_price

ATTEMPTS = 4
RETRY_SECONDS = 20
# How much cheaper than the usual price an offer has to be to count as a
# real offer, and how much for "a little cheaper"
REAL_DISCOUNT = 0.15
SMALL_DISCOUNT = 0.03


class FlyerError(Exception):
    """The flyer couldn't be imported; the message says why."""


def read_flyer(path: Path, sleep=time.sleep) -> str:
    """Gemini's raw answer for one file, retrying when Gemini is busy."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return extract_flyer(path)
        except Exception as e:
            if not is_retryable(e) or attempt == ATTEMPTS:
                raise
            sleep(RETRY_SECONDS * attempt)
    raise AssertionError("unreachable")


def import_flyer(db: Session, path: Path, chain: str | None = None, valid_from: date | None = None,
                 valid_to: date | None = None, read=read_flyer) -> models.Flyer:
    """Reads one flyer file and stores its offers. Chain and dates given here
    win over what Gemini read. Importing the same file for the same chain and
    dates again replaces the earlier import."""
    raw = read(path)
    try:
        data = ExtractedFlyer.model_validate_json(raw)
    except ValueError as e:
        raise FlyerError(f"Gemini returned an invalid answer for {path.name}: {e}") from None

    chain = chain_name(chain or data.store_chain)
    valid_from = valid_from or parse_date(data.valid_from)
    valid_to = valid_to or parse_date(data.valid_to)
    if not chain:
        raise FlyerError(f"No chain found in {path.name}; give it with --chain")
    if not valid_from or not valid_to:
        raise FlyerError(f"No dates found in {path.name}; give them with --from and --to")
    if valid_to < valid_from:
        raise FlyerError(f"The offers end ({valid_to}) before they start ({valid_from})")

    for old in db.query(models.Flyer).filter_by(chain=chain, valid_from=valid_from, valid_to=valid_to, source=path.name):
        db.delete(old)

    flyer = models.Flyer(chain=chain, valid_from=valid_from, valid_to=valid_to, source=path.name,
                         raw_response=raw, model=GEMINI_MODEL)
    for offer in data.offers:
        flyer.offers.append(models.FlyerOffer(
            raw_name=offer.raw_name,
            canonical_name=normalize_name(offer.canonical_name),
            brand=offer.brand.strip() if offer.brand else None,
            variant=offer.variant,
            category=offer.category,
            quantity=offer.quantity,
            unit=offer.unit,
            pack_size=max(offer.pack_size, 1),
            total_price=offer.total_price,
            price_per_unit=unit_price(offer),
            is_member_price=int(offer.is_member_price),
            deal=offer.deal,
        ))
    db.add(flyer)
    db.commit()
    return flyer


def active_offers(db: Session, today: date) -> list[tuple[models.FlyerOffer, models.Flyer]]:
    return (
        db.query(models.FlyerOffer, models.Flyer)
        .join(models.Flyer, models.FlyerOffer.flyer_id == models.Flyer.id)
        .filter(models.Flyer.valid_from <= today, models.Flyer.valid_to >= today,
                models.FlyerOffer.total_price.isnot(None))
        .all()
    )


@dataclass
class Price:
    """A price reduced to something comparable: per 100 g/ml when the size
    is known, else per item."""
    value: float
    per: str  # "100 g", "100 ml" or "stk"


def comparable(total_price, pack_size, unit, quantity) -> Price | None:
    if total_price is None:
        return None
    each = total_price / max(pack_size or 1, 1)
    if unit in ("g", "ml") and quantity:
        return Price(each / quantity * 100, f"100 {unit}")
    return Price(each, "stk")


def judge(offer: models.FlyerOffer, flyer: models.Flyer, receipts) -> dict:
    """Is this offer cheaper than usual? `receipts` are (ReceiptItem, Receipt)
    pairs for the same product. "Usual" is the chain's latest receipt price
    from before the offer started (a receipt from the offer week would just
    show the offer price), compared per 100 g/ml or per item."""
    price = comparable(offer.total_price, offer.pack_size, offer.unit, offer.quantity)
    usual, usual_date, elsewhere = None, None, {}
    for item, receipt in receipts:
        p = comparable(item.total_price, item.pack_size, item.unit, item.quantity)
        if not p or p.per != price.per:
            continue
        chain = chain_name(receipt.store_chain)
        when = receipt.purchase_date
        if chain == flyer.chain:
            if when and when < flyer.valid_from and (usual_date is None or when > usual_date):
                usual, usual_date = p.value, when
        elif chain:
            # The latest price at each other chain
            if chain not in elsewhere or (when and elsewhere[chain][1] and when > elsewhere[chain][1]):
                elsewhere[chain] = (p.value, when)

    verdict = {"kind": "unknown", "usual": round(usual, 2) if usual else None, "discount": None, "cheaper_chain": None, "cheaper_price": None, "per": price.per}
    cheaper = min(((v, c) for c, (v, _) in elsewhere.items()), default=None)
    if cheaper and cheaper[0] < price.value * 0.98:
        verdict.update(kind="cheaper_elsewhere", cheaper_chain=cheaper[1], cheaper_price=round(cheaper[0], 2))
    if usual:
        discount = 1 - price.value / usual
        verdict["discount"] = round(discount, 3)
        if verdict["kind"] != "cheaper_elsewhere":
            verdict["kind"] = "real" if discount >= REAL_DISCOUNT else "small" if discount >= SMALL_DISCOUNT else "not"
    elif verdict["kind"] == "unknown" and elsewhere:
        verdict["kind"] = "cheapest_known"
    elif verdict["kind"] == "unknown" and offer.pre_price and (offer.pack_size or 1) == 1 and offer.total_price < offer.pre_price:
        # Nothing to check it against: say what the flyer claims, as a claim
        verdict.update(kind="claimed", discount=round(1 - offer.total_price / offer.pre_price, 3), pre_price=offer.pre_price)
    return verdict


def offer_list(db: Session, today: date) -> list[dict]:
    """Every offer valid today, with its verdict, biggest real discount first."""
    offers = active_offers(db, today)
    names = {o.canonical_name for o, _ in offers}
    receipts: dict[str, list] = {}
    if names:
        rows = (
            db.query(models.ReceiptItem, models.Receipt)
            .join(models.Receipt, models.ReceiptItem.receipt_id == models.Receipt.id)
            .filter(models.ReceiptItem.canonical_name.in_(names), models.ReceiptItem.total_price.isnot(None))
            .all()
        )
        for item, receipt in rows:
            receipts.setdefault(item.canonical_name, []).append((item, receipt))

    result = []
    for offer, flyer in offers:
        price = comparable(offer.total_price, offer.pack_size, offer.unit, offer.quantity)
        result.append({
            "canonical_name": offer.canonical_name,
            "raw_name": offer.raw_name,
            "brand": offer.brand,
            "variant": offer.variant,
            "category": offer.category,
            "chain": flyer.chain,
            "quantity": offer.quantity,
            "unit": offer.unit,
            "pack_size": offer.pack_size,
            "total_price": offer.total_price,
            "each": round(offer.total_price / max(offer.pack_size or 1, 1), 2),
            "per_unit": round(price.value, 2),
            "is_member_price": bool(offer.is_member_price),
            "deal": offer.deal,
            "pre_price": offer.pre_price,
            "valid_from": str(flyer.valid_from),
            "valid_to": str(flyer.valid_to),
            "verdict": judge(offer, flyer, receipts.get(offer.canonical_name, [])),
        })
    order = {"real": 0, "small": 1, "cheapest_known": 2, "claimed": 3, "unknown": 4, "not": 5, "cheaper_elsewhere": 6}
    result.sort(key=lambda o: (order[o["verdict"]["kind"]], -(o["verdict"]["discount"] or 0), o["canonical_name"]))
    return result
