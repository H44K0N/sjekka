"""Shared test helpers (fixtures live in conftest.py)."""
import io

from google.genai import errors
from PIL import Image


def image_bytes(fmt="JPEG", size=(300, 100), mode="RGB", exif=None):
    buf = io.BytesIO()
    extra = {"exif": exif} if exif is not None else {}
    Image.new(mode, size, "white").save(buf, format=fmt, **extra)
    return buf.getvalue()


def upload(client, data, name="receipt.jpg", ip="203.0.113.1", headers=None):
    # The server decides the format from the file itself, so the declared
    # content type is deliberately generic.
    return client.post(
        "/upload",
        files={"file": (name, data, "application/octet-stream")},
        headers={"CF-Connecting-IP": ip} | (headers or {}),
    )


def api_error(code):
    return errors.APIError(code, {"error": {"code": code, "message": "x", "status": "X"}})
