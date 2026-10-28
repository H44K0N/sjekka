import asyncio
from datetime import date, timedelta

import pytest

from app import models, trumf, worker
from app.schema import ExtractedItem, ExtractedReceipt
from tests.helpers import api_error


def receipt(**overrides):
    items = [
        ExtractedItem(raw_name="GILDE KJØTTDEIG 400G", canonical_name="kjøttdeig ", brand=" Gilde",
                      category="Kjøtt", quantity=400, unit="g", total_price=89.0),
        ExtractedItem(raw_name="LABAN SEIGMENN 260G", canonical_name="Seigmenn", category="Godteri",
                      quantity=260, unit="g", pack_size=3, total_price=38.7),
        ExtractedItem(raw_name="AGURK", canonical_name="Agurk", category="Frukt og grønt",
                      unit="stk", pack_size=2, total_price=30.0),
    ]
    fields = dict(store_chain="Kiwi", purchase_date="2026-10-20", items=items, total=157.7)
    return ExtractedReceipt(**(fields | overrides))


def run(db, extract, filename="a.jpg"):
    job = models.Job(filename=filename)
    db.add(job)
    db.commit()
    worker.extract_receipt = extract
    asyncio.run(worker.process_job(job, db))
    db.refresh(job)
    return job


@pytest.fixture(autouse=True)
def restore_extractor():
    original = worker.extract_receipt
    yield
    worker.extract_receipt = original


def test_saves_receipt_with_computed_unit_prices(client, db):
    job = run(db, lambda path, known=(): receipt().model_dump_json())
    assert job.status == "done"
    items = {i.raw_name: i for i in db.query(models.ReceiptItem)}
    mince = items["GILDE KJØTTDEIG 400G"]
    assert mince.canonical_name == "Kjøttdeig"
    assert mince.brand == "Gilde"
    assert mince.price_per_unit == 22.25  # per 100 g
    assert items["LABAN SEIGMENN 260G"].price_per_unit == 4.96  # one bag of three
    assert items["AGURK"].price_per_unit == 15.0  # per stk


def test_a_multipack_is_priced_for_the_whole_pack(client, db):
    yoghurt = ExtractedItem(raw_name="Yoghurt kokos 4x150g tine", canonical_name="Yoghurt", category="Meieri",
                            quantity=150, unit="g", total_price=20.0)
    run(db, lambda path, known=(): receipt(items=[yoghurt], total=20.0).model_dump_json())
    saved = db.query(models.ReceiptItem).one()
    assert (saved.quantity, saved.pack_size, saved.price_per_unit) == (600, 1, 3.33)


def test_a_pack_counted_in_pieces_is_priced_per_piece(client, db):
    eggs = ExtractedItem(raw_name="FROKOSTEGG FRITTGÅENDE L 12STK PRIOR", canonical_name="Egg", category="Annet",
                         unit="stk", total_price=56.4)
    run(db, lambda path, known=(): receipt(items=[eggs], total=56.4).model_dump_json())
    saved = db.query(models.ReceiptItem).one()
    assert (saved.quantity, saved.unit, saved.price_per_unit) == (12, "stk", 4.7)


def test_repair_gives_pieces_to_lines_and_offers_saved_before_and_only_once(client, db):
    receipt_row = models.Receipt(image_filename="r.jpg", store_chain="Kiwi")
    flyer = models.Flyer(chain="Kiwi", source="t", valid_from=date(2026, 10, 20), valid_to=date(2026, 10, 26))
    db.add_all([receipt_row, flyer])
    db.flush()
    db.add_all([
        models.ReceiptItem(receipt_id=receipt_row.id, raw_name="EGG 6PK PRIOR", canonical_name="Egg",
                           unit="stk", pack_size=1, total_price=29.9, price_per_unit=29.9),
        # Gemini had the pieces, but the price was per pack
        models.ReceiptItem(receipt_id=receipt_row.id, raw_name="EGG 18STK", canonical_name="Egg",
                           quantity=18, unit="stk", pack_size=1, total_price=61.2, price_per_unit=61.2),
        models.ReceiptItem(receipt_id=receipt_row.id, raw_name="AGURK", canonical_name="Agurk",
                           unit="stk", pack_size=1, total_price=15.0, price_per_unit=15.0),
        models.FlyerOffer(flyer_id=flyer.id, raw_name="EGG 15stk, S/M, Norgården", canonical_name="Egg",
                          unit="stk", pack_size=1, total_price=49.9, price_per_unit=49.9),
    ])
    db.commit()
    assert worker.repair_piece_counts(db) == 3
    assert worker.repair_piece_counts(db) == 0
    lines = {i.raw_name: (i.quantity, i.price_per_unit) for i in db.query(models.ReceiptItem)}
    assert lines == {"EGG 6PK PRIOR": (6, 4.98), "EGG 18STK": (18, 3.4), "AGURK": (None, 15.0)}
    offer = db.query(models.FlyerOffer).one()
    assert (offer.quantity, offer.price_per_unit) == (15, 3.33)


