"""Pricing a shopping list across chains: app/basket.py and POST /basket."""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app import basket, models

TODAY = date(2026, 10, 24)


def row(name, chain, price, *, unit=None, quantity=None, pack=1, days=0, brand=None):
    """A (ReceiptItem, Receipt) pair as the query returns them."""
    each = price / pack if price is not None else None
    per_unit = round(each / quantity * 100, 2) if unit in ("g", "ml") and quantity and each else None
    item = SimpleNamespace(canonical_name=name, total_price=price, pack_size=pack, unit=unit,
                           quantity=quantity, price_per_unit=per_unit, brand=brand)
    receipt = SimpleNamespace(store_chain=chain, purchase_date=TODAY - timedelta(days=days))
    return item, receipt


def priced(wanted, rows):
    return basket.price_list(wanted, basket.latest_offers(rows))


@pytest.mark.parametrize("raw, expected", [
    ("REMA 1000", "Rema 1000"), ("Rema", "Rema 1000"), ("KIWI Majorstuen", "Kiwi"),
    ("Coop Extra Storo", "Extra"), ("Coop Mega", "Coop Mega"), ("Coop Prix", "Coop Prix"),
    ("Coop Obs Hypermarked", "Obs"), ("EUROSPAR", "Spar"), ("Meny Ullevål", "Meny"),
    ("  bunnpris  ", "Bunnpris"), ("ukjent  butikk", "Ukjent Butikk"), ("", None), (None, None),
    ("COOP MEGA STORO", "Coop Mega"), ("Megaflis", "Megaflis"),
])
def test_chain_names_are_grouped(raw, expected):
    assert basket.chain_name(raw) == expected


def test_latest_price_per_chain_wins():
    offers = basket.latest_offers([
        row("Lettmelk", "Kiwi", 20, days=1),
        row("Lettmelk", "KIWI", 30, days=10),   # older, same chain spelled differently
        row("Lettmelk", "Kiwi", None, days=0),  # no price: ignored
    ])
    assert offers[("Lettmelk", "Kiwi")].each == 20


def test_cheapest_store_for_the_whole_list():
    rows = [
        row("Melk", "Kiwi", 20), row("Melk", "Rema", 22),
        row("Brød", "Kiwi", 40), row("Brød", "Rema", 35),
        row("Ost", "Kiwi", 100), row("Ost", "Rema", 90),
    ]
    result = priced([("Melk", 2), ("Brød", 1), ("Ost", 1)], rows)
    kiwi, rema = (next(s for s in result["stores"] if s["chain"] == c) for c in ("Kiwi", "Rema 1000"))
    assert kiwi["total"] == 2 * 20 + 40 + 100
    assert rema["total"] == 2 * 22 + 35 + 90
    assert result["stores"][0]["chain"] == "Rema 1000"
    assert kiwi["known"] == 3 and kiwi["estimated"] == 0
    milk = next(i for i in result["items"] if i["name"] == "Melk")
    assert milk["cheapest"] == "Kiwi" and milk["prices"]["Kiwi"]["price"] == 40


def test_missing_prices_are_estimated_from_how_expensive_the_chain_is():
    # Meny is 20% dearer than Kiwi on everything both sell; Meny has no egg price
    rows = [row(p, "Kiwi", price) for p, price in [("A", 10), ("B", 20), ("C", 30), ("Egg", 40)]]
    rows += [row(p, "Meny", price * 1.2) for p, price in [("A", 10), ("B", 20), ("C", 30)]]
    result = priced([("A", 1), ("Egg", 1)], rows)
    egg = result["items"][1]["prices"]
    assert egg["Kiwi"] == {"price": 40, "estimated": False, "date": str(TODAY), "brand": None, "offer_until": None, "deal": None}
    assert egg["Meny"]["estimated"] is True
    # Dearer than the known price, but pulled towards average by the prior
    assert 40 < egg["Meny"]["price"] < 40 * 1.2
    meny = next(s for s in result["stores"] if s["chain"] == "Meny")
    assert meny["known"] == 1 and meny["estimated"] == 1


