import asyncio
from datetime import datetime, timedelta

import pytest
from google.genai import errors

from app import models, worker
from app.schema import ExtractedItem, ExtractedReceipt


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


def api_error(code):
    return errors.APIError(code, {"error": {"code": code, "message": "nope", "status": "X"}})


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
    job = run(db, lambda path: receipt())
    assert job.status == "done"
    items = {i.raw_name: i for i in db.query(models.ReceiptItem)}
    mince = items["GILDE KJØTTDEIG 400G"]
    assert mince.canonical_name == "Kjøttdeig"
    assert mince.brand == "Gilde"
    assert mince.price_per_unit == 22.25  # per 100 g
    assert items["LABAN SEIGMENN 260G"].price_per_unit == 4.96  # one bag of three
    assert items["AGURK"].price_per_unit == 15.0  # per stk


def test_duplicate_receipt_is_not_saved_twice(client, db):
    run(db, lambda path: receipt(), filename="a.jpg")
    job = run(db, lambda path: receipt(), filename="b.jpg")
    assert job.status == "duplicate"
    assert db.query(models.Receipt).count() == 1


def test_same_store_and_day_with_other_items_is_not_a_duplicate(client, db):
    run(db, lambda path: receipt(), filename="a.jpg")
    job = run(db, lambda path: receipt(items=receipt().items[:1]), filename="b.jpg")
    assert job.status == "done"
    assert db.query(models.Receipt).count() == 2


@pytest.mark.parametrize("code", [429, 503])
def test_rate_limits_and_overload_are_retried(client, db, code):
    def extract(path):
        raise api_error(code)
    job = run(db, extract)
    assert job.status == "pending"
    assert job.attempts == 1


def test_gives_up_after_max_attempts(client, db):
    def extract(path):
        raise api_error(503)
    job = models.Job(filename="a.jpg", attempts=worker.MAX_ATTEMPTS - 1)
    db.add(job)
    db.commit()
    worker.extract_receipt = extract
    asyncio.run(worker.process_job(job, db))
    assert job.status == "failed"


def test_client_errors_fail_immediately(client, db):
    def extract(path):
        raise api_error(400)
    job = run(db, extract)
    assert job.status == "failed"
    assert job.attempts == 1


def test_waiting_job_does_not_block_the_queue(client, db):
    now = datetime.utcnow()
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
    run(db, lambda path: receipt(**missing), filename="a.jpg")
    job = run(db, lambda path: receipt(**missing), filename="b.jpg")
    assert job.status == "done"
