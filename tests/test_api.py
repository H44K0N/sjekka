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
