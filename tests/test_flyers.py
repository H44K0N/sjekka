"""Flyer offers (kundeavis): reading, importing, judging, and using them in
the shopping list. Gemini is always faked."""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app import basket, extractor, flyers, models
from app.schema import ExtractedFlyer
from google.genai import errors
from helpers import api_error
from util import import_flyer as cli

TODAY = date.today()
WEEK = (TODAY - timedelta(days=2), TODAY + timedelta(days=4))


def answer(offers, chain="Kiwi", valid_from=None, valid_to=None):
    return json.dumps({"store_chain": chain, "valid_from": valid_from, "valid_to": valid_to, "offers": offers})


def offer(name, price, **extra):
    return {"raw_name": name.upper(), "canonical_name": name, "category": "Annet", "total_price": price, **extra}


def flyer_file(tmp_path, name="kiwi.pdf"):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4")
    return path


def add_receipt_price(db, name, chain, price, days_ago, unit=None, quantity=None, pack=1):
    receipt = models.Receipt(image_filename="r.jpg", store_chain=chain, purchase_date=TODAY - timedelta(days=days_ago))
    db.add(receipt)
    db.flush()
    db.add(models.ReceiptItem(receipt_id=receipt.id, canonical_name=name, total_price=price, pack_size=pack,
                              unit=unit, quantity=quantity))
    db.commit()


def add_flyer(db, chain, offers, valid=WEEK):
    flyer = models.Flyer(chain=chain, valid_from=valid[0], valid_to=valid[1], source="test.pdf")
    for o in offers:
        flyer.offers.append(models.FlyerOffer(**{"pack_size": 1, **o}))
    db.add(flyer)
    db.commit()
    return flyer


@pytest.fixture
def clean(client, db):
    """The client fixture empties the receipt tables; empty the flyers too."""
    db.query(models.FlyerOffer).delete()
    db.query(models.Flyer).delete()
    db.commit()
    return client


# Reading a flyer

