import asyncio
from datetime import timedelta

import pytest

from app import models, worker
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
    job = run(db, lambda path: receipt().model_dump_json())
    assert job.status == "done"
    items = {i.raw_name: i for i in db.query(models.ReceiptItem)}
    mince = items["GILDE KJØTTDEIG 400G"]
    assert mince.canonical_name == "Kjøttdeig"
    assert mince.brand == "Gilde"
    assert mince.price_per_unit == 22.25  # per 100 g
    assert items["LABAN SEIGMENN 260G"].price_per_unit == 4.96  # one bag of three
    assert items["AGURK"].price_per_unit == 15.0  # per stk


def test_duplicate_receipt_is_not_saved_twice(client, db):
    run(db, lambda path: receipt().model_dump_json(), filename="a.jpg")
    job = run(db, lambda path: receipt().model_dump_json(), filename="b.jpg")
    assert job.status == "duplicate"
    assert db.query(models.Receipt).count() == 1


def test_same_store_and_day_with_other_items_is_not_a_duplicate(client, db):
    run(db, lambda path: receipt().model_dump_json(), filename="a.jpg")
    job = run(db, lambda path: receipt(items=receipt().items[:1]).model_dump_json(), filename="b.jpg")
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
    run(db, lambda path: receipt(**missing).model_dump_json(), filename="a.jpg")
    job = run(db, lambda path: receipt(**missing).model_dump_json(), filename="b.jpg")
    assert job.status == "done"


def photo(name):
    from app.config import UPLOAD_DIR
    path = UPLOAD_DIR / name
    path.write_bytes(b"jpeg")
    return path


def test_raw_answer_and_model_are_kept(client, db):
    raw = receipt().model_dump_json()
    job = run(db, lambda path: raw)
    assert job.raw_response == raw
    assert job.model == worker.GEMINI_MODEL


@pytest.mark.parametrize("second", [False, True])
def test_photo_is_deleted_once_read(client, db, second):
    if second:  # a duplicate is also fully handled
        run(db, lambda path: receipt().model_dump_json(), filename="first.jpg")
    path = photo("p.jpg")
    job = run(db, lambda path: receipt().model_dump_json(), filename="p.jpg")
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
    job = run(db, lambda _: raw, filename="p.jpg")
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
