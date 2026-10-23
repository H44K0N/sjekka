import io
import json

from app import models
from util import export_extractions


def test_exports_every_job_with_its_raw_answer(db):
    db.query(models.Job).delete()
    db.add_all([
        models.Job(filename="a.jpg", status="done", model="gemini-x", raw_response='{"store_chain": "Kiwi", "items": []}'),
        models.Job(filename="b.jpg", status="failed", model="gemini-x", raw_response="not json", error="invalid"),
        models.Job(filename="c.jpg", status="pending"),
    ])
    db.commit()

    out = io.StringIO()
    export_extractions.main(out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]

    assert [r["filename"] for r in lines] == ["a.jpg", "b.jpg", "c.jpg"]
    assert lines[0]["answer"] == {"store_chain": "Kiwi", "items": []}
    assert lines[0]["model"] == "gemini-x"
    assert lines[1]["answer"] == "not json"
    assert lines[2]["answer"] is None
