import hashlib
import re
from pathlib import Path
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


def test_products_say_what_is_sold_by_weight(client, db):
    add_item(db, "Appelsiner", 70.49, date(2026, 10, 23), store="Meny")
    add_item(db, "Lettmelk", 24.9, date(2026, 10, 23))
    oranges, milk = sorted(db.query(models.ReceiptItem), key=lambda i: i.canonical_name)
    oranges.raw_name, oranges.unit, oranges.quantity, oranges.price_per_unit = "Appelsiner kg", "g", 1570, 4.49
    milk.raw_name = "Tine lettmelk 1l"
    db.commit()
    by_weight = {p["canonical_name"]: p["by_weight"] for p in client.get("/products").json()}
    assert by_weight == {"Appelsiner": True, "Lettmelk": False}
    assert client.get("/products/Appelsiner").json()["prices"][0]["by_weight"] is True


def test_products_include_the_variant_and_size(client, db):
    add_item(db, "Pizza", 169.0, date(2026, 10, 23), store="Meny")
    db.query(models.ReceiptItem).one().variant = "Hjemmelaget"
    db.commit()
    product = client.get("/products").json()[0]
    assert (product["variant"], product["quantity"], product["unit"]) == ("Hjemmelaget", 1000, "ml")


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

    def fake(name):
        async def task():
            events.append(f"{name} started")
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                events.append(f"{name} stopped")
                raise
        return task

    monkeypatch.setattr(main, "RUN_WORKER", True)
    monkeypatch.setattr(main.worker, "run_worker", fake("worker"))
    monkeypatch.setattr(main.tjek, "run_refresh", fake("flyers"))
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
        assert events == ["worker started", "flyers started"]
    assert events[2:] == ["worker stopped", "flyers stopped"]

    # FLYER_REFRESH_HOURS=0 leaves the flyers alone
    events.clear()
    monkeypatch.setattr(main, "FLYER_REFRESH_HOURS", 0)
    with TestClient(main.app):
        pass
    assert events == ["worker started", "worker stopped"]


def test_app_can_run_without_the_worker(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(main, "RUN_WORKER", False)
    monkeypatch.setattr(main.worker, "run_worker", lambda: (_ for _ in ()).throw(AssertionError("started")))
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200


def test_product_lists_every_price_oldest_first(client, db):
    add_item(db, "Lettmelk", 24.90, date(2026, 10, 1), store="Kiwi")
    add_item(db, "Lettmelk", 22.90, date(2026, 9, 1), store="Rema 1000")
    add_item(db, "Smør", 39.90, date(2026, 10, 1))
    product = client.get("/products/Lettmelk").json()
    assert product["canonical_name"] == "Lettmelk"
    assert product["category"] == "Meieri"
    assert [(p["store_chain"], p["total_price"]) for p in product["prices"]] == [("Rema 1000", 22.90), ("Kiwi", 24.90)]
    assert product["prices"][0]["purchase_date"] == "2026-09-01"


def test_product_names_with_special_characters(client, db):
    add_item(db, "Smør & ost", 39.90, date(2026, 10, 1))
    assert client.get("/products/Sm%C3%B8r%20%26%20ost").json()["canonical_name"] == "Smør & ost"


def test_unknown_product_is_404(client):
    res = client.get("/products/Finnes%20ikke")
    assert res.status_code == 404
    assert "Fant ikke" in res.json()["detail"]


def test_products_count_the_stores_each_product_was_seen_at(client, db):
    add_item(db, "Lettmelk", 24.90, date(2026, 10, 1), store="Kiwi")
    add_item(db, "Lettmelk", 22.90, date(2026, 9, 1), store="Rema 1000")
    add_item(db, "Lettmelk", 23.90, date(2026, 8, 1), store="Kiwi")
    add_item(db, "Smør", 39.90, date(2026, 10, 1))
    counts = {p["canonical_name"]: p["store_count"] for p in client.get("/products").json()}
    assert counts == {"Lettmelk": 2, "Smør": 1}


def test_fonts_and_logos_are_cached_for_a_week(client):
    for path in ["/fonts/Libron-Regular.woff2", "/chains/kiwi.png", "/favicon.svg", "/og.png"]:
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers["cache-control"] == "public, max-age=604800", path


def test_pages_and_scripts_are_not_long_cached(client):
    for path in ["/", "/style.css", "/common.js", "/health"]:
        assert "max-age" not in client.get(path).headers.get("cache-control", ""), path


def test_pages_link_scripts_and_styles_by_content(client):
    # Cloudflare keeps JS and CSS for hours but not pages. A page from a new
    # deploy once ran against the old common.js and showed no offers at all.
    pages = ["/"] + [f"/{p.name}" for p in Path("static").glob("*.html")]
    for page in pages:
        html = client.get(page).text
        assert not re.search(r'(?:src|href)="/[\w.-]+\.(?:js|css)"', html), f"{page} links an unversioned asset"
        for name, version in re.findall(r'(?:src|href)="/([\w.-]+\.(?:js|css))\?v=(\w+)"', html):
            assert version == hashlib.sha256(Path("static", name).read_bytes()).hexdigest()[:10], (page, name)
            res = client.get(f"/{name}?v={version}")
            assert res.status_code == 200, (page, name)
            assert res.headers["cache-control"] == "public, max-age=31536000, immutable", (page, name)


def test_an_old_script_version_is_not_kept(client):
    assert client.get("/common.js?v=0000000000").headers["cache-control"] == "no-cache"


def test_missing_pages_get_the_404_page(client):
    res = client.get("/finnes-ikke.html")
    assert res.status_code == 404
    assert "Fant ikke siden" in res.text
    assert "max-age" not in res.headers.get("cache-control", "")


def test_pages_have_link_previews(client):
    for path in ["/", "/upload.html", "/produkt.html", "/takk.html"]:
        html = client.get(path).text
        assert 'property="og:image" content="https://sjekka.tischbein.dev/og.png"' in html, path
        assert 'name="twitter:card" content="summary_large_image"' in html, path


PAGES = ["/", "/upload.html", "/takk.html", "/produkt.html", "/personvern.html", "/om.html", "/finnes-ikke.html",
         "/handleliste.html", "/tilbud.html"]


def test_every_page_links_to_the_privacy_and_about_pages(client):
    for path in PAGES:
        footer = client.get(path).text.split("<footer>")[1]
        assert 'href="/personvern.html"' in footer and 'href="/om.html"' in footer, path


def test_the_about_page_has_both_films(client):
    html = client.get("/om.html").text
    for name in ["sjekka-film-desktop", "sjekka-film-mobil"]:
        assert f'src="/video/{name}.mp4"' in html and f'poster="/video/{name}.jpg"' in html
        video = client.get(f"/video/{name}.mp4")
        assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
        assert len(video.content) < 8_000_000, "keep the films small"
        assert client.get(f"/video/{name}.jpg").status_code == 200


def test_pages_load_nothing_from_google(client):
    # The privacy page says visiting sends nothing to Google; only uploads do
    for path in PAGES + ["/style.css"]:
        text = client.get(path).text
        assert "googleapis" not in text and "gstatic" not in text, path


def test_self_hosted_fonts_are_served(client):
    for name in ["Libron-Regular.woff2", "Libron-Bold.woff2"]:
        assert client.get(f"/fonts/{name}").status_code == 200, name
