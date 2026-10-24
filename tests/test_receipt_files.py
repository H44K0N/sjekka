"""Receipts uploaded as PDF, CSV or JSON files instead of photos, including
whole purchase histories split into parts."""
import asyncio
import io
import json
import os
import time

import pytest
from pypdf import PdfReader, PdfWriter

from app import models, receipt_files, worker
from app.config import ORIGINALS_DIR, UPLOAD_DIR
from app.schema import ExtractedItem, ExtractedReceipt, ExtractedReceipts
from tests import helpers

CSV = "Vare;Antall;Pris\nTine Lettmelk 1 l;1;24,90\nGilde Kjøttdeig 400 g;2;89,80\n"
JSON = {"butikk": "KIWI Majorstuen", "dato": "2026-10-23", "varer": [{"navn": "AGURK", "pris": 15.0}]}


def pdf(pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=400)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def history(receipts=300, lines=8):
    """A year of purchases, as a store app might export it."""
    return {"medlem": "1234", "kvitteringer": [
        {"id": f"K{n}", "butikk": "KIWI 505 Storo", "tidspunkt": f"2026-{1 + n % 12:02d}-{1 + n % 28:02d}T17:{n % 60:02d}",
         "varelinjer": [{"tekst": f"VARE {n}-{i}", "antall": 1, "belop": 10 + i} for i in range(lines)]}
        for n in range(receipts)]}


def upload(client, data, name):
    res = helpers.upload(client, data, name=name)
    assert res.status_code == 200, res.text
    return res.json()


def jobs(db, batch):
    return db.query(models.Job).filter_by(batch=batch).order_by(models.Job.filename).all()


# ---- one receipt in a file ----

def test_a_pdf_receipt_is_queued_and_its_original_kept(client, db):
    data = pdf()
    answer = upload(client, data, "ordre-12345.pdf")
    assert answer["parts"] == 1
    job, = jobs(db, answer["batch"])
    assert job.filename == answer["id"] and job.filename.endswith(".pdf")
    assert (UPLOAD_DIR / job.filename).read_bytes() == data
    assert (ORIGINALS_DIR / f"{answer['batch']}.pdf").read_bytes() == data


def test_a_pdf_is_known_by_its_contents_not_its_name(client, db):
    assert upload(client, pdf(), "kvittering")["id"].endswith(".pdf")
    res = helpers.upload(client, b"not a pdf", name="fake.pdf")
    assert res.status_code == 400


def test_a_csv_from_norwegian_excel_is_stored_as_utf8(client, db):
    answer = upload(client, CSV.encode("cp1252"), "kvittering.csv")
    assert (UPLOAD_DIR / answer["id"]).read_text(encoding="utf-8") == CSV  # "Kjøttdeig" survives


def test_a_json_export_is_stored(client, db):
    answer = upload(client, json.dumps(JSON, ensure_ascii=False).encode(), "export.json")
    assert json.loads((UPLOAD_DIR / answer["id"]).read_bytes()) == JSON
    # Without the extension, JSON is still recognised by its first character
    assert upload(client, b'  {"varer": [1]}', "kvittering")["id"].endswith(".json")


@pytest.mark.parametrize("name, data, message", [
    ("broken.pdf", b"%PDF-1.4\n<< /Type /Page >>", "ødelagt"),
    ("one-line.csv", b"Vare;Pris\n", "minst en overskrift"),
    ("binary.csv", b"\x00\x01\x02\x03" * 10, "tekst"),
    ("bad.json", b"{not json", "gyldig JSON"),
    ("empty.json", b"{}", "tom"),
    ("huge.json", b"[" + b"1," * 6_000_000 + b"1]", "for stor"),
])
def test_unusable_files_are_turned_away_with_a_reason(client, db, name, data, message):
    res = helpers.upload(client, data, name=name)
    assert res.status_code == 400 and message in res.json()["detail"]
    assert db.query(models.Job).count() == 0


def test_files_count_towards_the_upload_limit(client, monkeypatch):
    monkeypatch.setattr("app.main.UPLOADS_PER_IP_PER_HOUR", 1)
    assert helpers.upload(client, pdf(), name="a.pdf").status_code == 200
    assert helpers.upload(client, pdf(), name="b.pdf").status_code == 429


def test_photos_still_go_the_photo_way():
    assert receipt_files.kind_of("receipt.jpg", helpers.image_bytes()) is None
    assert receipt_files.kind_of("receipt.png", helpers.image_bytes("PNG")) is None


# ---- a whole purchase history ----

def test_a_json_history_is_split_between_purchases():
    doc = history()
    parts = [json.loads(p) for p in receipt_files.split("json", json.dumps(doc).encode())]
    assert len(parts) > 1
    assert all(len(json.dumps(p)) <= receipt_files.PART_BYTES + 2000 for p in parts)
    # Every purchase exactly once and whole, with the member info kept on each part
    ids = [r["id"] for p in parts for r in p["kvitteringer"]]
    assert ids == [r["id"] for r in doc["kvitteringer"]]
    assert all(p["medlem"] == "1234" and all(len(r["varelinjer"]) == 8 for r in p["kvitteringer"]) for p in parts)


