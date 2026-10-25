"""Runs the real Sjekka app and worker for the video, with demo data and the
Gemini call replaced by a fixed answer for the demo receipt."""
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

DATA = sys.argv[1]
os.environ.update(DATA_DIR=DATA, GEMINI_API_KEY="demo", FLYER_REFRESH_HOURS="0")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # the app serves static/ relative to the repo root

import uvicorn  # noqa: E402
from app import models, worker  # noqa: E402
from app.basket import chain_name  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.schema import ExtractedItem, ExtractedReceipt  # noqa: E402

models.Base.metadata.create_all(engine)
today = date.today()

# name, brand, category, qty, unit, pack, price, store, days ago
SEED = [
    ("Lettmelk", "Tine", "Meieri", 1000, "ml", 1, 24.90, "Kiwi", 0),
    ("Lettmelk", "Q", "Meieri", 1000, "ml", 1, 26.50, "Meny", 3),
    ("Norvegia", "Tine", "Meieri", 500, "g", 1, 74.90, "Rema 1000", 2),
    ("Smør", "Tine", "Meieri", 250, "g", 1, 39.90, "Coop Extra", 4),
    ("Yoghurt naturell", "Q", "Meieri", 500, "g", 1, 21.90, "Kiwi", 1),
    ("Kjøttdeig", "Gilde", "Kjøtt", 400, "g", 1, 59.90, "Rema 1000", 1),
    ("Kyllingfilet", "Prior", "Kjøtt", 700, "g", 1, 119.00, "Meny", 6),
    ("Grillpølse", "Gilde", "Kjøtt", 600, "g", 1, 49.90, "Kiwi", 9),
    ("Laksefilet", "Lerøy", "Fisk", 400, "g", 1, 89.90, "Meny", 2),
    ("Fiskekaker", "Lofoten", "Fisk", 600, "g", 1, 64.90, "Coop Extra", 12),
    ("Bananer", None, "Frukt og grønt", 1000, "g", 1, 24.90, "Rema 1000", 0),
    ("Epler", None, "Frukt og grønt", 1000, "g", 1, 34.90, "Kiwi", 5),
    ("Agurk", None, "Frukt og grønt", None, "stk", 2, 30.00, "Coop Extra", 3),
    ("Grovbrød", "Bakers", "Bakeri", 750, "g", 1, 42.90, "Kiwi", 1),
    ("Rundstykker", "Bakers", "Bakeri", 6, "stk", 6, 29.90, "Meny", 20),
    ("Seigmenn", "Laban", "Godteri", 260, "g", 3, 89.70, "Rema 1000", 7),
    ("Kvikk Lunsj", "Freia", "Godteri", 47, "g", 1, 19.90, "Coop Extra", 2),
    ("Potetgull", "Kims", "Snacks", 175, "g", 1, 34.90, "Kiwi", 4),
    ("Appelsinjuice", "Sunniva", "Drikke", 1000, "ml", 1, 32.90, "Meny", 1),
    ("Farris", "Ringnes", "Drikke", 1500, "ml", 1, 24.90, "Rema 1000", 8),
    ("Pizza Grandiosa", "Stabburet", "Frys", 575, "g", 1, 59.90, "Coop Extra", 15),
    ("Tomatsuppe", "Toro", "Hermetikk", 1, "stk", 1, 18.90, "Kiwi", 10),
    ("Oppvaskmiddel", "Zalo", "Rengjøring", 500, "ml", 1, 34.90, "Meny", 30),
    # Some history, so product pages have prices to compare
    ("Lettmelk", "Q", "Meieri", 1000, "ml", 1, 22.90, "Rema 1000", 10),
    ("Lettmelk", "Tine", "Meieri", 1000, "ml", 1, 23.90, "Kiwi", 25),
    ("Lettmelk", "Q", "Meieri", 1000, "ml", 1, 21.90, "Rema 1000", 40),
    ("Kjøttdeig", "Gilde", "Kjøtt", 400, "g", 1, 62.90, "Kiwi", 5),
    ("Kjøttdeig", "Gilde", "Kjøtt", 400, "g", 1, 64.90, "Meny", 12),
    ("Kjøttdeig", "Gilde", "Kjøtt", 400, "g", 1, 57.90, "Rema 1000", 30),
    ("Kjøttdeig", "Gilde", "Kjøtt", 400, "g", 1, 59.90, "Extra", 18),
]


# This week's flyer offers: name, brand, qty, unit, pack, price, before-price, chain
OFFERS = [
    ("Kjøttdeig", "Gilde", 400, "g", 1, 44.90, 59.90, "Rema 1000"),
    ("Lettmelk", "Tine", 1000, "ml", 1, 23.90, None, "Meny"),
    ("Laksefilet", "Lerøy", 400, "g", 1, 69.90, 89.90, "Kiwi"),
    ("Grovbrød", "Bakers", 750, "g", 1, 29.90, 42.90, "Kiwi"),
    ("Farris", "Ringnes", 1500, "ml", 3, 50.00, None, "Coop Extra"),
    ("Taco Kit", "Old El Paso", 350, "g", 1, 39.00, 54.90, "Meny"),
    ("Pizza Grandiosa", "Stabburet", 575, "g", 2, 99.00, None, "Kiwi"),
]


