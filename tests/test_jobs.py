"""/jobs/{id}: what the thank-you page shows while an upload is read."""
import asyncio

from app import models, worker
from tests.helpers import api_error, image_bytes, upload
from tests.test_worker import receipt


def uploaded(client, ip="203.0.113.1"):
    return upload(client, image_bytes(), ip=ip).json()["id"]


def test_unknown_job_is_404(client):
    assert client.get("/jobs/nope.jpg").status_code == 404


def test_pending_job_reports_its_place_in_the_queue(client):
    first, second = uploaded(client, "203.0.113.1"), uploaded(client, "203.0.113.2")
    assert client.get(f"/jobs/{first}").json() == {"status": "pending", "retrying": False, "queue_position": 1}
    assert client.get(f"/jobs/{second}").json()["queue_position"] == 2


def process(db, filename, extract):
    worker.extract_receipt = extract
    job = db.query(models.Job).filter_by(filename=filename).one()
    asyncio.run(worker.process_job(job, db))


def test_done_job_lists_what_was_found(client, db, monkeypatch):
    monkeypatch.setattr(worker, "extract_receipt", worker.extract_receipt)
    job_id = uploaded(client)
    process(db, job_id, lambda path: receipt().model_dump_json())
    res = client.get(f"/jobs/{job_id}").json()
    assert res["status"] == "done"
    assert res["receipt"]["store_chain"] == "Kiwi"
    assert res["receipt"]["purchase_date"] == "2026-10-20"
    assert {i["canonical_name"] for i in res["receipt"]["items"]} == {"Kjøttdeig", "Seigmenn", "Agurk"}


def test_retrying_job_says_so(client, db, monkeypatch):
    monkeypatch.setattr(worker, "extract_receipt", worker.extract_receipt)
    job_id = uploaded(client)

    def busy(path):
        raise api_error(503)
    process(db, job_id, busy)
    assert client.get(f"/jobs/{job_id}").json()["retrying"] is True


def test_failed_job_doesnt_leak_the_error(client, db, monkeypatch):
    monkeypatch.setattr(worker, "extract_receipt", worker.extract_receipt)
    job_id = uploaded(client)

    def broken(path):
        raise api_error(400)
    process(db, job_id, broken)
    assert client.get(f"/jobs/{job_id}").json() == {"status": "failed", "retrying": False}
