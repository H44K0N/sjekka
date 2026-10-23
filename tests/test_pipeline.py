"""Upload -> job queue -> worker -> API, with only the Gemini call faked."""
import asyncio
import io
from datetime import date

import pytest
from PIL import Image

from app import models, worker
from app.schema import ExtractedItem, ExtractedReceipt


def jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (400, 800), "white").save(buf, format="JPEG")
    return buf.getvalue()


def upload(client, ip="203.0.113.1"):
    res = client.post("/upload", files={"file": ("r.jpg", jpeg(), "image/jpeg")}, headers={"CF-Connecting-IP": ip})
    assert res.status_code == 200
    return res.json()["id"]


def kiwi_receipt(path):
    return ExtractedReceipt(
        store_chain="Kiwi", purchase_date=date.today().isoformat(), total=113.9,
        items=[
            ExtractedItem(raw_name="TINE LETTMELK 1L", canonical_name="LETTMELK", brand="Tine",
                          category="Meieri", quantity=1000, unit="ml", total_price=24.9),
            ExtractedItem(raw_name="GILDE KJØTTDEIG 400G", canonical_name="Kjøttdeig", brand="Gilde",
                          category="Kjøtt", quantity=400, unit="g", total_price=89.0),
        ],
    )


@pytest.fixture
def fake_gemini(monkeypatch):
    calls = []

    def extract(path):
        calls.append(path)
        return kiwi_receipt(path)

    monkeypatch.setattr(worker, "extract_receipt", extract)
    return calls


def drain_queue(db):
    """Run the worker until no job is ready."""
    while job := worker.next_job(db):
        asyncio.run(worker.process_job(job, db))


def test_uploaded_receipt_shows_up_on_the_explore_page(client, db, fake_gemini):
    filename = upload(client)
    assert client.get("/stats").json()["pending_jobs"] == 1

    drain_queue(db)

    assert fake_gemini[0].name == filename
    assert client.get("/stats").json() == {"total_receipts": 1, "total_items": 2, "pending_jobs": 0}
    products = {p["canonical_name"]: p for p in client.get("/products").json()}
    assert set(products) == {"Lettmelk", "Kjøttdeig"}
    assert products["Lettmelk"]["brand"] == "Tine"
    assert products["Lettmelk"]["store_chain"] == "Kiwi"
    assert products["Lettmelk"]["price_per_unit"] == 2.49
    assert products["Kjøttdeig"]["price_per_unit"] == 22.25


def test_same_receipt_uploaded_twice_counts_once(client, db, fake_gemini):
    upload(client, ip="203.0.113.1")
    upload(client, ip="203.0.113.2")
    drain_queue(db)

    assert client.get("/stats").json()["total_receipts"] == 1
    statuses = sorted(j.status for j in db.query(models.Job))
    assert statuses == ["done", "duplicate"]


def test_receipt_photo_is_served_for_the_thank_you_page(client, db):
    filename = upload(client)
    res = client.get(f"/uploads/{filename}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"


def test_missing_photo_fails_the_job_without_calling_gemini(client, db, monkeypatch):
    # The real extractor, so the file read happens before any API call
    from app.extractor import extract_receipt
    monkeypatch.setattr(worker, "extract_receipt", extract_receipt)
    db.add(models.Job(filename="gone.jpg"))
    db.commit()

    drain_queue(db)

    job = db.query(models.Job).one()
    assert job.status == "failed"
    assert "gone.jpg" in job.error


def run_worker_until(predicate, db, timeout=5):
    async def main():
        task = asyncio.create_task(worker.run_worker())
        try:
            async with asyncio.timeout(timeout):
                while True:
                    await asyncio.sleep(0.01)
                    db.expire_all()
                    if predicate():
                        return
        finally:
            task.cancel()
    asyncio.run(main())


def test_worker_loop_resets_stuck_jobs_and_processes_them(client, db, fake_gemini, monkeypatch):
    monkeypatch.setattr(worker, "POLL_INTERVAL", 0.01)
    filename = upload(client)
    job = db.query(models.Job).one()
    job.status = "processing"  # as if the app crashed mid-job
    db.commit()

    run_worker_until(lambda: db.query(models.Job).one().status == "done", db)

    assert fake_gemini[0].name == filename


def test_worker_loop_survives_unexpected_errors(client, db, fake_gemini, monkeypatch):
    monkeypatch.setattr(worker, "POLL_INTERVAL", 0.01)
    real_next_job = worker.next_job
    failures = iter([True])

    def flaky_next_job(session):
        if next(failures, False):
            raise RuntimeError("database is locked")
        return real_next_job(session)

    monkeypatch.setattr(worker, "next_job", flaky_next_job)
    upload(client)

    run_worker_until(lambda: db.query(models.Job).one().status == "done", db)
