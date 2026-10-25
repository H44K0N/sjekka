"""Dinner suggestions: app/meals.py and GET /meals."""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app import basket, meals, models, recipes
from app.recipes import Recipe, ing

TODAY = date(2026, 10, 24)


def row(name, chain, price, *, unit="g", quantity=500, days=0):
    item = SimpleNamespace(canonical_name=name, total_price=price, pack_size=1, unit=unit, quantity=quantity,
                           price_per_unit=round(price / quantity * 100, 2), brand=None, raw_name=None)
    return item, SimpleNamespace(store_chain=chain, purchase_date=TODAY - timedelta(days=days))


def dinner(rid, name, kind, *ingredients, minutes=30):
    return Recipe(rid, name, "Smaker godt.", minutes, kind, tuple(ingredients), ("salt",), ("Lag maten.",))


# Pasta 500 g: Kiwi 20, Rema 30. Mince 400 g: Kiwi 60, Rema 50. Cream 2 dl: Kiwi 15, Rema 18.
ROWS = [row("Pasta", "Kiwi", 20), row("Pasta", "Rema 1000", 30),
        row("Kjøttdeig", "Kiwi", 60, quantity=400), row("Kjøttdeig", "Rema 1000", 50, quantity=400),
        row("Matfløte", "Kiwi", 15, unit="ml", quantity=200), row("Matfløte", "Rema 1000", 18, unit="ml", quantity=200)]
BOLOGNESE = dinner("bolo", "Bolognese", "kjøtt", ing("Pasta", "Pasta", share=0.8), ing("Kjøttdeig", "Kjøttdeig"))
CARBONARA = dinner("carb", "Pastaen", "svin", ing("Pasta", "Pasta", share=0.8), ing("Fløte", "Matfløte"))
KNOWN = {"Pasta": 2, "Kjøttdeig": 2, "Matfløte": 2}


@pytest.fixture
def two_dinners(monkeypatch):
    monkeypatch.setattr(meals, "RECIPES", (BOLOGNESE, CARBONARA))
    monkeypatch.setattr(meals, "BY_ID", {r.id: r for r in (BOLOGNESE, CARBONARA)})


def test_the_product_with_most_prices_behind_it_is_the_one_priced():
    assert meals.choose(("Spaghetti", "Pasta"), {"Spaghetti": 1, "Pasta": 4}) == "Pasta"
    assert meals.choose(("Spaghetti", "Pasta"), {"Spaghetti": 2, "Pasta": 2}) == "Spaghetti"  # the first listed
    assert meals.choose(("Ris",), {"Pasta": 3}) is None


def test_a_chain_priced_mostly_by_guesses_is_not_named_cheapest():
    # The guessed total is lowest, as guesses often are, but it's a guess
    assert meals.pick_chain({"Kiwi": 100.0, "Spar": 90.0}, {"Kiwi": 0.0, "Spar": 0.8}) == "Kiwi"
    assert meals.pick_chain({"Kiwi": 100.0, "Spar": 90.0}, {"Kiwi": 0.5, "Spar": 0.5}) == "Spar"
    # When every chain is a guess, the cheapest of them is the answer there is
    assert meals.pick_chain({"Kiwi": 100.0, "Spar": 90.0}, {"Kiwi": 0.9, "Spar": 0.8}) == "Spar"


def test_a_dinner_without_a_price_for_a_main_ingredient_is_left_out_but_a_minor_one_is_not():
    needs_rice = dinner("a", "A", "vegetar", ing("Pasta", "Pasta"), ing("Ris", "Ris"))
    assert meals.resolve(needs_rice, KNOWN) is None
    garnish = dinner("b", "B", "vegetar", ing("Pasta", "Pasta"), ing("Persille", "Persille", minor=True))
    lines = meals.resolve(garnish, KNOWN)
    assert [product for _, product in lines] == ["Pasta", None]
    # It's listed as having no price, and not counted
    offers = basket.latest_offers(ROWS)
    units, plain = meals.unit_prices(["Pasta"], offers, {})
    priced = meals.price_recipe(garnish, lines, 4, units, plain)
    assert priced["unpriced"] == ["Persille"] and [i["product"] for i in priced["ingredients"]] == ["Pasta"]


def test_a_dinner_is_priced_by_its_share_of_each_package_at_the_cheapest_chain(two_dinners):
    offers = basket.latest_offers(ROWS)
    result = meals.suggest(offers, {}, portions=4)
    bolognese = next(r for r in result["recipes"] if r["id"] == "bolo")
    # Kiwi: 0.8 × 20 + 60 = 76; Rema: 0.8 × 30 + 50 = 74
    assert (bolognese["chain"], bolognese["total"], bolognese["per_portion"]) == ("Rema 1000", 74.0, 18.5)
    assert [(i["label"], i["price"]) for i in bolognese["ingredients"]] == [("Pasta", 24.0), ("Kjøttdeig", 50.0)]
    # The packages are bought whole: a whole bag of pasta, not 0.8 of one
    assert bolognese["buy_total"] == 80.0
    assert bolognese["steps"] == ["Lag maten."] and bolognese["pantry"] == ["salt"]