def test_only_chains_with_a_known_price_on_the_list_are_ranked():
    # All-estimate totals would be guesswork
    rows = [row("A", "Kiwi", 10), row("A", "Meny", 12), row("Egg", "Kiwi", 40)]
    assert [s["chain"] for s in priced([("Egg", 1)], rows)["stores"]] == ["Kiwi"]


def test_chain_index_needs_shared_products():
    index = basket.chain_index({"A": {"Kiwi": 10}, "B": {"Kiwi": 10, "Meny": 10}})
    assert index == {"Kiwi": 1.0, "Meny": 1.0}
    assert basket.chain_index({"A": {"Kiwi": 10}}) == {}


def test_sizes_are_compared_per_unit():
    # Half a litre at Kiwi is not cheaper than a litre at Rema
    rows = [row("Juice", "Kiwi", 20, unit="ml", quantity=500),
            row("Juice", "Rema", 30, unit="ml", quantity=1000),
            row("Juice", "Meny", 35, unit="ml", quantity=1000)]
    item = priced([("Juice", 1)], rows)["items"][0]
    assert item["size"] == "1000 ml"
    assert item["prices"]["Kiwi"]["price"] == 40
    assert item["cheapest"] == "Rema 1000"


def test_mixed_units_fall_back_to_price_per_item():
    rows = [row("Agurk", "Kiwi", 15, unit="stk"), row("Agurk", "Rema", 300, unit="g", quantity=400)]
    item = priced([("Agurk", 1)], rows)["items"][0]
    assert item["size"] is None
    assert item["prices"]["Kiwi"]["price"] == 15


def test_pack_price_is_per_item():
    item = priced([("Yoghurt", 1)], [row("Yoghurt", "Kiwi", 60, pack=4)])["items"][0]
    assert item["prices"]["Kiwi"]["price"] == 15


def test_unknown_products_are_listed_separately():
    result = priced([("Melk", 1), ("Safran", 1)], [row("Melk", "Kiwi", 20)])
    assert result["unknown"] == ["Safran"]
    assert [i["name"] for i in result["items"]] == ["Melk"]


def test_empty_list():
    assert priced([], [row("Melk", "Kiwi", 20)]) == {"items": [], "unknown": [], "stores": [], "split": None}


def test_split_suggested_when_it_saves_enough():
    rows = [row("Kjøtt", "Kiwi", 100), row("Kjøtt", "Rema", 160),
            row("Kaffe", "Kiwi", 120), row("Kaffe", "Rema", 60)]
    split = priced([("Kjøtt", 1), ("Kaffe", 1)], rows)["split"]
    assert split["chains"] == ["Kiwi", "Rema 1000"]
    assert split["plan"] == {"Kjøtt": "Kiwi", "Kaffe": "Rema 1000"}
    assert split["total"] == 160
    assert split["saving"] == 60


def test_no_split_for_small_savings():
    rows = [row("Kjøtt", "Kiwi", 100), row("Kjøtt", "Rema", 110),
            row("Kaffe", "Kiwi", 110), row("Kaffe", "Rema", 100)]
    assert priced([("Kjøtt", 1), ("Kaffe", 1)], rows)["split"] is None


def test_no_split_with_one_store():
    assert priced([("Melk", 1)], [row("Melk", "Kiwi", 20)])["split"] is None


def test_no_split_when_one_store_wins_everything():
    rows = [row("A", "Kiwi", 10), row("A", "Rema", 100), row("B", "Kiwi", 10), row("B", "Rema", 100)]
    assert priced([("A", 1), ("B", 1)], rows)["split"] is None


# The endpoint, against the database