def test_repair_fixes_multipacks_saved_before_and_only_once(client, db):
    receipt_row = models.Receipt(image_filename="r.jpg", store_chain="Meny")
    flyer = models.Flyer(chain="Kiwi", source="t.pdf", valid_from=date(2026, 10, 20), valid_to=date(2026, 10, 26))
    db.add_all([receipt_row, flyer])
    db.flush()
    db.add_all([
        models.ReceiptItem(receipt_id=receipt_row.id, raw_name="Yoghurt kokos 4x150g tine", canonical_name="Yoghurt",
                           quantity=150, unit="g", pack_size=1, total_price=20.0, price_per_unit=13.33),
        models.ReceiptItem(receipt_id=receipt_row.id, raw_name="Pizza 1kg", canonical_name="Pizza",
                           quantity=1000, unit="g", pack_size=1, total_price=169.0, price_per_unit=16.9),
        models.FlyerOffer(flyer_id=flyer.id, raw_name="COLA 6X0,5L", canonical_name="Cola",
                          quantity=500, unit="ml", pack_size=1, total_price=90.0, price_per_unit=18.0),
        models.FlyerOffer(flyer_id=flyer.id, raw_name="Yoghurt 4-pk TINE 4x150 g", canonical_name="Yoghurt",
                          quantity=150, unit="g", pack_size=4, total_price=20.0, price_per_unit=3.33, deal="4 for 20"),
        models.FlyerOffer(flyer_id=flyer.id, raw_name="Battery 0,5 l", canonical_name="Energidrikk",
                          quantity=500, unit="ml", pack_size=2, total_price=30.0, price_per_unit=3.0, deal="2 for 30"),
    ])
    db.commit()

    assert worker.repair_multipacks(db) == 3
    assert worker.repair_multipacks(db) == 0
    pack = db.query(models.FlyerOffer).filter_by(canonical_name="Yoghurt").one()
    assert (pack.quantity, pack.pack_size, pack.deal, pack.price_per_unit) == (600, 1, None, 3.33)
    assert db.query(models.FlyerOffer).filter_by(canonical_name="Energidrikk").one().pack_size == 2
    rows = {r.canonical_name: r for r in [*db.query(models.ReceiptItem), *db.query(models.FlyerOffer)]}
    assert (rows["Yoghurt"].quantity, rows["Yoghurt"].price_per_unit) == (600, 3.33)
    assert (rows["Cola"].quantity, rows["Cola"].price_per_unit) == (3000, 3.0)
    assert (rows["Pizza"].quantity, rows["Pizza"].price_per_unit) == (1000, 16.9)
    db.query(models.FlyerOffer).delete()
    db.query(models.Flyer).delete()
    db.commit()


def test_duplicate_receipt_is_not_saved_twice(client, db):
    run(db, lambda path, known=(): receipt().model_dump_json(), filename="a.jpg")
    job = run(db, lambda path, known=(): receipt().model_dump_json(), filename="b.jpg")
    assert job.status == "duplicate"
    assert db.query(models.Receipt).count() == 1


def test_same_store_and_day_with_other_items_is_not_a_duplicate(client, db):
    run(db, lambda path, known=(): receipt().model_dump_json(), filename="a.jpg")
    first = receipt().items[0]
    job = run(db, lambda path, known=(): receipt(items=[first], total=first.total_price).model_dump_json(), filename="b.jpg")
    assert job.status == "done"
    assert db.query(models.Receipt).count() == 2


@pytest.mark.parametrize("code", [429, 503])
def test_rate_limits_and_overload_are_retried(client, db, code):
    def extract(path, known=()):
        raise api_error(code)
    job = run(db, extract)
    assert job.status == "pending"
    assert job.attempts == 1


def test_gives_up_after_max_attempts(client, db):
    def extract(path, known=()):
        raise api_error(503)
    job = models.Job(filename="a.jpg", attempts=worker.MAX_ATTEMPTS - 1)
    db.add(job)
    db.commit()
    worker.extract_receipt = extract
    asyncio.run(worker.process_job(job, db))
    assert job.status == "failed"


def test_client_errors_fail_immediately(client, db):
    def extract(path, known=()):
        raise api_error(400)
    job = run(db, extract)
    assert job.status == "failed"
    assert job.attempts == 1


