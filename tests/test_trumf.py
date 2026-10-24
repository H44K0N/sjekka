"""Trumf purchase histories: prices from the file, names from Gemini."""
import asyncio
import json
import logging

import pytest

from app import models, trumf, worker
from tests import helpers

# Trumf's format, with made-up contents
HISTORY = [
    {"dato": "23.10.2026", "kvitteringsnummer": "196075", "kjede": None, "butikknavn": "MENY Sandvika Storsenter",
     "totaltBelop": "156.38", "korttype": "ZTPT", "kanal": None, "varelinjer": [
         {"varenavn": "TINE LETTMELK 1L", "vareAntallVekt": "2.000", "vareBelop": "49.80"},
         {"varenavn": "APPELSINER KG", "vareAntallVekt": "1.570", "vareBelop": "70.49"},
         {"varenavn": "TAST PANT UTGÅENDE SALG AV EMB", "vareAntallVekt": "1.000", "vareBelop": "3.00"},
         {"varenavn": "BÆREPOSE PLAST KIWI", "vareAntallVekt": "1.000", "vareBelop": "6.75"},
         {"varenavn": "TRENDKORT DOBBELT", "vareAntallVekt": "1.000", "vareBelop": "26.34"},
     ]},
    {"dato": "02.09.2025", "kvitteringsnummer": "88812", "kjede": None, "butikknavn": "KIWI 305 Kjørbo",
     "totaltBelop": "34.90", "korttype": "ZTPT", "kanal": None, "varelinjer": [
         {"varenavn": "TINE LETTMELK 1L", "vareAntallVekt": "1.000", "vareBelop": "24.90"},
         {"varenavn": "Ukjent produkt", "vareAntallVekt": "1.000", "vareBelop": "-5.00"},
         {"varenavn": "VILJE HÅNDSÅPE REFILL 1L", "vareAntallVekt": "1.000", "vareBelop": "15.00"},
     ]},
]

NAMES = {
    "APPELSINER KG": dict(canonical_name="Appelsiner", category="Frukt og grønt", unit="g"),
    "TINE LETTMELK 1L": dict(canonical_name="Lettmelk", brand="Tine", category="Meieri", quantity=1000, unit="ml"),
    "TRENDKORT DOBBELT": dict(canonical_name="Kort", category="Annet", product=False),
    "VILJE HÅNDSÅPE REFILL 1L": dict(canonical_name="Håndsåpe", brand="Vilje", category="Hygiene", quantity=1000, unit="ml"),
    "Ukjent produkt": dict(canonical_name="Ukjent", category="Annet", product=False),
}


def namer(calls=None):
    def name(texts):
        if calls is not None:
            calls.append(texts)
        return {i: trumf.LineName(index=i, **NAMES[t]) for i, t in enumerate(texts)}, json.dumps({"named": texts})
    return name


@pytest.mark.parametrize("doc, found", [
    (HISTORY, True),
    ({"kvitteringer": HISTORY}, True),  # a part of a split history keeps the list
    ([{"dato": "1.1.2026", "butikknavn": "X", "varelinjer": [{"navn": "Y"}]}], False),
    ({"varer": [{"navn": "AGURK", "pris": 15}]}, False),
    ([], False),
])
def test_a_trumf_history_is_recognised_by_its_fields(doc, found):
    assert (trumf.receipts_in(doc) is not None) is found


def test_prices_come_from_the_file_and_only_names_from_gemini():
    calls = []
    receipts, raw = trumf.read(HISTORY, namer(calls))
    # One call, for the distinct lines that might be products
    assert calls == [["APPELSINER KG", "TINE LETTMELK 1L", "TRENDKORT DOBBELT", "Ukjent produkt", "VILJE HÅNDSÅPE REFILL 1L"]]
    meny, kiwi = receipts
    assert (meny.purchase_date, meny.store_chain, meny.total) == ("2026-10-23", "MENY Sandvika Storsenter", 156.38)
    milk, oranges = meny.items  # deposit, bag and card left out
    assert (milk.pack_size, milk.total_price, milk.quantity, milk.unit) == (2, 49.80, 1000, "ml")
    assert (oranges.quantity, oranges.unit, oranges.pack_size, oranges.total_price) == (1570, "g", 1, 70.49)
    assert kiwi.purchase_date == "2025-09-02"
    assert [i.canonical_name for i in kiwi.items] == ["Lettmelk", "Håndsåpe"]  # the negative line left out


def test_a_receipt_that_does_not_add_up_is_logged(caplog):
    broken = [dict(HISTORY[1], totaltBelop="99.00")]
    with caplog.at_level(logging.WARNING, logger="sjekka.worker"):
        trumf.read(broken, namer())
    assert "lines add up to 34.9, total says 99.0" in caplog.text


def test_an_uploaded_history_is_read_without_gemini_reading_prices(client, db, monkeypatch):
    monkeypatch.setattr(trumf, "gemini_names", namer())
    monkeypatch.setattr(worker, "extract_receipts", lambda path: pytest.fail("Trumf files aren't read by Gemini"))
    res = helpers.upload(client, json.dumps(HISTORY, ensure_ascii=False).encode(), name="trumf.json")
    while job := worker.next_job(db):
        asyncio.run(worker.process_job(job, db))

    job = db.query(models.Job).one()
    assert job.status == "done" and job.model.startswith(trumf.MODEL_PREFIX)
    items = {(i.receipt.purchase_date.isoformat(), i.canonical_name): i for i in db.query(models.ReceiptItem)}
    assert set(items) == {("2026-10-23", "Lettmelk"), ("2026-10-23", "Appelsiner"), ("2025-09-02", "Lettmelk"), ("2025-09-02", "Håndsåpe")}
    assert items[("2026-10-23", "Lettmelk")].price_per_unit == 2.49  # per 100 ml, for one of the two
    assert items[("2026-10-23", "Appelsiner")].price_per_unit == 4.49  # per 100 g: 44,90 kr/kg
    assert client.get(f"/jobs/batch/{res.json()['batch']}").json()["receipts"] == 2

    # The same history again adds nothing: every receipt is a duplicate
    helpers.upload(client, json.dumps(HISTORY, ensure_ascii=False).encode(), name="trumf-again.json")
    while job := worker.next_job(db):
        asyncio.run(worker.process_job(job, db))
    assert db.query(models.Receipt).count() == 2
