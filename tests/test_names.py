"""One name per product and one spelling per brand (app/names.py)."""
import asyncio
from datetime import date

import pytest

from app import models, names, tjek, worker
from app.schema import ExtractedItem, ExtractedReceipt


def receipt_with(db, *lines, store="Kiwi"):
    """A saved receipt with lines of (name, brand, variant, price)."""
    receipt = models.Receipt(image_filename="r.jpg", store_chain=store, purchase_date=date(2026, 10, 20))
    db.add(receipt)
    db.flush()
    for name, brand, variant, price in lines:
        db.add(models.ReceiptItem(receipt_id=receipt.id, canonical_name=name, brand=brand, variant=variant,
                                  category="Annet", unit="stk", pack_size=1, total_price=price, price_per_unit=price))
    db.commit()


@pytest.mark.parametrize("given, expected", [
    # The brand as the name: the product is the generic thing
    (("Grandiosa", None, "Original"), ("Pizza", "Grandiosa", "Original")),
    (("PIZZA GRANDIOSA", "Stabb", None), ("Pizza", "Grandiosa", None)),
    (("Coca-cola", None, "Zero"), ("Brus", "Coca-Cola", "Zero")),
    # A kind of the product becomes its variant
    (("Frokostegg", "Prior", "12 stk"), ("Egg", "Prior", "Frokostegg")),
    # Another spelling or the plural
    (("Spiss paprika", None, None), ("Spisspaprika", None, None)),
    (("Søtpoteter", None, None), ("Søtpotet", None, None)),
    # Brands get one spelling
    (("Lettmelk", "Q-Meieriene", None), ("Lettmelk", "Q", None)),
    (("Lettmelk", "TINE", None), ("Lettmelk", "Tine", None)),
    (("Taco saus", "St.Maria", None), ("Taco saus", "Santa Maria", None)),
    (("Iskrem", "Ben & Jerry\n\t", None), ("Iskrem", "Ben & Jerry's", None)),
    # What's fine is left alone, but tidied of stray spaces and case
    ((" kjøttdeig ", " Gilde", None), ("Kjøttdeig", "Gilde", None)),
    (("Agurk", "", None), ("Agurk", None, None)),
])
def test_names_are_tidied(client, db, given, expected):
    assert names.Names(db).tidy(*given) == expected


def test_a_spelling_in_use_wins(client, db):
    receipt_with(db, ("Serranoskinke", "Taga", None, 39.9), ("Serranoskinke", "Taga", None, 41.9),
                 ("Serrano skinke", "TAGA", None, 40.9))
    tidy = names.Names(db).tidy
    assert tidy("Serrano skinke", "TAGA") == ("Serranoskinke", "Taga", None)
    assert tidy("Serrano-skinke") == ("Serranoskinke", None, None)
    assert tidy("Ukjent ny vare", "Ukjent merke") == ("Ukjent ny vare", "Ukjent merke", None)


def test_a_receipt_is_saved_under_the_names_in_use(client, db, monkeypatch):
    data = ExtractedReceipt(store_chain="Kiwi", purchase_date="2026-10-20", total=89.8, items=[
        ExtractedItem(raw_name="GRANDIOSA ORIGINAL", canonical_name="Grandiosa", brand="Grandiosa",
                      variant="Original", category="Frys", total_price=59.9),
        ExtractedItem(raw_name="Q LETTMELK", canonical_name="lettmelk", brand="Q-Meieriene",
                      category="Meieri", total_price=29.9),
    ])
    job = models.Job(filename="a.jpg")
    db.add(job)
    db.commit()
    monkeypatch.setattr(worker, "extract_receipt", lambda path, known=(): data.model_dump_json())
    asyncio.run(worker.process_job(job, db))
    saved = {i.raw_name: (i.canonical_name, i.brand, i.variant) for i in db.query(models.ReceiptItem)}
    assert saved == {"GRANDIOSA ORIGINAL": ("Pizza", "Grandiosa", "Original"), "Q LETTMELK": ("Lettmelk", "Q", None)}


