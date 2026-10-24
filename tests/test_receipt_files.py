"""Receipts uploaded as PDF, CSV or JSON files instead of photos."""
import json

import pytest

from app import models, receipt_files
from app.config import UPLOAD_DIR
from tests import helpers

PDF = b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n%%EOF\n"
CSV = "Vare;Antall;Pris\nTine Lettmelk 1 l;1;24,90\nGilde Kjøttdeig 400 g;2;89,80\n"
JSON = {"butikk": "KIWI Majorstuen", "dato": "2026-10-23", "varer": [{"navn": "AGURK", "pris": 15.0}]}


def stored(client, db, data, name):
    res = helpers.upload(client, data, name=name)
    assert res.status_code == 200, res.text
    job = db.query(models.Job).filter_by(filename=res.json()["id"]).one()
    return job.filename, (UPLOAD_DIR / job.filename).read_bytes()


def test_a_pdf_receipt_is_queued_as_it_is(client, db):
    filename, data = stored(client, db, PDF, "ordre-12345.pdf")
    assert filename.endswith(".pdf") and data == PDF


def test_a_pdf_is_known_by_its_contents_not_its_name(client, db):
    filename, _ = stored(client, db, PDF, "kvittering")
    assert filename.endswith(".pdf")
    res = helpers.upload(client, b"not a pdf", name="fake.pdf")
    assert res.status_code == 400


def test_a_csv_from_norwegian_excel_is_stored_as_utf8(client, db):
    filename, data = stored(client, db, CSV.encode("cp1252"), "kvittering.csv")
    assert filename.endswith(".csv")
    assert data.decode("utf-8") == CSV  # "Kjøttdeig" survives the re-encoding


def test_a_json_export_is_stored(client, db):
    filename, data = stored(client, db, json.dumps(JSON, ensure_ascii=False).encode(), "export.json")
    assert filename.endswith(".json") and json.loads(data) == JSON
    # Without the extension, JSON is still recognised by its first character
    filename, _ = stored(client, db, b'  {"varer": [1]}', "kvittering")
    assert filename.endswith(".json")


@pytest.mark.parametrize("name, data, message", [
    ("broken.pdf", b"%PDF-1.4\n<< /Type /Page >>", "ødelagt"),
    ("big.pdf", b"%PDF-1.4\n" + b"<< /Type /Page >>\n" * 21 + b"%%EOF", "for mange sider"),
    ("one-line.csv", b"Vare;Pris\n", "minst en overskrift"),
    ("binary.csv", b"\x00\x01\x02\x03" * 10, "tekst"),
    ("bad.json", b"{not json", "gyldig JSON"),
    ("empty.json", b"{}", "tom"),
    ("huge.json", b"[" + b"1," * 600_000 + b"1]", "for stor"),
])
def test_unusable_files_are_turned_away_with_a_reason(client, db, name, data, message):
    res = helpers.upload(client, data, name=name)
    assert res.status_code == 400 and message in res.json()["detail"]
    assert db.query(models.Job).count() == 0


def test_files_count_towards_the_upload_limit(client, monkeypatch):
    monkeypatch.setattr("app.main.UPLOADS_PER_IP_PER_HOUR", 1)
    assert helpers.upload(client, PDF, name="a.pdf").status_code == 200
    assert helpers.upload(client, PDF, name="b.pdf").status_code == 429


def test_photos_still_go_the_photo_way():
    assert receipt_files.kind_of("receipt.jpg", helpers.image_bytes()) is None
    assert receipt_files.kind_of("receipt.png", helpers.image_bytes("PNG")) is None
