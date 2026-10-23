from datetime import date

from app import models


def add_item(db, name, price, purchase_date, store="Kiwi"):
    receipt = models.Receipt(image_filename="r.jpg", store_chain=store, purchase_date=purchase_date)
    db.add(receipt)
    db.flush()
    db.add(models.ReceiptItem(
        receipt_id=receipt.id, canonical_name=name, category="Meieri",
        quantity=1000, unit="ml", pack_size=1, total_price=price,
    ))
    db.commit()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_stats(client, db):
    add_item(db, "Lettmelk", 24.90, date(2026, 10, 1))
    db.add(models.Job(filename="x.jpg"))
    db.commit()
    assert client.get("/stats").json() == {"total_receipts": 1, "total_items": 1, "pending_jobs": 1}


def test_products_returns_latest_price(client, db):
    add_item(db, "Lettmelk", 22.90, date(2026, 9, 1), store="Rema 1000")
    add_item(db, "Lettmelk", 24.90, date(2026, 10, 1), store="Kiwi")
    products = client.get("/products").json()
    assert len(products) == 1
    assert products[0]["total_price"] == 24.90
    assert products[0]["store_chain"] == "Kiwi"


def test_pages_are_served(client):
    for path in ["/", "/upload.html", "/takk.html", "/style.css"]:
        assert client.get(path).status_code == 200


def test_products_lists_each_name_once_when_bought_same_day(client, db):
    add_item(db, "Lettmelk", 24.90, date(2026, 10, 1), store="Kiwi")
    add_item(db, "Lettmelk", 23.90, date(2026, 10, 1), store="Rema 1000")
    assert len(client.get("/products").json()) == 1


def test_products_empty_database(client):
    assert client.get("/products").json() == []
    assert client.get("/stats").json() == {"total_receipts": 0, "total_items": 0, "pending_jobs": 0}


def test_unknown_paths_are_404(client):
    assert client.get("/nope.html").status_code == 404
    assert client.get("/uploads/nope.jpg").status_code == 404


def test_app_starts_and_stops_the_worker(monkeypatch):
    import asyncio
    from fastapi.testclient import TestClient
    from app import main

    events = []

    async def fake_worker():
        events.append("started")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            events.append("stopped")
            raise

    monkeypatch.setattr(main, "RUN_WORKER", True)
    monkeypatch.setattr(main.worker, "run_worker", fake_worker)
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
        assert events == ["started"]
    assert events == ["started", "stopped"]


def test_app_can_run_without_the_worker(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(main, "RUN_WORKER", False)
    monkeypatch.setattr(main.worker, "run_worker", lambda: (_ for _ in ()).throw(AssertionError("started")))
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
