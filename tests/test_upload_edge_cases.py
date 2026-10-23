import io

from PIL import Image

from app import main
from app.config import MAX_DIMENSION, MAX_SIZE_MB, UPLOAD_DIR


def image_bytes(fmt="JPEG", size=(300, 100), mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, size).save(buf, format=fmt)
    return buf.getvalue()


def upload(client, data, ip="203.0.113.1", headers=None):
    return client.post(
        "/upload",
        files={"file": ("r.jpg", data, "image/jpeg")},
        headers={"CF-Connecting-IP": ip} | (headers or {}),
    )


def saved_image(res):
    return Image.open(UPLOAD_DIR / res.json()["id"])


def test_large_images_are_scaled_down(client):
    res = upload(client, image_bytes(size=(4000, 3000)))
    with saved_image(res) as img:
        assert max(img.size) == MAX_DIMENSION
        assert img.size == (MAX_DIMENSION, 1500)


def test_small_images_are_not_scaled_up(client):
    with saved_image(upload(client, image_bytes(size=(400, 200)))) as img:
        assert img.size == (400, 200)


def test_transparent_png_is_saved_as_jpeg(client):
    res = upload(client, image_bytes("PNG", mode="RGBA"))
    assert res.status_code == 200
    with saved_image(res) as img:
        assert img.format == "JPEG"
        assert img.mode == "RGB"


def test_upload_over_size_limit_is_rejected(client):
    data = b"\xff\xd8" + b"0" * (MAX_SIZE_MB * 1024 * 1024)
    res = upload(client, data)
    assert res.status_code == 400
    assert f"{MAX_SIZE_MB} MB" in res.json()["detail"]


def test_truncated_image_is_rejected(client):
    assert upload(client, image_bytes()[:200]).status_code == 400


def test_missing_file_field(client):
    assert client.post("/upload").status_code == 422


def test_client_ip_prefers_cloudflare_header(client, monkeypatch):
    monkeypatch.setattr(main, "UPLOADS_PER_IP_PER_HOUR", 1)
    data = image_bytes()
    assert upload(client, data, ip="198.51.100.7").status_code == 200
    # Same TCP client (the test client), different Cloudflare IP: allowed
    assert upload(client, data, ip="198.51.100.8").status_code == 200


def test_client_ip_falls_back_to_connection(client, monkeypatch):
    monkeypatch.setattr(main, "UPLOADS_PER_IP_PER_HOUR", 1)
    data = image_bytes()
    files = {"file": ("r.jpg", data, "image/jpeg")}
    assert client.post("/upload", files=files).status_code == 200
    assert client.post("/upload", files=files).status_code == 429


def test_rate_limit_window_expires_after_an_hour(client, monkeypatch):
    monkeypatch.setattr(main, "UPLOADS_PER_IP_PER_HOUR", 1)
    clock = [1000.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    data = image_bytes()
    assert upload(client, data).status_code == 200
    clock[0] += 3599
    assert upload(client, data).status_code == 429
    clock[0] += 2
    assert upload(client, data).status_code == 200


def test_rate_limit_message_is_norwegian(client, monkeypatch):
    monkeypatch.setattr(main, "UPLOADS_PER_IP_PER_HOUR", 0)
    res = upload(client, image_bytes())
    assert res.status_code == 429
    assert "Prøv igjen" in res.json()["detail"]