def test_waiting_job_does_not_block_the_queue(client, db):
    now = models.utcnow()
    waiting = models.Job(filename="old.jpg", attempts=3, created_at=now - timedelta(minutes=5), updated_at=now)
    fresh = models.Job(filename="new.jpg", created_at=now)
    db.add_all([waiting, fresh])
    db.commit()
    assert worker.next_job(db).filename == "new.jpg"

    waiting.updated_at = now - worker.retry_delay(3) - timedelta(seconds=1)
    db.commit()
    assert worker.next_job(db).filename == "old.jpg"


@pytest.mark.parametrize("missing", [{"store_chain": None}, {"purchase_date": None}])
def test_receipts_without_store_or_date_are_never_duplicates(client, db, missing):
    run(db, lambda path, known=(): receipt(**missing).model_dump_json(), filename="a.jpg")
    job = run(db, lambda path, known=(): receipt(**missing).model_dump_json(), filename="b.jpg")
    assert job.status == "done"


def photo(name):
    from app.config import UPLOAD_DIR
    path = UPLOAD_DIR / name
    path.write_bytes(b"jpeg")
    return path


def test_raw_answer_and_model_are_kept(client, db):
    raw = receipt().model_dump_json()
    job = run(db, lambda path, known=(): raw)
    assert job.raw_response == raw
    assert job.model == worker.GEMINI_MODEL


@pytest.mark.parametrize("second", [False, True])
def test_photo_is_deleted_once_read(client, db, second):
    if second:  # a duplicate is also fully handled
        run(db, lambda path, known=(): receipt().model_dump_json(), filename="first.jpg")
    path = photo("p.jpg")
    job = run(db, lambda path, known=(): receipt().model_dump_json(), filename="p.jpg")
    assert job.status == ("duplicate" if second else "done")
    assert not path.exists()


@pytest.mark.parametrize("code", [503, 400])
def test_photo_is_kept_when_reading_fails(client, db, code):
    path = photo("p.jpg")
    def extract(_):
        raise api_error(code)
    run(db, extract, filename="p.jpg")
    assert path.exists()


def test_invalid_answer_fails_but_keeps_the_raw_answer_and_photo(client, db):
    raw = '{"items": [{"raw_name": "LEGO", "canonical_name": "Lego", "category": "Leker"}]}'
    path = photo("p.jpg")
    job = run(db, lambda _, known=(): raw, filename="p.jpg")
    assert job.status == "failed"
    assert "invalid receipt" in job.error
    assert job.raw_response == raw
    assert path.exists()
    assert db.query(models.Receipt).count() == 0


def test_photos_of_long_failed_jobs_are_deleted(client, db):
    now = models.utcnow()
    old, recent, done_old = photo("old.jpg"), photo("recent.jpg"), photo("done.jpg")
    db.add_all([
        models.Job(filename="old.jpg", status="failed", updated_at=now - timedelta(days=31)),
        models.Job(filename="recent.jpg", status="failed", updated_at=now - timedelta(days=29)),
        models.Job(filename="done.jpg", status="pending", updated_at=now - timedelta(days=40)),
        models.Job(filename="gone.jpg", status="failed", updated_at=now - timedelta(days=60)),  # photo already gone
    ])
    db.commit()
    assert worker.delete_old_failed_photos(db) == 1
    assert not old.exists()
    assert recent.exists() and done_old.exists()


def items(*prices, names=None):
    return [ExtractedItem(raw_name=(names or {}).get(i, f"VARE {i}"), canonical_name=f"Vare {i}", category="Annet",
                          total_price=p) for i, p in enumerate(prices)]


def saved_receipt(db, store, day, prices, filename="photo.jpg"):
    data = ExtractedReceipt(store_chain=store, purchase_date=day, items=items(*prices))
    job = models.Job(filename=filename)
    db.add(job)
    db.flush()
    worker.save_receipt(db, job, data)
    db.commit()


SHOP = [24.9, 49.8, 70.49, 37.9, 31.9, 31.9, 10.0, 43.9, 20.0, 25.0]


def test_a_photo_and_a_trumf_export_of_one_shop_are_the_same_purchase(client, db):
    saved_receipt(db, "MENY", "2026-10-23", SHOP)
    # Written differently, and the export has a bag the photo reading left out
    export = ExtractedReceipt(store_chain="MENY Sandvika Storsenter", purchase_date="2026-10-23",
                              items=items(*SHOP, 6.75, names={0: "TINE LETTMELK 1L"}))
    assert worker.find_duplicate(db, export) is not None