def test_fewer_portions_cost_proportionally_less_and_the_cheapest_dinner_comes_first(two_dinners):
    offers = basket.latest_offers(ROWS)
    four = meals.suggest(offers, {}, portions=4)
    two = meals.suggest(offers, {}, portions=2)
    assert two["portions"] == 2
    assert two["recipes"][0]["total"] == pytest.approx(four["recipes"][0]["total"] / 2)
    assert [r["per_portion"] for r in four["recipes"]] == sorted(r["per_portion"] for r in four["recipes"])
    assert meals.suggest(offers, {}, portions=99)["portions"] == meals.PORTIONS_MAX
    assert meals.suggest(offers, {}, portions=0)["portions"] == meals.PORTIONS_MIN


def test_offers_lower_the_price_and_the_saving_is_what_they_took_off(two_dinners):
    offers = basket.latest_offers(ROWS)
    flyer = SimpleNamespace(chain="Rema 1000", valid_from=TODAY, valid_to=TODAY + timedelta(days=4))
    offer = SimpleNamespace(canonical_name="Kjøttdeig", total_price=30.0, pack_size=1, unit="g", quantity=400,
                            price_per_unit=7.5, brand="Gilde", deal=None, raw_name=None)
    deals = basket.flyer_deals([(offer, flyer)])
    bolognese = next(r for r in meals.suggest(offers, deals)["recipes"] if r["id"] == "bolo")
    assert (bolognese["chain"], bolognese["total"], bolognese["saving"], bolognese["offers"]) == ("Rema 1000", 54.0, 20.0, 1)
    assert [i["offer"] for i in bolognese["ingredients"]] == [False, True]


def test_a_product_only_the_flyers_know_has_a_price_but_no_saving(two_dinners):
    flyer = SimpleNamespace(chain="Kiwi", valid_from=TODAY, valid_to=TODAY + timedelta(days=4))
    only = SimpleNamespace(canonical_name="Pasta", total_price=15.0, pack_size=1, unit="g", quantity=500,
                           price_per_unit=3.0, brand=None, deal=None, raw_name=None)
    deals = basket.flyer_deals([(only, flyer)])
    result = meals.suggest({}, deals)
    carbonara = next((r for r in result["recipes"] if r["id"] == "carb"), None)
    assert carbonara is None  # no price for the cream anywhere
    solo = dinner("solo", "Bare pasta", "vegetar", ing("Pasta", "Pasta"))
    assert meals.resolve(solo, meals.known_products({}, deals))


def test_the_week_shares_packages_between_dinners(two_dinners):
    offers = basket.latest_offers(ROWS)
    plan = meals.suggest(offers, {}, portions=4)["plan"]
    assert sorted(d["id"] for d in plan["days"]) == ["bolo", "carb"]
    pasta = next(s for s in plan["shopping"] if s["product"] == "Pasta")
    # 0.8 + 0.8 of a bag is two bags, not the four a pair of separate trips would buy
    assert pasta["count"] == 2 and sorted(pasta["used_in"]) == ["Bolognese", "Pastaen"]
    # Kiwi: 2 bags of pasta (40) + mince (60) + cream (15). Bought apart it's 80 + 35: the same, since 0.8 + 0.8 needs two bags
    assert plan["chain"] == "Kiwi" and plan["total"] == 115.0
    assert plan["per_portion"] == pytest.approx(115 / 8, abs=0.01)
    assert plan["shared_saving"] == 0.0
    # The food uses its shares only: 0.8 + 0.8 of the pasta (32), the mince (60) and the cream (15)
    assert plan["used_total"] == 107.0 and plan["used_per_portion"] == pytest.approx(107 / 8, abs=0.01)


def test_a_week_buys_less_than_its_dinners_bought_one_by_one(monkeypatch):
    # Two dinners each using a quarter of a bag of rice: one bag serves both
    a = dinner("a", "Gryte", "kylling", ing("Ris", "Ris", share=0.25), ing("Kylling", "Kyllingfilet"))
    b = dinner("b", "Wok", "fisk", ing("Ris", "Ris", share=0.25), ing("Torsk", "Torskefilet"))
    rows = [row("Ris", "Kiwi", 40), row("Kyllingfilet", "Kiwi", 80), row("Torskefilet", "Kiwi", 90)]
    offers = basket.latest_offers(rows)
    known = meals.known_products(offers, {})
    resolved = {r.id: meals.resolve(r, known) for r in (a, b)}
    units, plain = meals.unit_prices(["Ris", "Kyllingfilet", "Torskefilet"], offers, {})
    priced = {r.id: meals.price_recipe(r, resolved[r.id], 4, units, plain) for r in (a, b)}
    monkeypatch.setattr(meals, "RECIPES", (a, b))
    monkeypatch.setattr(meals, "BY_ID", {"a": a, "b": b})
    plan = meals.plan_week(priced, resolved, units, plain, 4, chosen=["a", "b"])
    assert plan["total"] == 210.0 and plan["shared_saving"] == 40.0  # 80 + 90 + one bag of rice, not two


