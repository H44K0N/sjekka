"""Importing flyers from eTilbudsavis (Tjek), with the API and Gemini faked."""
import asyncio
import json
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from app import basket, models, tjek
from util import import_tjek as cli


def tjek_offer(heading, price, description="", unit="g", size=400, size_to=None, pieces=1, pre_price=None):
    return {
        "heading": heading, "description": description,
        "pricing": {"price": price, "pre_price": pre_price, "currency": "NOK"},
        "quantity": {"unit": {"symbol": unit} if unit else None, "size": {"from": size, "to": size_to or size},
                     "pieces": {"from": pieces, "to": pieces}},
    }


def catalog(id_, name, run_from="2026-10-18T22:00:00+0000", run_till="2026-10-25T21:59:59+0000"):
    return {"id": id_, "label": f"{name} uke 43", "branding": {"name": name}, "run_from": run_from, "run_till": run_till}


CATALOGS = [catalog("kiwi1", "KIWI"), catalog("bygg", "Obs BYGG"), catalog("jula", "Jula"), catalog("meny1", "MENY")]
OFFERS = {
    "kiwi1": [tjek_offer("KJØTTDEIG 10 % FETT", 69.9, "400 g, Folkets, pr. kg 174,75"),
              tjek_offer("COCA-COLA ZERO", 89, "6 x 1,5 liter", unit="l", size=1.5, pieces=6),
              tjek_offer("Gratis kaffe", None)],
    "meny1": [tjek_offer(f"Vare {i}", 10, unit="pcs", size=1) for i in range(150)],
}


def fake_api(requests=None):
    def handler(request):
        if requests is not None:
            requests.append(request)
        if request.url.path == "/v2/catalogs":
            return httpx.Response(200, json=CATALOGS)
        items = OFFERS.get(request.url.params["catalog_id"], [])
        offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
        return httpx.Response(200, json=items[offset:offset + limit])
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def clean(client, db):
    db.query(models.FlyerOffer).delete()
    db.query(models.Flyer).delete()
    db.commit()
    return client


def test_only_grocery_chains_we_know():
    names = [c["branding"]["name"] for c in tjek.grocery_catalogs(fake_api(), 59.9, 10.7, 20000)]
    assert names == ["KIWI", "MENY"]


# Every flyer Tjek listed around Oslo on 24 October 2026. Megaflis (tiles)
# once came in as Coop Mega, with 423 offers of flooring and LED spots.
TJEK_BRANDS = ["Afood Market", "Apotek 1", "Bohus", "Bunnpris", "Byggmakker", "Byggmax", "Coop Marked", "Coop Mega",
               "Coop Prix", "Elon", "Europris", "Extra", "Felleskjøpet", "Gigaboks", "Holdbart", "JYSK", "Jacobs", "Jernia",
               "Joker", "Jula", "KIWI", "Kid", "MAXBO", "MENY", "Matkroken", "Megaflis", "Møbelringen", "Nille",
               "Nærbutikken", "Obs", "Obs! Bygg", "POWER", "REMA 1000", "Right Price Tiles", "Rusta", "SPAR", "Selfmade",
               "Skeidar", "Spar Kjøp", "Sport Outlet", "thansen"]


def test_every_real_flyer_brand_maps_to_the_right_chain():
    class Listing:
        def get(self, url, params):
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: [{"id": b, "branding": {"name": b}} for b in TJEK_BRANDS])
    kept = {c["branding"]["name"]: basket.chain_name(c["branding"]["name"]) for c in tjek.grocery_catalogs(Listing(), 59.9, 10.7, 20000)}
    assert kept == {"Bunnpris": "Bunnpris", "Coop Mega": "Coop Mega", "Coop Prix": "Coop Prix", "Extra": "Extra",
                    "Joker": "Joker", "KIWI": "Kiwi", "MENY": "Meny", "Obs": "Obs", "REMA 1000": "Rema 1000", "SPAR": "Spar"}


def test_offers_are_fetched_page_by_page():
    requests = []
    assert len(tjek.catalog_offers(fake_api(requests), "meny1")) == 150
    assert [r.url.params["offset"] for r in requests] == ["0", "100"]


def test_dates_are_oslo_days():
    assert tjek.local_date("2026-10-18T22:00:00+0000") == date(2026, 10, 19)
    assert tjek.local_date("2026-10-25T21:59:59+0000") == date(2026, 10, 25)