def add_price(db, name, chain, price, days=0, unit=None, quantity=None, per_unit=None):
    receipt = models.Receipt(image_filename="r.jpg", store_chain=chain, purchase_date=date.today() - timedelta(days=days))
    db.add(receipt)
    db.flush()
    db.add(models.ReceiptItem(receipt_id=receipt.id, canonical_name=name, total_price=price,
                              pack_size=1, unit=unit, quantity=quantity, price_per_unit=per_unit))
    db.commit()


def test_basket_endpoint(client, db):
    add_price(db, "Lettmelk", "KIWI", 24.9)
    add_price(db, "Lettmelk", "REMA 1000", 22.9)
    add_price(db, "Smør", "Kiwi", 39.9)
    add_price(db, "Uten pris", "Kiwi", None)
    res = client.post("/basket", json={"items": [
        {"name": "Lettmelk", "count": 2}, {"name": "Smør"}, {"name": "Lettmelk"}, {"name": "Uten pris"},
    ]})
    assert res.status_code == 200
    data = res.json()
    milk = next(i for i in data["items"] if i["name"] == "Lettmelk")
    assert milk["count"] == 3  # duplicates are added up
    assert data["unknown"] == ["Uten pris"]
    assert {s["chain"] for s in data["stores"]} == {"Kiwi", "Rema 1000"}
    kiwi = next(s for s in data["stores"] if s["chain"] == "Kiwi")
    assert kiwi["total"] == pytest.approx(3 * 24.9 + 39.9)


@pytest.mark.parametrize("body", [
    {"items": [{"name": ""}]},
    {"items": [{"name": "Melk", "count": 0}]},
    {"items": [{"name": "Melk", "count": 100}]},
    {"items": [{"name": "x"}] * 101},
    {},
])
def test_basket_rejects_bad_lists(client, body):
    assert client.post("/basket", json=body).status_code == 422


def test_a_store_that_is_not_a_grocery_chain_is_not_compared():
    # Trumf also lists florists that take the card
    offers = basket.latest_offers([row("Tulipaner", "Mester Grønn Sandvika", 200), row("Melk", "Kiwi", 20)])
    assert set(offers) == {("Melk", "Kiwi")}


def test_mostly_weighed_products_are_compared_by_weight():
    # The real case: one weighed apple (196 g, 5,47 kr) was "cheapest" against
    # 29,90 kr a kilo, and a 6-pack priced per pack muddled it all
    rows = [row("Epler", "Joker", 5.47, unit="g", quantity=196),
            row("Epler", "Kiwi", 29.9, unit="g", quantity=1000),
            row("Epler", "Extra", 32.9, unit="g", quantity=1000),
            row("Epler", "Spar", 30, unit="stk")]
    item = priced([("Epler", 1)], rows)["items"][0]
    assert item["size"] == "1000 g"
    assert item["prices"]["Joker"]["price"] == pytest.approx(27.91, abs=0.01)
    assert item["cheapest"] == "Joker"
    # The 6-pack can't be compared by weight, so Spar has no price for it
    assert "Spar" not in item["prices"]


def test_a_chain_whose_only_price_cant_be_compared_is_not_ranked():
    rows = [row("Epler", "Kiwi", 29.9, unit="g", quantity=1000),
            row("Epler", "Extra", 32.9, unit="g", quantity=1000),
            row("Epler", "Spar", 30, unit="stk")]
    assert {s["chain"] for s in priced([("Epler", 1)], rows)["stores"]} == {"Kiwi", "Extra"}


@pytest.mark.parametrize("quantities, size", [
    ([500, 1000, 1000], 1000),   # the most common size
    ([125, 600, 500, 150], 310),  # all different: the median (325), to two figures
    ([575], 575),
])
def test_the_typical_size_is_one_people_buy(quantities, size):
    assert basket.typical_size(quantities) == pytest.approx(size, rel=0.05)


