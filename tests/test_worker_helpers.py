from datetime import date, timedelta

import httpx
import pytest
from pydantic import ValidationError

from app import worker
from app.schema import ExtractedItem
from tests.helpers import api_error


def item(**fields):
    defaults = dict(raw_name="X", canonical_name="X", category="Annet")
    return ExtractedItem(**(defaults | fields))


@pytest.mark.parametrize("raw, expected", [
    ("kjøttdeig", "Kjøttdeig"),
    ("KJØTTDEIG", "Kjøttdeig"),
    ("  kjøtt   deig ", "Kjøtt deig"),
    ("Frukt og Grønt", "Frukt og grønt"),
    ("æbleskiver", "Æbleskiver"),
    ("", ""),
])
def test_normalize_name(raw, expected):
    assert worker.normalize_name(raw) == expected


@pytest.mark.parametrize("fields, expected", [
    (dict(unit="g", quantity=400, total_price=89.0), 22.25),
    (dict(unit="ml", quantity=1000, total_price=24.9), 2.49),
    (dict(unit="g", quantity=260, pack_size=3, total_price=38.7), 4.96),
    (dict(unit="stk", total_price=12.0), 12.0),
    (dict(unit="stk", pack_size=4, total_price=10.0), 2.5),
    (dict(unit="g", quantity=None, total_price=10.0), None),  # size unreadable
    (dict(unit=None, total_price=10.0), None),
    (dict(unit="g", quantity=100, total_price=None), None),
    (dict(unit="stk", pack_size=0, total_price=10.0), 10.0),  # bad pack size counts as one
])
def test_unit_price(fields, expected):
    assert worker.unit_price(item(**fields)) == expected


@pytest.mark.parametrize("raw, quantity, unit, pack, expected", [
    ("Yoghurt kokos 4x150g tine", 150, "g", 1, 600),        # the size of one cup: fixed
    ("Yoghurt kokos 4x150g tine", 600, "g", 1, 600),        # already the whole pack
    ("Farris 6 × 0,5 l", 500, "ml", 1, 3000),
    ("Cola 6x33cl", 330, "ml", 1, 1980),
    ("Pølser 2 X 1kg", 1000, "g", 1, 2000),
    ("Yoghurt 4x150g", 150, "g", 4, 150),                   # bought as four: priced per item already
    ("Yoghurt 4x150g", 150, "ml", 1, 150),                  # units disagree: leave it
    ("Pizza grandiosa hj.laget m/ kjøttdeig 1kg", 1000, "g", 1, 1000),
    ("Hamburgerbrød 1x6stk", 6, "stk", 1, 6),
    ("Tørkerull 30x40cm", None, "stk", 1, None),
    (None, 150, "g", 1, 150),
])
def test_multipack_quantity(raw, quantity, unit, pack, expected):
    assert worker.multipack_quantity(raw, quantity, unit, pack) == expected


@pytest.mark.parametrize("value, expected", [
    ("2026-10-20", date(2026, 10, 20)),
    ("20.10.2026", None),
    ("", None),
    (None, None),
])
def test_parse_date(value, expected):
    assert worker.parse_date(value) == expected


@pytest.mark.parametrize("error, retryable", [
    (api_error(429), True),
    (api_error(500), True),
    (api_error(503), True),
    (api_error(504), True),
    (api_error(400), False),
    (api_error(403), False),
    (api_error(404), False),
    (httpx.ConnectTimeout("timeout"), True),
    (httpx.ReadError("reset"), True),
    (ConnectionError(), True),
    (TimeoutError(), True),
    (ValueError("bad json"), False),
    (FileNotFoundError("gone"), False),
])
def test_is_retryable(error, retryable):
    assert worker.is_retryable(error) is retryable


def test_validation_errors_are_not_retried():
    with pytest.raises(ValidationError) as e:
        ExtractedItem(raw_name="X", canonical_name="X", category="Leker")
    assert not worker.is_retryable(e.value)


def test_retry_delay_doubles_then_caps():
    delays = [worker.retry_delay(n) for n in range(1, 10)]
    assert delays[:4] == [timedelta(seconds=s) for s in (30, 60, 120, 240)]
    assert max(delays) == timedelta(minutes=30)
    assert delays == sorted(delays)


def test_total_retry_window_is_about_an_hour():
    total = sum((worker.retry_delay(n) for n in range(1, worker.MAX_ATTEMPTS)), timedelta())
    assert timedelta(minutes=45) <= total <= timedelta(minutes=90)
