"""Upload -> job queue -> worker -> API, with only the Gemini call faked."""
import asyncio
from datetime import date

import pytest

from app import models, trumf, worker
from app.config import UPLOAD_DIR
from app.schema import ExtractedItem, ExtractedReceipt, ExtractedReceipts
from tests import helpers


def upload(client, ip="203.0.113.1"):
    res = helpers.upload(client, helpers.image_bytes(size=(400, 800)), ip=ip)
    assert res.status_code == 200
    return res.json()["id"]


def kiwi_receipt(path, known=()):
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

    def extract(path, known=()):
        calls.append(path)
        return kiwi_receipt(path).model_dump_json()

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


def test_receipt_photos_are_never_served(client, db):
    # They can show card digits; the photo is stored but not readable over HTTP.
    filename = upload(client)
    for path in [f"/uploads/{filename}", f"/{filename}", f"/data/uploads/{filename}"]:
        assert client.get(path).status_code == 404


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


def test_worker_loop_repairs_multipacks_when_it_starts(client, db, fake_gemini, monkeypatch):
    monkeypatch.setattr(worker, "POLL_INTERVAL", 0.01)
    repaired = []
    monkeypatch.setattr(worker, "repair_multipacks", lambda session: repaired.append("multipacks") or 0)
    monkeypatch.setattr(worker, "repair_chain_names", lambda session: repaired.append("chains") or 0)
    monkeypatch.setattr(worker, "repair_duplicates", lambda session: repaired.append("duplicates") or 0)
    monkeypatch.setattr(worker, "reread_trumf_histories", lambda session: repaired.append("histories") or 0)
    upload(client)
    run_worker_until(lambda: db.query(models.Job).one().status == "done", db)
    assert repaired == ["multipacks", "chains", "duplicates", "histories"]


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


def test_a_receipt_uploaded_as_csv_goes_all_the_way_through(client, db, monkeypatch):
    sent = []

    def extract(path, known=()):
        sent.append(path.read_text(encoding="utf-8"))
        return ExtractedReceipts(receipts=[kiwi_receipt(path)]).model_dump_json()

    monkeypatch.setattr(worker, "extract_receipts", extract)
    csv = "Vare;Pris\nTINE LETTMELK 1L;24,90\nGILDE KJØTTDEIG 400G;89,00\n".encode("cp1252")
    res = helpers.upload(client, csv, name="kvittering.csv")
    filename = res.json()["id"]
    drain_queue(db)

    assert "KJØTTDEIG" in sent[0]  # Gemini got the text, decoded
    assert client.get(f"/jobs/{filename}").json()["status"] == "done"
    assert {p["canonical_name"] for p in client.get("/products").json()} == {"Lettmelk", "Kjøttdeig"}
    assert not (UPLOAD_DIR / filename).exists()  # deleted once read, like a photo


def stale(db, raw="PULVERPADDER 120G"):
    receipt = models.Receipt(image_filename="old.jpg", store_chain="Extra", purchase_date=date(2026, 10, 27))
    db.add(receipt)
    db.flush()
    db.add(models.ReceiptItem(receipt_id=receipt.id, raw_name=raw, canonical_name="Godteri", category="Godteri",
                              quantity=120, unit="g", pack_size=1, total_price=37.5, price_per_unit=31.25))
    db.commit()


def test_the_idle_worker_reviews_lines_when_the_rules_change(client, db, monkeypatch):
    monkeypatch.setattr(worker, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(trumf, "gemini_review", lambda saved, known=(): (
        {i: trumf.LineReview(index=i, canonical_name="Godteri", variant="Pulverpadder") for i in range(len(saved))}, "{}"))
    stale(db)
    run_worker_until(lambda: db.query(models.ReceiptItem).one().variant == "Pulverpadder", db)


def test_the_worker_backs_off_when_naming_again_fails(client, db, monkeypatch, caplog):
    monkeypatch.setattr(worker, "POLL_INTERVAL", 0.01)
    calls = []

    def quota(saved, known=()):
        calls.append(saved)
        raise RuntimeError("429 quota")
    monkeypatch.setattr(trumf, "gemini_review", quota)
    stale(db)
    # The upload after it is still read: a failed naming holds nothing up
    monkeypatch.setattr(worker, "extract_receipt", lambda path, known=(): kiwi_receipt(path).model_dump_json())
    run_worker_until(lambda: bool(calls), db)
    upload(client)
    run_worker_until(lambda: db.query(models.Job).one().status == "done", db)
    assert "Naming lines again failed" in caplog.text
    assert db.query(models.ReceiptItem).filter_by(raw_name="PULVERPADDER 120G").one().named_by is None