def test_extract_flyer_sends_the_file_with_the_flyer_schema(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(extractor.client.models, "generate_content",
                        lambda **kw: calls.append(kw) or SimpleNamespace(text=answer([offer("Agurk", 10)])))
    raw = extractor.extract_flyer(flyer_file(tmp_path, "uke43.PDF"))
    assert ExtractedFlyer.model_validate_json(raw).offers[0].canonical_name == "Agurk"
    assert calls[0]["config"].response_schema is ExtractedFlyer
    assert calls[0]["contents"][0].inline_data.mime_type == "application/pdf"
    assert "kundeavis" in calls[0]["contents"][1]


def test_flyer_prompt_names_products_like_receipts():
    # The same definitions, so offers and receipt lines get the same names
    assert extractor.DEFINITIONS in extractor.PROMPT
    assert extractor.DEFINITIONS in extractor.FLYER_PROMPT
    assert "4x150g\" → quantity: 600" in extractor.DEFINITIONS


def test_extract_flyer_rejects_other_files(tmp_path):
    with pytest.raises(ValueError, match="Unsupported"):
        extractor.extract_flyer(flyer_file(tmp_path, "kiwi.docx"))


def test_read_flyer_retries_when_gemini_is_busy(monkeypatch, tmp_path):
    answers = [api_error(503), api_error(429), answer([])]

    def fake(path, known=()):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(flyers, "extract_flyer", fake)
    waits = []
    assert flyers.read_flyer(flyer_file(tmp_path), sleep=waits.append) == answer([])
    assert waits == [flyers.RETRY_SECONDS, 2 * flyers.RETRY_SECONDS]


def test_read_flyer_gives_up(monkeypatch, tmp_path):
    def busy(path, known=()):
        raise api_error(503)
    monkeypatch.setattr(flyers, "extract_flyer", busy)
    with pytest.raises(errors.APIError):
        flyers.read_flyer(flyer_file(tmp_path), sleep=lambda s: None)

    def bad_request(path, known=()):
        raise api_error(400)
    monkeypatch.setattr(flyers, "extract_flyer", bad_request)
    waits = []
    with pytest.raises(errors.APIError):
        flyers.read_flyer(flyer_file(tmp_path), sleep=waits.append)
    assert waits == []


# Importing

def test_import_stores_offers_with_dates_and_raw_answer(clean, db, tmp_path):
    raw = answer([
        offer("KJØTTDEIG ", 39.9, brand=" Gilde ", quantity=400, unit="g", category="Kjøtt"),
        offer("Brus", 50, pack_size=2, deal="2 for 50", quantity=1500, unit="ml"),
        offer("Klementiner", None, deal="Kjøp 3 betal for 2"),
    ], chain="KIWI", valid_from="2026-10-20", valid_to="2026-10-26")
    flyer = flyers.import_flyer(db, flyer_file(tmp_path), read=lambda p: raw)

    assert (flyer.chain, flyer.valid_from, flyer.valid_to) == ("Kiwi", date(2026, 10, 20), date(2026, 10, 26))
    assert flyer.raw_response == raw and flyer.model == extractor.GEMINI_MODEL and flyer.source == "kiwi.pdf"
    mince, soda, oranges = flyer.offers
    assert (mince.canonical_name, mince.brand, mince.price_per_unit) == ("Kjøttdeig", "Gilde", 9.97)
    assert (soda.pack_size, soda.deal, soda.price_per_unit) == (2, "2 for 50", 1.67)
    assert oranges.total_price is None


def test_import_prices_a_multipack_for_the_whole_pack(clean, db, tmp_path):
    raw = answer([offer("Yoghurt 4x150g", 30, quantity=150, unit="g")], valid_from="2026-10-20", valid_to="2026-10-26")
    yoghurt, = flyers.import_flyer(db, flyer_file(tmp_path), read=lambda p: raw).offers
    assert (yoghurt.quantity, yoghurt.price_per_unit) == (600, 5.0)


def test_import_arguments_win_over_what_gemini_read(clean, db, tmp_path):
    raw = answer([offer("Agurk", 10)], chain="Meny", valid_from="2026-01-01", valid_to="2026-01-07")
    flyer = flyers.import_flyer(db, flyer_file(tmp_path), "Rema", date(2026, 2, 1), date(2026, 2, 7), read=lambda p: raw)
    assert (flyer.chain, flyer.valid_from, flyer.valid_to) == ("Rema 1000", date(2026, 2, 1), date(2026, 2, 7))


def test_importing_again_replaces_the_offers(clean, db, tmp_path):
    path = flyer_file(tmp_path)
    args = ("Kiwi", *WEEK)
    flyers.import_flyer(db, path, *args, read=lambda p: answer([offer("Agurk", 10), offer("Melk", 20)]))
    flyers.import_flyer(db, path, *args, read=lambda p: answer([offer("Agurk", 9)]))
    assert db.query(models.Flyer).count() == 1
    assert [(o.canonical_name, o.total_price) for o in db.query(models.FlyerOffer)] == [("Agurk", 9)]


@pytest.mark.parametrize("raw, args, message", [
    ("not json", {}, "invalid answer"),
    (answer([], chain=None, valid_from="2026-10-20", valid_to="2026-10-26"), {}, "No chain"),
    (answer([]), {}, "No dates"),
    (answer([], valid_from="2026-10-26", valid_to="2026-10-20"), {}, "end"),
])
def test_import_errors(clean, db, tmp_path, raw, args, message):
    with pytest.raises(flyers.FlyerError, match=message):
        flyers.import_flyer(db, flyer_file(tmp_path), read=lambda p: raw, **args)
    assert db.query(models.Flyer).count() == 0


def test_only_offers_valid_today_are_active(clean, db):
    add_flyer(db, "Kiwi", [{"canonical_name": "Nå", "total_price": 1}])
    add_flyer(db, "Kiwi", [{"canonical_name": "Gammel", "total_price": 1}], (TODAY - timedelta(10), TODAY - timedelta(4)))
    add_flyer(db, "Kiwi", [{"canonical_name": "Snart", "total_price": 1}], (TODAY + timedelta(1), TODAY + timedelta(7)))
    add_flyer(db, "Kiwi", [{"canonical_name": "Uten pris", "total_price": None}])
    add_flyer(db, "Kiwi", [{"canonical_name": "Siste dag", "total_price": 1}], (TODAY - timedelta(6), TODAY))
    names = sorted(o.canonical_name for o, _ in flyers.active_offers(db, TODAY))
    assert names == ["Nå", "Siste dag"]



def test_listing_offers_leaves_the_raw_flyer_data_unread(clean, db):
    # Every offer row would otherwise carry its flyer's whole raw answer:
    # 1,500 offers took over a gigabyte and killed the 512 MB container.
    flyer = add_flyer(db, "Kiwi", [{"canonical_name": "Nå", "total_price": 1}])
    flyer.raw_response = "x" * 100_000
    db.commit()
    db.expunge_all()
    [(_, loaded)] = flyers.active_offers(db, TODAY)
    assert "raw_response" not in loaded.__dict__
    assert loaded.raw_response == "x" * 100_000

# Is it really an offer?

def judged(db, name, chain, price, **extra):
    flyer = add_flyer(db, chain, [{"canonical_name": name, "total_price": price, **extra}])
    rows = (db.query(models.ReceiptItem, models.Receipt).join(models.Receipt)
            .filter(models.ReceiptItem.canonical_name == name).all())
    return flyers.judge(flyer.offers[0], flyer, rows)


def test_real_discount_against_the_chains_usual_price(clean, db):
    add_receipt_price(db, "Kaffe", "KIWI", 100, days_ago=20)
    add_receipt_price(db, "Kaffe", "Kiwi", 110, days_ago=10)   # latest before the offer: the usual price
    add_receipt_price(db, "Kaffe", "Kiwi", 70, days_ago=0)     # bought on offer: ignored
    v = judged(db, "Kaffe", "Kiwi", 77)
    assert v["kind"] == "real" and v["usual"] == 110 and v["discount"] == 0.3


@pytest.mark.parametrize("price, kind", [(96, "small"), (99, "not"), (120, "not")])
def test_small_or_no_discount(clean, db, price, kind):
    add_receipt_price(db, "Kaffe", "Kiwi", 100, days_ago=10)
    assert judged(db, "Kaffe", "Kiwi", price)["kind"] == kind


def test_eggs_are_judged_per_egg(clean, db):
    add_receipt_price(db, "Egg", "Kiwi", 29.9, days_ago=10, unit="stk", quantity=6)   # 4,98 an egg
    v = judged(db, "Egg", "Kiwi", 49.9, unit="stk", quantity=12)                         # 4,16 an egg
    assert (v["kind"], v["per"], v["usual"]) == ("real", "stk", 4.98)
    # A pack whose eggs weren't counted can't be held against a counted one
    add_receipt_price(db, "Egg", "Meny", 30.0, days_ago=5, unit="stk")
    assert judged(db, "Egg", "Kiwi", 49.9, unit="stk", quantity=12)["cheaper_chain"] is None


def test_cheaper_elsewhere_without_an_offer(clean, db):
    add_receipt_price(db, "Kaffe", "Kiwi", 100, days_ago=10)
    add_receipt_price(db, "Kaffe", "Rema 1000", 60, days_ago=30)
    add_receipt_price(db, "Kaffe", "Rema 1000", 70, days_ago=3)   # latest at Rema
    v = judged(db, "Kaffe", "Kiwi", 80)
    assert v["kind"] == "cheaper_elsewhere"
    assert (v["cheaper_chain"], v["cheaper_price"], v["discount"]) == ("Rema 1000", 70, 0.2)


def test_compares_per_100_g(clean, db):
    # 500 g for 50 is 10 kr/100 g; the usual 1 kg for 80 is 8 kr/100 g
    add_receipt_price(db, "Ris", "Kiwi", 80, days_ago=10, unit="g", quantity=1000)
    add_receipt_price(db, "Ris", "Meny", 5, days_ago=10)  # no size: can't be compared
    v = judged(db, "Ris", "Kiwi", 50, unit="g", quantity=500)
    assert (v["kind"], v["per"], v["usual"]) == ("not", "100 g", 8)


def test_without_receipts_we_cant_tell(clean, db):
    assert judged(db, "Safran", "Kiwi", 40)["kind"] == "unknown"


def test_cheapest_known_when_only_other_chains_have_prices(clean, db):
    add_receipt_price(db, "Kaffe", "Meny", 100, days_ago=10)
    assert judged(db, "Kaffe", "Kiwi", 90)["kind"] == "cheapest_known"


def test_offers_endpoint_lists_the_best_deals_first(clean, db):
    add_receipt_price(db, "Kaffe", "Kiwi", 100, days_ago=10)
    add_receipt_price(db, "Smør", "Kiwi", 40, days_ago=10)
    add_receipt_price(db, "Melk", "Kiwi", 20, days_ago=10)
    add_flyer(db, "Kiwi", [
        {"canonical_name": "Melk", "total_price": 20},
        {"canonical_name": "Safran", "total_price": 40},
        {"canonical_name": "Smør", "total_price": 36},
        {"canonical_name": "Kaffe", "total_price": 60, "deal": "Ukens kupp", "is_member_price": 1},
        {"canonical_name": "Brus", "total_price": 50, "pack_size": 2, "unit": "ml", "quantity": 1500},
    ])
    data = clean.get("/offers").json()
    assert [o["canonical_name"] for o in data] == ["Kaffe", "Smør", "Brus", "Safran", "Melk"]
    coffee = data[0]
    assert coffee["chain"] == "Kiwi" and coffee["deal"] == "Ukens kupp" and coffee["is_member_price"] is True
    assert coffee["verdict"]["kind"] == "real" and coffee["valid_to"] == str(WEEK[1])
    soda = data[2]
    assert (soda["each"], soda["per_unit"]) == (25, 1.67)


def test_a_flyer_claim_ranks_above_lowest_known(clean, db):
    add_receipt_price(db, "Melk", "Meny", 25, days_ago=3)
    add_flyer(db, "Kiwi", [
        {"canonical_name": "Melk", "total_price": 20},                     # lowest known: Meny has 25
        {"canonical_name": "Ost", "total_price": 60, "pre_price": 100},    # the flyer says -40 %
        {"canonical_name": "Brød", "total_price": 30, "pre_price": 40},    # the flyer says -25 %
    ])
    data = clean.get("/offers").json()
    assert [(o["canonical_name"], o["verdict"]["kind"]) for o in data] == [
        ("Ost", "claimed"), ("Brød", "claimed"), ("Melk", "cheapest_known")]


def test_each_product_shows_its_best_offer_before_any_shows_a_second(clean, db):
    add_flyer(db, "Kiwi", [{"canonical_name": "Energidrikk", "total_price": p, "pre_price": 40} for p in (10, 12, 14)])
    add_flyer(db, "Meny", [{"canonical_name": "Kaffe", "total_price": 30, "pre_price": 40}])
    names = [(o["canonical_name"], o["total_price"]) for o in clean.get("/offers").json()]
    # Kaffe's -25 % claim beats the second and third energy drink
    assert names == [("Energidrikk", 10), ("Kaffe", 30), ("Energidrikk", 12), ("Energidrikk", 14)]
    assert [o["total_price"] for o in clean.get("/offers?best=1").json()] == [10, 30]


def test_offers_say_what_is_sold_by_weight(clean, db):
    add_flyer(db, "Coop Prix", [
        {"canonical_name": "Appelsiner", "raw_name": "APPELSINER Pr kg. Løsvekt", "total_price": 29.9, "unit": "g", "quantity": 1000},
        {"canonical_name": "Kjøttdeig", "raw_name": "KJØTTDEIG 400 g, pr. kg 174,75", "total_price": 69.9, "unit": "g", "quantity": 400},
    ])
    data = {o["canonical_name"]: o for o in clean.get("/offers").json()}
    assert (data["Appelsiner"]["by_weight"], data["Appelsiner"]["per_unit"]) == (True, 2.99)
    assert data["Kjøttdeig"]["by_weight"] is False


def test_an_offer_in_two_overlapping_flyers_is_listed_once(clean, db):
    # Coop Mega's weekly and weekend flyers carry the same offers; Gemini
    # named the copies differently
    bun = {"raw_name": "COOP SKOLEBRØD Løse. 100-120 g", "total_price": 10}
    add_flyer(db, "Coop Mega", [{**bun, "canonical_name": "Skolebrød"}, {"canonical_name": "Melk", "total_price": 20}])
    add_flyer(db, "Coop Mega", [{**bun, "canonical_name": "Bakervarer"}])
    add_flyer(db, "Kiwi", [{**bun, "canonical_name": "Skolebrød"}])  # another chain: its own offer
    add_flyer(db, "Coop Mega", [{**bun, "canonical_name": "Skolebrød", "total_price": 12}])  # another price
    offers = clean.get("/offers").json()
    assert sorted((o["chain"], o["total_price"]) for o in offers if o["raw_name"] == bun["raw_name"]) == [
        ("Coop Mega", 10), ("Coop Mega", 12), ("Kiwi", 10)]


def test_copies_worded_differently_but_alike_are_listed_once(clean, db):
    # The real case: Spar's two flyers had "Sjokoladepudding 2-pk Danette" and "2-pk. Danette"
    pudding = {"canonical_name": "Sjokoladepudding", "brand": "Danette", "quantity": 200, "unit": "g", "total_price": 20}
    add_flyer(db, "Spar", [{**pudding, "raw_name": "Sjokoladepudding 2-pk Danette, 200 g"}])
    add_flyer(db, "Spar", [{**pudding, "raw_name": "Sjokoladepudding 2-pk. Danette, 200 g"},
                           {**pudding, "raw_name": "Sjokoladepudding vanilje Danette", "variant": "Vanilje"}])
    names = sorted((o["variant"] or "") for o in clean.get("/offers").json())
    assert names == ["", "Vanilje"]


def test_offers_endpoint_filters(clean, db):
    add_receipt_price(db, "Kaffe", "Kiwi", 100, days_ago=10)
    add_flyer(db, "Kiwi", [{"canonical_name": "Kaffe", "total_price": 99}, {"canonical_name": "Melk", "total_price": 20}])
    add_flyer(db, "Meny", [{"canonical_name": "Kaffe", "total_price": 60}])
    assert [(o["chain"], o["total_price"]) for o in clean.get("/offers?navn=Kaffe").json()] == [("Meny", 60), ("Kiwi", 99)]
    best = clean.get("/offers?best=1").json()
    assert [(o["canonical_name"], o["chain"]) for o in best] == [("Kaffe", "Meny"), ("Melk", "Kiwi")]


def test_offers_are_compressed(clean, db):
    add_flyer(db, "Kiwi", [{"canonical_name": f"Vare {i}", "total_price": i + 1} for i in range(30)])
    res = clean.get("/offers", headers={"Accept-Encoding": "gzip"})
    assert res.headers["content-encoding"] == "gzip"


def test_offers_endpoint_when_there_are_none(clean):
    assert clean.get("/offers").json() == []


# Offers in the shopping list

def test_an_offer_beats_the_receipt_price_in_the_basket(clean, db):
    add_receipt_price(db, "Kaffe", "Kiwi", 100, days_ago=10)
    add_receipt_price(db, "Kaffe", "Rema 1000", 90, days_ago=10)
    add_flyer(db, "Kiwi", [{"canonical_name": "Kaffe", "total_price": 70, "deal": "Ukens kupp"}])
    data = clean.post("/basket", json={"items": [{"name": "Kaffe"}]}).json()
    kiwi = data["items"][0]["prices"]["Kiwi"]
    assert kiwi["price"] == 70 and kiwi["offer_until"] == str(WEEK[1]) and kiwi["deal"] == "Ukens kupp"
    assert kiwi["date"] is None
    assert data["stores"][0]["chain"] == "Kiwi" and data["stores"][0]["offers"] == 1


def test_a_dearer_offer_is_ignored(clean, db):
    add_receipt_price(db, "Kaffe", "Kiwi", 60, days_ago=1)
    add_flyer(db, "Kiwi", [{"canonical_name": "Kaffe", "total_price": 70}])
    kiwi = clean.post("/basket", json={"items": [{"name": "Kaffe"}]}).json()["items"][0]["prices"]["Kiwi"]
    assert kiwi["price"] == 60 and kiwi["offer_until"] is None


def test_products_only_on_offer_can_be_priced(clean, db):
    add_flyer(db, "Meny", [{"canonical_name": "Safran", "total_price": 40}])
    data = clean.post("/basket", json={"items": [{"name": "Safran"}]}).json()
    assert data["unknown"] == [] and data["stores"][0]["chain"] == "Meny"


def test_offers_dont_make_a_chain_look_cheap(clean, db):
    for name, price in [("A", 10), ("B", 20), ("C", 30)]:
        add_receipt_price(db, name, "Kiwi", price, days_ago=5)
        add_receipt_price(db, name, "Meny", price, days_ago=5)
    rows = (db.query(models.ReceiptItem, models.Receipt).join(models.Receipt).all())
    deals = basket.flyer_deals([(models.FlyerOffer(canonical_name=n, total_price=1, pack_size=1),
                                 models.Flyer(chain="Meny", valid_from=WEEK[0], valid_to=WEEK[1])) for n in "ABC"])
    result = basket.price_list([("A", 1)], basket.latest_offers(rows), deals)
    assert {s["chain"]: s["index"] for s in result["stores"]} == {"Kiwi": 1.0, "Meny": 1.0}


def test_flyer_deals_keep_the_cheapest_per_chain():
    week = models.Flyer(chain="Kiwi", valid_from=WEEK[0], valid_to=WEEK[1])
    rows = [(models.FlyerOffer(canonical_name="Kaffe", total_price=p, pack_size=1), week) for p in (80, 70, None)]
    assert basket.flyer_deals(rows)[("Kaffe", "Kiwi")].each == 70


# The command line

def test_cli_imports_files(clean, db, tmp_path, capsys):
    path = flyer_file(tmp_path)
    raw = answer([offer("Agurk", 10), offer("Melk", None)])
    code = cli.main([str(path), "--chain", "Kiwi", "--from", "2026-10-20", "--to", "2026-10-26"], read=lambda p: raw)
    assert code == 0
    assert "2 offers (1 with a price) from Kiwi, 2026-10-20 to 2026-10-26" in capsys.readouterr().out


def test_cli_reports_failures_and_carries_on(clean, db, tmp_path, capsys):
    good, bad = flyer_file(tmp_path, "good.pdf"), flyer_file(tmp_path, "bad.pdf")
    raw = {"good.pdf": answer([offer("Agurk", 10)], valid_from="2026-10-20", valid_to="2026-10-26"), "bad.pdf": "nope"}
    code = cli.main([str(bad), str(tmp_path / "missing.pdf"), str(good)], read=lambda p: raw[p.name])
    assert code == 1
    err = capsys.readouterr().err
    assert "bad.pdf: Gemini returned an invalid answer" in err and "missing.pdf: no such file" in err
    assert db.query(models.Flyer).count() == 1


# Like for like: a canonical name ("Kyllingfilet") covers many products

def add_branded_price(db, name, chain, price, days_ago, brand=None, unit=None, quantity=None):
    add_receipt_price(db, name, chain, price, days_ago, unit=unit, quantity=quantity)
    item = db.query(models.ReceiptItem).order_by(models.ReceiptItem.id.desc()).first()
    item.brand = brand
    db.commit()


def test_an_old_receipt_says_nothing_about_the_usual_price(clean, db):
    # The real case: a 1.5 kg First Price offer judged "real" against a 20-month-old receipt
    add_receipt_price(db, "Kyllingfilet", "Kiwi", 299, days_ago=flyers.RECENT_DAYS + 30, unit="g", quantity=2000)
    v = judged(db, "Kyllingfilet", "Kiwi", 187, unit="g", quantity=1500)
    assert v["kind"] == "unknown" and v["usual"] is None


def test_an_old_receipt_elsewhere_is_ignored_too(clean, db):
    add_receipt_price(db, "Kaffe", "Meny", 50, days_ago=flyers.RECENT_DAYS + 30)
    assert judged(db, "Kaffe", "Kiwi", 90)["kind"] == "unknown"


def test_another_brand_is_another_product(clean, db):
    add_branded_price(db, "Buljong", "Kiwi", 60, days_ago=10, brand="Toro")
    v = judged(db, "Buljong", "Kiwi", 30, brand="Maggi")
    assert v["kind"] == "unknown"
    # The same brand, written differently, is the same
    add_branded_price(db, "Buljong", "Kiwi", 40, days_ago=5, brand="MAGGI")
    assert judged(db, "Buljong", "Kiwi", 30, brand="Maggi")["usual"] == 40


def test_a_brand_list_matches_any_of_its_brands(clean, db):
    add_branded_price(db, "Brus", "Kiwi", 30, days_ago=10, brand="Farris")
    assert judged(db, "Brus", "Kiwi", 20, brand="Coca-Cola / Farris")["kind"] == "real"


def test_a_much_bigger_or_smaller_pack_is_another_product(clean, db):
    # A 6-pack of 47 g bars against a 100 g plate, a 225 g salad bag against a 65 g one
    add_receipt_price(db, "Kvikk lunsj", "Kiwi", 29.9, days_ago=30, unit="g", quantity=100)
    assert judged(db, "Kvikk lunsj", "Kiwi", 49.9, unit="g", quantity=282)["kind"] == "unknown"
    add_receipt_price(db, "Salatmiks", "Kiwi", 16.9, days_ago=30, unit="g", quantity=65)
    assert judged(db, "Salatmiks", "Kiwi", 26.9, unit="g", quantity=225)["kind"] == "unknown"
    # Within a factor of two it's still compared, per 100 g
    add_receipt_price(db, "Ris", "Kiwi", 80, days_ago=10, unit="g", quantity=1000)
    assert judged(db, "Ris", "Kiwi", 30, unit="g", quantity=500)["kind"] == "real"


def test_a_multibuy_is_compared_by_the_size_of_one_item(clean, db):
    add_receipt_price(db, "Saus", "Kiwi", 49.5, days_ago=10, unit="g", quantity=360)
    v = judged(db, "Saus", "Kiwi", 100, unit="g", quantity=360, pack_size=3)
    assert v["kind"] == "real" and v["discount"] == pytest.approx(0.327, abs=0.001)