def test_a_csv_history_is_split_with_overlap_and_the_header_on_every_part():
    rows = [f"2026-01-{1 + n // 40:02d};KIWI;VARE {n};{10 + n % 7},90" for n in range(3000)]
    text = "Dato;Butikk;Vare;Pris\n" + "\n".join(rows) + "\n"
    parts = [p.decode() for p in receipt_files.split("csv", text.encode())]
    assert len(parts) > 1 and all(p.startswith("Dato;Butikk;Vare;Pris\n") for p in parts)
    assert {line for p in parts for line in p.splitlines()[1:]} == set(rows)  # nothing lost
    first, second = parts[0].splitlines()[1:], parts[1].splitlines()[1:]
    # A receipt cut at the border is whole in one of the two
    assert first[-receipt_files.OVERLAP_ROWS:] == second[:receipt_files.OVERLAP_ROWS]


def test_a_long_pdf_is_split_into_overlapping_page_runs():
    parts = receipt_files.split("pdf", pdf(10))
    assert [len(PdfReader(io.BytesIO(p)).pages) for p in parts] == [4, 4, 4]  # pages 1-4, 4-7, 7-10
    assert len(receipt_files.split("pdf", pdf(3))) == 1


def test_a_history_too_big_for_one_go_is_turned_away():
    big = history(receipts=5000, lines=12)  # about 3.5 MB: over 100 parts
    with pytest.raises(receipt_files.ReceiptFileError, match="Del den opp"):
        receipt_files.split("json", json.dumps(big).encode())


def test_all_parts_must_fit_in_the_daily_limit(client, db, monkeypatch):
    monkeypatch.setattr("app.main.UPLOADS_PER_DAY", 2)  # the history is about five parts
    before = set(ORIGINALS_DIR.iterdir())
    res = helpers.upload(client, json.dumps(history()).encode(), name="trumf.json")
    assert res.status_code == 429 and "dagens grense" in res.json()["detail"]
    assert db.query(models.Job).count() == 0 and set(ORIGINALS_DIR.iterdir()) == before


def test_a_history_upload_queues_one_job_per_part(client, db):
    answer = upload(client, json.dumps(history()).encode(), "trumf.json")
    queued = jobs(db, answer["batch"])
    assert len(queued) == answer["parts"] > 1 and queued[0].filename == answer["id"]
    assert [j.filename for j in queued] == [f"{answer['batch']}-{i:03d}.json" for i in range(1, len(queued) + 1)]


def receipt(day, store="Kiwi", item="Lettmelk", price=24.9):
    return ExtractedReceipt(store_chain=store, purchase_date=f"2026-10-{day:02d}",
                            items=[ExtractedItem(raw_name=item.upper(), canonical_name=item, category="Meieri", total_price=price)])


def test_every_receipt_in_a_part_is_saved_and_repeats_are_dropped(client, db, monkeypatch):
    answer = upload(client, pdf(10), "trumf-innsyn.pdf")
    # The parts overlap, so the receipt on page 4 comes back in both of the first two
    answers = iter([
        [receipt(1), receipt(2), receipt(3)],
        [receipt(3), receipt(4)],
        [receipt(5, "Meny"), ExtractedReceipt(items=[])],  # an empty entry is skipped
    ])
    monkeypatch.setattr(worker, "extract_receipts", lambda path: ExtractedReceipts(receipts=next(answers)).model_dump_json())
    while job := worker.next_job(db):
        asyncio.run(worker.process_job(job, db))

    assert db.query(models.Receipt).count() == 5
    batch = client.get(f"/jobs/batch/{answer['batch']}").json()
    assert batch == {"parts": 3, "read": 3, "failed": 0, "waiting": 0, "receipts": 5, "duplicates": 1, "items": 5,
                     "chains": {"Kiwi": 4, "Meny": 1}, "from": "2026-10-01", "to": "2026-10-05"}
    # The parts are deleted once read; the original is kept
    assert not any((UPLOAD_DIR / j.filename).exists() for j in jobs(db, answer["batch"]))
    assert (ORIGINALS_DIR / f"{answer['batch']}.pdf").exists()


def test_an_unknown_batch_is_not_found(client):
    assert client.get("/jobs/batch/nope").status_code == 404


def test_kept_files_are_deleted_after_the_retention_period(monkeypatch):
    old, new = ORIGINALS_DIR / "old.json", ORIGINALS_DIR / "new.json"
    old.write_text("{}")
    new.write_text("{}")
    year_ago = time.time() - 366 * 86400
    os.utime(old, (year_ago, year_ago))
    monkeypatch.setattr(worker, "KEEP_FILES_DAYS", 365)
    assert worker.delete_old_originals() == 1
    assert not old.exists() and new.exists()
    new.unlink()