def test_a_flyer_offer_is_saved_under_the_names_in_use(client, db):
    catalog = {"id": "c1", "branding": {"name": "Kiwi"},
               "run_from": "2026-10-18T22:00:00+0000", "run_till": "2026-10-25T21:59:59+0000"}
    offer = {"heading": "Pepsi Max", "pricing": {"price": 39.9}, "quantity": {"unit": {"symbol": "l"},
             "size": {"from": 1.5, "to": 1.5}, "pieces": {"from": 1, "to": 1}}}
    namer = lambda offers: ({0: tjek.OfferName(index=0, canonical_name="Pepsi max", brand="Pepsi",  # noqa: E731
                                                category="Drikke")}, "raw")
    saved, = tjek.import_catalog(db, catalog, [offer], namer).offers
    assert (saved.canonical_name, saved.brand) == ("Brus", "Pepsi Max")


def test_lines_saved_before_are_renamed_once_and_none_lost(client, db):
    receipt_with(db, ("Grandiosa", "Grandiosa", None, 59.9), ("Pizza grandiosa", "Stabb", None, 62.9),
                 ("Pizza", "Big One", None, 49.9), ("Lettmelk", "TINE", None, 24.9), ("Agurk", None, None, 15.0))
    flyer = models.Flyer(chain="Meny", source="t", valid_from=date(2026, 10, 20), valid_to=date(2026, 10, 26))
    flyer.offers.append(models.FlyerOffer(canonical_name="Frokostegg", brand="Prior", total_price=39.9))
    db.add(flyer)
    db.commit()

    assert names.repair_product_names(db) == 4
    assert names.repair_product_names(db) == 0
    lines = sorted((i.canonical_name, i.brand, i.total_price) for i in db.query(models.ReceiptItem))
    assert lines == [("Agurk", None, 15.0), ("Lettmelk", "Tine", 24.9), ("Pizza", "Big One", 49.9),
                     ("Pizza", "Grandiosa", 59.9), ("Pizza", "Grandiosa", 62.9)]
    offer = db.query(models.FlyerOffer).one()
    assert (offer.canonical_name, offer.brand, offer.variant) == ("Egg", "Prior", "Frokostegg")


def test_an_old_name_still_finds_the_product(client, db):
    receipt_with(db, ("Pizza", "Grandiosa", None, 59.9), ("Pizza", "Big One", None, 49.9))
    receipt_with(db, ("Pizza", "Grandiosa", None, 64.9), store="Meny")

    page = client.get("/products/Grandiosa").json()
    assert page["canonical_name"] == "Pizza" and len(page["prices"]) == 3
    assert client.get("/products/Finnes ikke").status_code == 404

    # A saved list asks for the old name: it's priced as the product, and told the new name
    res = client.post("/basket", json={"items": [{"name": "Grandiosa", "count": 1}, {"name": "Pizza", "count": 1}]}).json()
    assert res["renamed"] == {"Grandiosa": "Pizza"}
    assert [(i["name"], i["count"]) for i in res["items"]] == [("Pizza", 2)]
    # And the product page's offers
    today = date.today()
    flyer = models.Flyer(chain="Rema 1000", source="t", valid_from=today, valid_to=today)
    flyer.offers.append(models.FlyerOffer(canonical_name="Pizza", brand="Grandiosa", unit="stk", pack_size=1,
                                          total_price=45.0, price_per_unit=45.0))
    db.add(flyer)
    db.commit()
    assert [o["canonical_name"] for o in client.get("/offers?navn=Grandiosa").json()] == ["Pizza"]


def test_a_list_name_only_differing_in_case_is_not_renamed(client, db):
    receipt_with(db, ("Lettmelk", "Tine", None, 24.9))
    res = client.post("/basket", json={"items": [{"name": "lettmelk", "count": 1}]}).json()
    assert res["renamed"] == {}