def test_the_typical_size_is_judged_from_every_purchase_not_the_latest():
    # The real case: two chains' latest pizza was a 1 kg one, so every pizza
    # was priced "for 1 kg", twice what anyone pays for theirs
    rows = [row("Pizza", chain, price, unit="g", quantity=570, days=10)
            for chain, price in [("Kiwi", 59.9), ("Kiwi", 62.9), ("Meny", 64.9), ("Meny", 65.9), ("Rema 1000", 57.9)]]
    rows += [row("Pizza", "Kiwi", 149, unit="g", quantity=1000), row("Pizza", "Meny", 159, unit="g", quantity=1000)]
    item = priced([("Pizza", 1)], rows)["items"][0]
    assert item["size"] == "570 g"
    assert item["prices"]["Rema 1000"]["price"] == 57.9


def test_a_big_pack_is_not_scaled_down_to_a_small_one():
    # Kiwi's latest Brus was a 9-litre multipack: 4,94 kr "for 500 ml".
    # Kiwi is priced from its last bottle instead; Extra, with only a
    # 24-pack, gets no price of its own
    rows = [row("Brus", "Kiwi", 24.9, unit="ml", quantity=500, days=30),
            row("Brus", "Kiwi", 89, unit="ml", quantity=9000),
            row("Brus", "Meny", 29.9, unit="ml", quantity=500),
            row("Brus", "Rema 1000", 27.9, unit="ml", quantity=500),
            row("Brus", "Extra", 66.7, unit="ml", quantity=12000)]
    result = priced([("Brus", 1)], rows)
    item = result["items"][0]
    assert item["size"] == "500 ml"
    assert item["prices"]["Kiwi"]["price"] == 24.9 and not item["prices"]["Kiwi"]["estimated"]
    assert "Extra" not in item["prices"]
    assert item["cheapest"] == "Kiwi"


def test_a_chain_is_priced_by_the_cheapest_brand_it_has_now():
    # "Pizza" is any pizza: Kiwi's latest was a Peppes, but it sells
    # Grandiosa cheaper. Each brand counts at its latest price, and only
    # if seen in the last half year
    rows = [row("Pizza", "Kiwi", 89.9, unit="g", quantity=570, brand="Peppes"),
            row("Pizza", "Kiwi", 59.9, unit="g", quantity=570, brand="Grandiosa", days=30),
            row("Pizza", "Kiwi", 49.9, unit="g", quantity=570, brand="Grandiosa", days=60),  # since gone up
            row("Pizza", "Kiwi", 39.9, unit="g", quantity=570, brand="First Price", days=400),  # too long ago
            row("Pizza", "Meny", 64.9, unit="g", quantity=570, brand="Grandiosa")]
    kiwi = priced([("Pizza", 1)], rows)["items"][0]["prices"]["Kiwi"]
    assert (kiwi["price"], kiwi["brand"], kiwi["date"]) == (59.9, "Grandiosa", str(TODAY - timedelta(days=30)))


def test_a_chain_without_sizes_is_priced_by_its_cheapest_brand_too():
    rows = [row("Egg", "Kiwi", 34.9, brand="Prior"), row("Egg", "Kiwi", 29.9, brand="First Price", days=10)]
    assert priced([("Egg", 1)], rows)["items"][0]["prices"]["Kiwi"]["price"] == 29.9


def test_a_split_never_sends_you_to_a_store_on_a_guess():
    # Rema has no coffee price; its estimate is cheap because Rema is cheap
    # on meat. The split buys coffee where its price is known.
    rows = [row("Kjøtt", "Kiwi", 160), row("Kjøtt", "Rema", 80),
            row("Kaffe", "Kiwi", 100), row("Ost", "Kiwi", 100), row("Ost", "Rema", 60)]
    result = priced([("Kjøtt", 1), ("Kaffe", 1), ("Ost", 1)], rows)
    plan = result["split"]["plan"] if result["split"] else {}
    assert plan.get("Kaffe", "Kiwi") == "Kiwi"
    assert basket.pick({"A": {"x": (10, True)}, "B": {"x": (20, False)}}, "x", "A", "B") == "B"
    assert basket.pick({"A": {"x": (10, False)}, "B": {"x": (20, False)}}, "x", "A", "B") == "A"