def test_a_plan_has_limited_dinners_of_one_kind_and_puts_friday_food_last(monkeypatch):
    many = tuple(dinner(f"v{i}", f"Veg {i}", "vegetar", ing("Pasta", "Pasta", share=0.1 * (i + 1))) for i in range(4))
    taco = dinner("taco", "Taco", "kjøtt", ing("Kjøttdeig", "Kjøttdeig"))
    fish = dinner("fish", "Fisk", "fisk", ing("Matfløte", "Matfløte"))
    everything = (*many, taco, fish)
    monkeypatch.setattr(meals, "RECIPES", everything)
    monkeypatch.setattr(meals, "BY_ID", {r.id: r for r in everything})
    plan = meals.suggest(basket.latest_offers(ROWS), {})["plan"]
    ids = [d["id"] for d in plan["days"]]
    assert sum(1 for i in ids if i.startswith("v")) == 1  # at most one vegetarian dinner
    assert ids[-1] == "taco" and len(ids) == 3
    assert [d["day"] for d in plan["days"]] == ["Mandag", "Tirsdag", "Onsdag"]
    assert set(plan["days"][0]["alternatives"]).isdisjoint(ids)


def test_a_plan_can_be_asked_for_with_chosen_dinners(two_dinners):
    offers = basket.latest_offers(ROWS)
    only = meals.suggest(offers, {}, chosen=["carb"])["plan"]
    assert [d["id"] for d in only["days"]] == ["carb"]
    assert meals.suggest(offers, {}, chosen=["finnes-ikke"])["plan"] is None


def test_there_are_no_dinners_without_prices():
    assert meals.suggest({}, {}) == {"portions": 4, "recipes": [], "plan": None}


def test_every_dinner_in_the_book_is_well_formed():
    assert len({r.id for r in recipes.RECIPES}) == len(recipes.RECIPES)
    for r in recipes.RECIPES:
        assert r.kind in meals.KIND_LIMITS, r.id
        assert r.steps and r.ingredients and 10 <= r.minutes <= 90, r.id
        assert any(not i.minor for i in r.ingredients), r.id
        for i in r.ingredients:
            assert i.options and 0 < i.share <= 3, (r.id, i.label)


# GET /meals

def test_the_meals_endpoint_prices_dinners_from_receipts_and_flyers(client, db, monkeypatch):
    monkeypatch.setattr(meals, "RECIPES", (BOLOGNESE,))
    monkeypatch.setattr(meals, "BY_ID", {BOLOGNESE.id: BOLOGNESE})
    monkeypatch.setattr(recipes, "BY_ID", {BOLOGNESE.id: BOLOGNESE})
    for store, pasta, mince in [("Kiwi", 20, 60), ("Rema 1000", 30, 50)]:
        receipt = models.Receipt(image_filename="r.jpg", store_chain=store, purchase_date=date.today())
        db.add(receipt)
        db.flush()
        for name, price, qty in [("Pasta", pasta, 500), ("Kjøttdeig", mince, 400)]:
            db.add(models.ReceiptItem(receipt_id=receipt.id, canonical_name=name, category="Annet", unit="g", quantity=qty,
                                      pack_size=1, total_price=price, price_per_unit=price / qty * 100))
    flyer = models.Flyer(chain="Kiwi", source="t", valid_from=date.today(), valid_to=date.today() + timedelta(days=5))
    flyer.offers.append(models.FlyerOffer(canonical_name="Kjøttdeig", unit="g", quantity=400, pack_size=1, total_price=35.0,
                                          price_per_unit=8.75))
    db.add(flyer)
    db.commit()

    result = client.get("/meals?portions=4").json()
    assert [r["id"] for r in result["recipes"]] == ["bolo"]
    bolo = result["recipes"][0]
    assert (bolo["chain"], bolo["total"], bolo["offers"]) == ("Kiwi", 51.0, 1)  # 0.8 × 20 + the 35 kr offer
    assert result["plan"]["days"][0]["id"] == "bolo"
    # Unknown ids are ignored, and the count is kept sensible
    assert client.get("/meals?ids=nope,bolo&portions=50").json()["portions"] == 8
    assert client.get("/meals?ids=nope").json()["plan"]["days"][0]["id"] == "bolo"


def test_the_meals_endpoint_is_empty_without_prices(client):
    assert client.get("/meals").json() == {"portions": 4, "recipes": [], "plan": None}