@pytest.mark.parametrize("offer, expected", [
    (tjek_offer("x", 1, unit="g", size=400), (400, "g", 1)),
    (tjek_offer("x", 1, unit="kg", size=1.5), (1500, "g", 1)),
    (tjek_offer("x", 1, unit="l", size=1.5, pieces=6), (1500, "ml", 6)),
    (tjek_offer("x", 1, unit="cl", size=33), (330, "ml", 1)),
    (tjek_offer("x", 1, unit="g", size=320, size_to=370), (345, "g", 1)),
    (tjek_offer("x", 1, unit="pcs", size=15), (None, "stk", 1)),
    (tjek_offer("x", 1, unit=None), (None, "stk", 1)),
])
def test_sizes(offer, expected):
    assert tjek.size_of(offer) == expected


@pytest.mark.parametrize("offer, expected", [
    (tjek_offer("x", 50, pre_price=74.9), 74.9),
    (tjek_offer("x", 50, "400g, Folkets (125,00/kg) Førpris 74,90"), 74.9),
    (tjek_offer("x", 30, "Førpris: 34,90–37,50"), None),        # a range: which one?
    (tjek_offer("x", 14.9, "Pr kg 149,00 Førpris 229,00"), None),  # a kilo price, not a discount
    (tjek_offer("x", 50, "Førpris 45,00"), None),                # not lower
    (tjek_offer("x", 50, "Ny pris"), None),
])
def test_before_price(offer, expected):
    assert tjek.before_price(offer) == expected


def test_matching_names_without_gemini():
    offers = [tjek_offer("KJØTTDEIG 10 % FETT", 1), tjek_offer("Gresk yoghurt naturell", 1), tjek_offer("Sørlandsis*", 1)]
    names, raw = tjek.matched_names(offers, ["Kjøttdeig", "Yoghurt naturell", "Yoghurt", "Is"])
    assert [names[i].canonical_name for i in range(3)] == ["Kjøttdeig", "Yoghurt naturell", "Sørlandsis"]
    assert json.loads(raw) == {"matched_without_gemini": True}


def test_gemini_names_in_one_text_call(monkeypatch):
    calls = []
    answer = {"offers": [{"index": 0, "canonical_name": "Kjøttdeig", "brand": "Folkets", "category": "Kjøtt"}]}
    monkeypatch.setattr(tjek.client.models, "generate_content",
                        lambda **kw: calls.append(kw) or SimpleNamespace(text=json.dumps(answer)))
    names, raw = tjek.gemini_names([tjek_offer("KJØTTDEIG 10 % FETT", 69.9, "400 g, Folkets")])
    assert names[0].brand == "Folkets" and json.loads(raw) == answer
    prompt = calls[0]["contents"][0]
    assert "0 | KJØTTDEIG 10 % FETT 400 g, Folkets" in prompt and "canonical_name" in prompt
    assert calls[0]["config"].response_schema is tjek.OfferNames


def namer(offers):
    return {i: tjek.OfferName(index=i, canonical_name=o["heading"].split()[0], category="Annet")
            for i, o in enumerate(offers)}, "raw"


def test_import_catalog(clean, db):
    flyer = tjek.import_catalog(db, CATALOGS[0], OFFERS["kiwi1"], namer)
    assert (flyer.chain, flyer.valid_from, flyer.valid_to, flyer.source) == ("Kiwi", date(2026, 10, 19), date(2026, 10, 25), "tjek:kiwi1")
    assert json.loads(flyer.raw_response)["names"] == "raw"
    mince, cola = flyer.offers  # the offer without a price is left out
    assert (mince.canonical_name, mince.quantity, mince.unit, mince.price_per_unit) == ("Kjøttdeig", 400, "g", 17.48)
    # "6 x 1,5 liter" is one 9-litre pack, not "6 for 89"
    assert (cola.pack_size, cola.deal, cola.quantity, cola.price_per_unit) == (1, None, 9000, 0.99)
    assert mince.raw_name == "KJØTTDEIG 10 % FETT 400 g, Folkets, pr. kg 174,75"

    # Importing again replaces it
    tjek.import_catalog(db, CATALOGS[0], OFFERS["kiwi1"][:1], namer)
    assert db.query(models.Flyer).count() == 1 and db.query(models.FlyerOffer).count() == 1


def test_a_multi_buy_stays_n_for_x(clean, db):
    battery = tjek_offer("Battery 0,5 l", 30, "med og uten sukker", unit="l", size=0.5, pieces=2)
    offer, = tjek.import_catalog(db, CATALOGS[0], [battery], namer).offers
    assert (offer.pack_size, offer.deal, offer.quantity, offer.price_per_unit) == (2, "2 for 30", 500, 3.0)


