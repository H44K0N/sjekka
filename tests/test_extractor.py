import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import extractor
from app.schema import ExtractedReceipt


def fake_gemini(monkeypatch, payload):
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(payload))

    monkeypatch.setattr(extractor.client.models, "generate_content", generate_content)
    return calls


def test_asks_for_the_schema_and_parses_the_answer(monkeypatch, tmp_path):
    image = tmp_path / "r.jpg"
    image.write_bytes(b"jpeg")
    calls = fake_gemini(monkeypatch, {"store_chain": "Kiwi", "items": [
        {"raw_name": "AGURK", "canonical_name": "Agurk", "category": "Frukt og grønt"},
    ]})
    data = extractor.extract_receipt(image)
    assert data.items[0].canonical_name == "Agurk"
    assert calls[0]["config"].response_schema is ExtractedReceipt
    assert calls[0]["model"] == extractor.GEMINI_MODEL


def test_rejects_categories_outside_the_list(monkeypatch, tmp_path):
    image = tmp_path / "r.jpg"
    image.write_bytes(b"jpeg")
    fake_gemini(monkeypatch, {"items": [{"raw_name": "X", "canonical_name": "X", "category": "Leker"}]})
    with pytest.raises(ValidationError):
        extractor.extract_receipt(image)
