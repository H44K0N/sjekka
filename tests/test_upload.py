import io

import pytest
from PIL import Image

from app import main, models
from app.config import UPLOAD_DIR


def image_bytes(fmt="JPEG", size=(300, 100), exif=None):
    buf = io.BytesIO()
    img = Image.new("RGB", size, "white")
    if exif is not None:
        img.save(buf, format=fmt, exif=exif)
    else:
        img.save(buf, format=fmt)
    return buf.getvalue()


def upload(client, data, name="receipt.jpg", ip="203.0.113.1"):
    return client.post(
        "/upload",
        files={"file": (name, data, "application/octet-stream")},
        headers={"CF-Connecting-IP": ip},
    )


def test_upload_creates_job(client, db):
    res = upload(client, image_bytes())
    assert res.status_code == 200
    filename = res.json()["id"]
    assert (UPLOAD_DIR / filename).exists()
    assert db.query(models.Job).filter_by(filename=filename, status="pending").count() == 1


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_upload_accepts_other_formats(client, fmt):
    assert upload(client, image_bytes(fmt)).status_code == 200


def test_upload_accepts_heic(client):
    pillow_heif = pytest.importorskip("pillow_heif")
    buf = io.BytesIO()
    try:
        pillow_heif.from_pillow(Image.new("RGB", (64, 64), "white")).save(buf, quality=50)
    except Exception as e:  # wheels without an HEVC encoder
        pytest.skip(f"can't encode HEIC here: {e}")
    assert upload(client, buf.getvalue(), name="receipt.heic").status_code == 200


def test_upload_applies_exif_rotation(client):
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90° clockwise
    res = upload(client, image_bytes(size=(300, 100), exif=exif))
    assert res.status_code == 200
    with Image.open(UPLOAD_DIR / res.json()["id"]) as saved:
        assert saved.size == (100, 300)


def test_upload_rejects_non_image(client, db):
    res = upload(client, b"not an image", name="receipt.jpg")
    assert res.status_code == 400
    assert db.query(models.Job).count() == 0


def test_upload_rejects_unsupported_format(client):
    assert upload(client, image_bytes("GIF"), name="receipt.gif").status_code == 400


def test_upload_limit_per_ip(client, monkeypatch):
    monkeypatch.setattr(main, "UPLOADS_PER_IP_PER_HOUR", 2)
    data = image_bytes()
    assert upload(client, data).status_code == 200
    assert upload(client, data).status_code == 200
    assert upload(client, data).status_code == 429
    # Another client still gets through
    assert upload(client, data, ip="203.0.113.2").status_code == 200


def test_failed_uploads_dont_count_towards_limit(client, monkeypatch):
    monkeypatch.setattr(main, "UPLOADS_PER_IP_PER_HOUR", 1)
    assert upload(client, b"junk").status_code == 400
    assert upload(client, image_bytes()).status_code == 200


def test_daily_upload_cap(client, monkeypatch):
    monkeypatch.setattr(main, "UPLOADS_PER_DAY", 2)
    data = image_bytes()
    assert upload(client, data, ip="203.0.113.1").status_code == 200
    assert upload(client, data, ip="203.0.113.2").status_code == 200
    assert upload(client, data, ip="203.0.113.3").status_code == 429