def test_non_grocery_offers_are_left_out(clean, db):
    def names(offers):
        return {0: tjek.OfferName(index=0, canonical_name="Kjøttdeig", category="Kjøtt"),
                1: tjek.OfferName(index=1, canonical_name="Laminatgulv", category="Annet", grocery=False)}, "raw"
    offers = tjek.import_catalog(db, CATALOGS[0], OFFERS["kiwi1"], names).offers
    assert [o.canonical_name for o in offers] == ["Kjøttdeig"]


def test_the_prompt_asks_which_offers_are_groceries():
    assert "**grocery**: false" in tjek.NAMES_PROMPT and "flooring" in tjek.NAMES_PROMPT
    assert "grocery" in tjek.OfferName.model_json_schema()["properties"]


def test_offers_gemini_skipped_are_left_out(clean, db):
    def partial(offers):
        return {0: tjek.OfferName(index=0, canonical_name="Kjøttdeig", category="Kjøtt")}, "raw"
    assert len(tjek.import_catalog(db, CATALOGS[0], OFFERS["kiwi1"], partial).offers) == 1


def test_catalog_without_prices(clean, db):
    assert tjek.import_catalog(db, CATALOGS[0], [tjek_offer("Oppskrift", None)], namer) is None


def test_claimed_discount_when_receipts_cant_tell(clean, db):
    tjek.import_catalog(db, CATALOGS[0], [tjek_offer("Smøremyk", 40, pre_price=52.9)], namer)
    from app import flyers
    [offer] = flyers.offer_list(db, date(2026, 10, 20))
    assert offer["verdict"]["kind"] == "claimed" and offer["verdict"]["discount"] == 0.244 and offer["pre_price"] == 52.9


def test_cli(clean, db, capsys):
    code = cli.main(["--chain", "Kiwi"], client=fake_api(), namer=namer)
    assert code == 0
    out = capsys.readouterr().out
    assert "Kiwi «KIWI uke 43»: 2 offers, 2026-10-19 to 2026-10-25" in out and "Meny" not in out


def test_cli_without_gemini_matches_known_names(clean, db, capsys):
    receipt = models.Receipt(image_filename="r.jpg", store_chain="Rema")
    db.add(receipt)
    db.flush()
    db.add(models.ReceiptItem(receipt_id=receipt.id, canonical_name="Kjøttdeig", total_price=60))
    db.commit()
    assert cli.main(["--chain", "kiwi", "--no-gemini"], client=fake_api()) == 0
    names = {o.canonical_name for o in db.query(models.FlyerOffer)}
    assert names == {"Kjøttdeig", "Coca-cola zero"}


def test_cli_reports_failures(clean, db, capsys):
    def broken(offers):
        raise RuntimeError("Gemini is down")
    assert cli.main([], client=fake_api(), namer=broken) == 1
    assert "Kiwi «KIWI uke 43»: Gemini is down" in capsys.readouterr().err


def test_cli_empty_catalog(clean, db, capsys):
    OFFERS["empty"] = []
    CATALOGS.append(catalog("empty", "Joker"))
    try:
        assert cli.main(["--chain", "Joker"], client=fake_api(), namer=namer) == 0
        assert "no priced offers" in capsys.readouterr().out
    finally:
        CATALOGS.pop()
        del OFFERS["empty"]


def test_background_refresh_imports_only_new_flyers(clean, db, monkeypatch):
    calls = []
    monkeypatch.setattr(tjek, "gemini_names", lambda offers: (calls.append(len(offers)), namer(offers))[1])
    assert tjek.refresh_new_flyers(fake_api()) == 2
    assert calls == [2, 150]
    # The next pass finds nothing new and asks Gemini nothing
    assert tjek.refresh_new_flyers(fake_api()) == 0
    assert calls == [2, 150]


def test_background_refresh_logs_failures_and_goes_on(clean, db, monkeypatch, caplog):
    def broken(offers):
        raise RuntimeError("quota")
    monkeypatch.setattr(tjek, "gemini_names", broken)
    assert tjek.refresh_new_flyers(fake_api()) == 0
    assert "Flyer Kiwi «KIWI uke 43»: quota" in caplog.text


def test_refresh_loop_survives_errors(monkeypatch, caplog):
    passes = []

    def fail():
        passes.append(1)
        raise httpx.ConnectError("no network")

    async def stop(seconds):
        assert seconds == tjek.FLYER_REFRESH_HOURS * 3600
        raise asyncio.CancelledError

    monkeypatch.setattr(tjek, "refresh_new_flyers", fail)
    monkeypatch.setattr(tjek.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tjek.run_refresh())
    assert passes == [1] and "Flyer refresh failed: no network" in caplog.text