@pytest.mark.parametrize("store, day, prices, same", [
    ("Meny", "2026-10-23", SHOP[:5] + [99.0, 88.0, 77.0, 66.0, 55.0], False),  # half alike: another shop
    ("Kiwi", "2026-10-23", SHOP, False),                                      # another chain
    ("Meny", "2026-10-24", SHOP, False),                                      # another day
])
def test_other_shops_are_not_duplicates(client, db, store, day, prices, same):
    saved_receipt(db, "MENY", "2026-10-23", SHOP)
    data = ExtractedReceipt(store_chain=store, purchase_date=day, items=items(*prices))
    assert (worker.find_duplicate(db, data) is not None) is same


def test_a_short_receipt_must_match_exactly(client, db):
    saved_receipt(db, "Kiwi", "2026-10-20", [24.9, 15.0])
    def same(*prices):
        return worker.find_duplicate(db, ExtractedReceipt(store_chain="KIWI 505", purchase_date="2026-10-20", items=items(*prices)))
    assert same(24.9, 15.0) is not None
    assert same(24.9, 16.0) is None


def test_duplicates_saved_before_are_removed_once(client, db):
    saved_receipt(db, "MENY", "2026-10-23", SHOP, "photo.jpg")
    saved_receipt(db, "MENY Sandvika Storsenter", "2026-10-23", SHOP + [6.75], "trumf-001.json")
    saved_receipt(db, "MENY", "2026-10-23", [99.0, 88.0, 77.0], "another-shop.jpg")
    assert worker.repair_duplicates(db) == 1
    assert worker.repair_duplicates(db) == 0
    assert sorted(r.image_filename for r in db.query(models.Receipt)) == ["another-shop.jpg", "photo.jpg"]
    assert db.query(models.ReceiptItem).count() == len(SHOP) + 3  # the copy's lines went with it


def test_known_names_are_the_most_used_on_receipts_and_in_flyers(client, db):
    for day in ("2026-10-01", "2026-10-02"):
        saved_receipt(db, "Kiwi", day, [24.9, 15.0])  # "Vare 0" and "Vare 1" twice each
    flyer = models.Flyer(chain="Meny", valid_from=date(2026, 10, 20), valid_to=date(2026, 10, 26), source="t")
    flyer.offers.extend(models.FlyerOffer(canonical_name=n, pack_size=1, total_price=9) for n in ("Vare 1", "Vare 1", "Ost"))
    db.add(flyer)
    db.commit()
    assert worker.known_product_names(db) == ["Vare 1", "Vare 0", "Ost"]
    db.query(models.FlyerOffer).delete()
    db.query(models.Flyer).delete()
    db.commit()


def test_a_receipt_gets_its_chains_name_and_keeps_the_store(client, db):
    saved_receipt(db, "KIWI 305 Kjørbo", "2026-10-01", [24.9])
    receipt = db.query(models.Receipt).one()
    assert (receipt.store_chain, receipt.store_location) == ("Kiwi", "KIWI 305 Kjørbo")


def test_receipts_saved_before_get_their_chains_name_once(client, db):
    db.add_all([models.Receipt(image_filename="a.jpg", store_chain="MENY"),
                models.Receipt(image_filename="b.json", store_chain="MENY Sandvika Storsenter"),
                models.Receipt(image_filename="c.json", store_chain="Kiwi")])
    db.commit()
    assert worker.repair_chain_names(db) == 2
    assert worker.repair_chain_names(db) == 0
    rows = {r.image_filename: (r.store_chain, r.store_location) for r in db.query(models.Receipt)}
    assert rows == {"a.jpg": ("Meny", "MENY"), "b.json": ("Meny", "MENY Sandvika Storsenter"), "c.json": ("Kiwi", None)}


def test_the_same_total_and_a_price_in_common_is_the_same_purchase(client, db):
    saved_receipt(db, "Kiwi", "2025-08-28", [32.9, 29.9])
    db.query(models.Receipt).one().total = 113.8
    db.commit()
    def found(total, *prices):
        return worker.find_duplicate(db, ExtractedReceipt(store_chain="Nærbutikken Kiwi", purchase_date="2025-08-28",
                                                         total=total, items=items(*prices)))
    assert found(113.8, 32.9, 29.9, 49.0) is not None  # a line more, read differently
    assert found(113.8, 12.0) is None                  # the same total by chance, nothing alike
    assert found(99.0, 32.9, 29.9) is not None         # another total, but the very same lines
    assert found(None, 32.9, 29.9, 49.0) is None       # no total: a short receipt must match exactly