# Price history for the films: a few months of receipts for the products
# the films show, in seven chains, so the ranking and timelines have
# something to show. Seeded, so every recording sees the same prices.
HISTORY = {
    # name: (brand, category, qty, unit, price at the dearest chain)
    "Kjøttdeig": ("Gilde", "Kjøtt", 400, "g", 64.90),
    "Lettmelk": ("Tine", "Meieri", 1000, "ml", 26.50),
    "Laksefilet": ("Lerøy", "Fisk", 400, "g", 94.90),
    "Grovbrød": ("Bakers", "Bakeri", 750, "g", 44.90),
    "Bananer": (None, "Frukt og grønt", 1000, "g", 29.90),
    "Norvegia": ("Tine", "Meieri", 500, "g", 79.90),
}
# How each chain prices against the dearest, roughly
CHAIN_LEVEL = {"Rema 1000": 0.93, "Kiwi": 0.86, "Coop Extra": 0.91, "Spar": 0.97, "Coop Prix": 0.95, "Joker": 1.02, "Meny": 1.0}


def history_rows():
    rng = random.Random(7)
    rows = []
    for name, (brand, cat, qty, unit, top) in HISTORY.items():
        for chain, level in CHAIN_LEVEL.items():
            for days in sorted(rng.sample(range(14, 150), 5)):
                if rng.random() < 0.15:
                    continue
                # Prices creep up over the months, as they do
                drift = 1 - 0.0006 * days
                price = top * level * drift * rng.uniform(0.97, 1.03)
                rows.append((name, brand, cat, qty, unit, 1, round(price, 1) - 0.1 + 0.0, chain, days))
    return rows


def seed_offers(db):
    # Started yesterday, so receipts from before it count as the usual price
    by_chain = {}
    for name, brand, qty, unit, pack, price, before, chain in OFFERS:
        flyer = by_chain.get(chain) or models.Flyer(
            chain=chain_name(chain), valid_from=today - timedelta(days=1), valid_to=today + timedelta(days=5), source="demo")
        by_chain[chain] = flyer
        each = price / pack
        flyer.offers.append(models.FlyerOffer(
            raw_name=f"{name.upper()} {qty:g} {unit}, {brand}", canonical_name=name, brand=brand,
            quantity=qty, unit=unit, pack_size=pack, total_price=price, pre_price=before,
            price_per_unit=round(each / qty * 100, 2), deal=f"{pack} for {price:g}" if pack > 1 else None,
        ))
    db.add_all(by_chain.values())


def seed():
    db = SessionLocal()
    if db.query(models.Receipt).count():
        return
    seed_offers(db)
    for name, brand, cat, qty, unit, pack, price, store, days in SEED + history_rows():
        receipt = models.Receipt(image_filename="demo.jpg", store_chain=store,
                                 purchase_date=today - timedelta(days=days))
        db.add(receipt)
        db.flush()
        item = ExtractedItem(raw_name=name.upper(), canonical_name=name, brand=brand, category=cat,
                             quantity=qty, unit=unit, pack_size=pack, total_price=price)
        db.add(models.ReceiptItem(
            receipt_id=receipt.id, canonical_name=name, brand=brand, category=cat, quantity=qty,
            unit=unit, pack_size=pack, total_price=price, price_per_unit=worker.unit_price(item),
        ))
    db.commit()
    db.close()


def fake_gemini(path, known=()):
    """What Gemini returns for the demo receipt in the video."""
    return ExtractedReceipt(
        store_chain="Rema 1000", store_location="Grünerløkka", region="Oslo",
        purchase_date=today.isoformat(), total=178.60,
        items=[
            ExtractedItem(raw_name="TACOKRYDDER 28G", canonical_name="Tacokrydder", brand="Old El Paso",
                          category="Annet", quantity=28, unit="g", total_price=14.90),
            ExtractedItem(raw_name="TORTILLA ORIGINAL 8STK", canonical_name="Tortilla", brand="Santa Maria",
                          category="Bakeri", quantity=320, unit="g", total_price=32.90),
            ExtractedItem(raw_name="RØMME LETT 300G", canonical_name="Rømme", brand="Tine", variant="lett",
                          category="Meieri", quantity=300, unit="g", total_price=26.90),
            ExtractedItem(raw_name="KJØTTDEIG STORFE 400G", canonical_name="kjøttdeig", brand="Gilde",
                          category="Kjøtt", quantity=400, unit="g", total_price=54.90),
            ExtractedItem(raw_name="AVOKADO 2STK", canonical_name="Avokado", category="Frukt og grønt",
                          unit="stk", pack_size=2, total_price=49.00),
        ],
    )


seed()
worker.extract_receipt = lambda path, known=(): fake_gemini(path).model_dump_json()
worker.POLL_INTERVAL = 0.5
uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