def stale_line(db, raw, name, brand=None, variant=None, price=37.5, quantity=120.0, named_by=None):
    receipt = models.Receipt(image_filename="old.jpg", store_chain="Extra", purchase_date=date(2026, 10, 27))
    db.add(receipt)
    db.flush()
    db.add(models.ReceiptItem(receipt_id=receipt.id, raw_name=raw, canonical_name=name, brand=brand, variant=variant,
                              category="Godteri", quantity=quantity, unit="g", pack_size=1, total_price=price,
                              price_per_unit=round(price / quantity * 100, 2) if quantity else None, named_by=named_by))
    db.commit()


def namer_of(answers, calls=None):
    def name(texts, known=()):
        if calls is not None:
            calls.append((list(texts), list(known)))
        return {i: trumf.LineName(index=i, **answers[t]) for i, t in enumerate(texts) if t in answers}, "{}"
    return name


def test_new_lines_are_stamped_with_the_rules_that_named_them(client, db):
    run(db, lambda path, known=(): receipt().model_dump_json())
    assert {i.named_by for i in db.query(models.ReceiptItem)} == {trumf.NAMING_RULES}


def test_lines_named_by_older_rules_are_named_again_from_the_receipt_text(client, db, caplog):
    caplog.set_level("INFO", logger="sjekka.worker")
    # Named before "PULVERPADDER" was kept, and "S.M." given a borrowed brand; the second twice
    stale_line(db, "PULVERPADDER 120G", "Godteri")
    stale_line(db, "S.M.NAAN BR.GARLIC", "Naan", brand="Smak", variant="Br.Garlic", price=35.9, quantity=None)
    stale_line(db, "S.M.NAAN BR.GARLIC", "Naan", brand="Smak", variant="Br.Garlic", price=34.9, quantity=None,
               named_by="older")
    stale_line(db, "LABAN SEIGMENN 260G", "Seigmenn", brand="Laban", named_by=trumf.NAMING_RULES)
    calls = []
    namer = namer_of({
        "PULVERPADDER 120G": dict(canonical_name="Godteri", variant="Pulverpadder", category="Godteri"),
        "S.M.NAAN BR.GARLIC": dict(canonical_name="Naan", variant="Butter Garlic", category="Bakeri"),
    }, calls)
    assert worker.rename_lines(db, namer) == 3
    # Each text once, and only the stale ones, with the names in use
    texts, known = calls[0]
    assert sorted(texts) == ["PULVERPADDER 120G", "S.M.NAAN BR.GARLIC"]
    assert "Seigmenn" in known
    rows = {(i.raw_name, i.total_price): i for i in db.query(models.ReceiptItem)}
    candy = rows[("PULVERPADDER 120G", 37.5)]
    assert (candy.canonical_name, candy.brand, candy.variant) == ("Godteri", None, "Pulverpadder")
    # Prices and sizes are left as they were
    assert (candy.quantity, candy.price_per_unit) == (120.0, 31.25)
    for price in (35.9, 34.9):
        naan = rows[("S.M.NAAN BR.GARLIC", price)]
        assert (naan.brand, naan.variant, naan.category) == (None, "Butter Garlic", "Bakeri")
    assert {i.named_by for i in db.query(models.ReceiptItem)} == {trumf.NAMING_RULES}
    assert "Renamed 'S.M.NAAN BR.GARLIC': Naan | Smak | Br.Garlic | Godteri -> Naan | None | Butter Garlic | Bakeri" in caplog.text
    # Nothing left: no call
    assert worker.rename_lines(db, lambda *a: pytest.fail("nothing to name")) == 0


def test_a_line_not_named_or_not_a_product_keeps_its_name(client, db):
    stale_line(db, "PANT 3KR", "Pant")
    stale_line(db, "UTYDELIG", "Godteri")
    namer = namer_of({"PANT 3KR": dict(canonical_name="Pant", category="Annet", product=False)})
    assert worker.rename_lines(db, namer) == 2
    assert {(i.canonical_name, i.category, i.named_by) for i in db.query(models.ReceiptItem)} == {
        ("Pant", "Godteri", trumf.NAMING_RULES), ("Godteri", "Godteri", trumf.NAMING_RULES)}


def test_naming_again_goes_a_batch_at_a_time(client, db, monkeypatch):
    monkeypatch.setattr(trumf, "NAMES_PER_CALL", 2)
    for n in range(5):
        stale_line(db, f"VARE {n}", "Godteri")
    namer = namer_of({f"VARE {n}": dict(canonical_name="Godteri", variant=f"Vare {n}", category="Godteri") for n in range(5)})
    assert [worker.rename_lines(db, namer) for _ in range(4)] == [2, 2, 1, 0]
