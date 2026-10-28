import re
from pathlib import Path
from datetime import date

from app import main, models


def add_item(db, name, price, purchase_date, store="Kiwi", brand=None, variant=None):
    receipt = models.Receipt(image_filename="r.jpg", store_chain=store, purchase_date=purchase_date)
    db.add(receipt)
    db.flush()
    db.add(models.ReceiptItem(
        receipt_id=receipt.id, canonical_name=name, category="Meieri",
        quantity=1000, unit="ml", pack_size=1, total_price=price, price_per_unit=price / 10, brand=brand, variant=variant,
    ))
    db.commit()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_stats(client, db):
    add_item(db, "Lettmelk", 24.90, date(2026, 10, 1))
    db.add(models.Job(filename="x.jpg"))
    db.commit()
    assert client.get("/stats").json() == {"total_receipts": 1, "total_items": 1, "pending_jobs": 1}


def test_a_card_shows_the_cheapest_choice_not_the_latest_line(client, db):
    add_item(db, "Lettmelk", 22.90, date(2026, 9, 1), store="Rema 1000")
    add_item(db, "Lettmelk", 24.90, date(2026, 10, 1), store="Kiwi")
    product, = client.get("/products").json()
    assert (product["total_price"], product["store_chain"], product["purchase_date"]) == (22.90, "Rema 1000", "2026-09-01")
    assert (product["latest_date"], product["store_count"]) == ("2026-10-01", 2)


def test_a_card_is_not_led_by_a_family_pack(client, db):
    # The latest pizza bought was a 1 kg family pizza; the usual is 570 g
    for price, grams, day, store in [(41.3, 570, 1, "Bunnpris"), (59.9, 570, 2, "Kiwi"), (169.0, 1000, 20, "Meny"),
                                     (62.9, 570, 3, "Meny")]:
        add_item(db, "Pizza", price, date(2026, 10, day), store=store)
        item = db.query(models.ReceiptItem).order_by(models.ReceiptItem.id.desc()).first()
        item.unit, item.quantity, item.price_per_unit = "g", grams, round(price / grams * 100, 2)
    db.commit()
    product, = client.get("/products").json()
    assert (product["total_price"], product["store_chain"]) == (41.3, "Bunnpris")
    # The product page ranks each store by the same choice, at the usual size
    page = client.get("/products/Pizza").json()
    assert page["size"] == "570 g"
    assert {(p["store_chain"], p["total_price"]): p["compared"] for p in page["prices"]} == {
        ("Bunnpris", 41.3): 41.3, ("Kiwi", 59.9): 59.9, ("Meny", 169.0): 96.33, ("Meny", 62.9): None}


def test_items_are_one_card_each_named_as_most_often_spelled(client, db):
    add_item(db, "Godteri", 28.9, date(2026, 4, 18), brand="Ahlgrens", variant="Biler Original")
    add_item(db, "Godteri", 29.9, date(2026, 5, 1), brand="AHLGRENS", variant="Biler Original")
    add_item(db, "Godteri", 30.9, date(2026, 6, 1), brand="Ahlgrens", variant="Biler Original")
    add_item(db, "Godteri", 19.9, date(2026, 10, 1), brand="Laban", variant="Seigmenn")
    add_item(db, "Godteri", 37.5, date(2026, 10, 27), store="Extra")
    items = {(i["brand"], i["variant"]): i for i in client.get("/items").json()}
    assert set(items) == {("Ahlgrens", "Biler Original"), ("Laban", "Seigmenn"), (None, None)}
    biler = items[("Ahlgrens", "Biler Original")]
    assert (biler["canonical_name"], biler["key"], biler["category"]) == ("Godteri", "Godteri|Ahlgrens|Biler Original", "Meieri")
    # Lines without a brand are the product itself, with what the receipt said
    assert items[(None, None)]["key"] == "Godteri"
    assert "raw_name" in items[(None, None)]


def test_an_item_card_has_its_cheapest_price_now_and_its_newest(client, db):
    # Cheapest now at Rema; the newest line is from Kiwi
    add_item(db, "Kyllingfilet", 184.0, date(2026, 2, 17), store="Rema 1000", brand="Kyllinggården")
    add_item(db, "Kyllingfilet", 199.0, date(2026, 10, 28), store="Kiwi", brand="Kyllinggården")
    item, = client.get("/items").json()
    assert (item["total_price"], item["store_chain"], item["purchase_date"]) == (184.0, "Rema 1000", "2026-02-17")
    assert (item["latest"]["total_price"], item["latest"]["store_chain"], item["latest"]["purchase_date"]) == \
        (199.0, "Kiwi", "2026-10-28")
    assert (item["latest_date"], item["store_count"]) == ("2026-10-28", 2)


def test_an_item_bought_only_outside_grocery_chains_shows_its_latest(client, db):
    add_item(db, "Blomster", 99.0, date(2026, 10, 1), store="Mester Grønn", brand="Mester Grønn")
    item, = client.get("/items").json()
    assert (item["total_price"], item["latest"]["total_price"]) == (99.0, 99.0)


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
    assert client.get("/products/Lettmelk").json()["prices"][0]["raw_name"] == "Tine lettmelk 1l"


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


def test_pages_and_scripts_are_not_long_cached(client):
    for path in ["/", "/style.css", "/common.js", "/health"]:
        assert "max-age" not in client.get(path).headers.get("cache-control", ""), path


BARE_REFERENCE = re.compile(rf'["\'(](/[\w./-]+\.(?:{main.ASSET_EXTENSIONS}))["\')]')
VERSIONED_REFERENCE = re.compile(rf'["\'(](/[\w./-]+\.(?:{main.ASSET_EXTENSIONS}))\?v=(\w+)["\')]')


def served_texts(client):
    """Every page, stylesheet and script as a visitor gets it."""
    names = ["/"] + [f"/{p.name}" for p in Path("static").glob("*") if p.suffix in (".html", ".css", ".js")]
    return {name: client.get(name).text for name in names}


def test_everything_a_page_points_at_is_linked_by_content(client):
    # Cloudflare keeps files for hours but not pages. A page from a new deploy
    # once ran against the old common.js and showed no offers at all; a film
    # re-rendered under the same name would show the old one just the same.
    seen = set()
    for name, text in served_texts(client).items():
        for path in BARE_REFERENCE.findall(text):
            assert not main.asset_path(path), f"{name} links {path} without its version"
        for path, version in VERSIONED_REFERENCE.findall(text):
            assert version == main.asset_version(path), (name, path)
            seen.add((path, version))
    kinds = {Path(path).suffix for path, _ in seen}
    assert {".js", ".css", ".woff2", ".svg", ".png", ".mp4", ".vtt", ".jpg"} <= kinds, kinds
    for path, version in sorted(seen):
        # A film is fetched in pieces, and each piece must be kept as well
        res = client.get(f"{path}?v={version}", headers={"Range": "bytes=0-99"} if path.endswith(".mp4") else {})
        assert res.status_code in (200, 206), path
        assert res.headers["cache-control"] == "public, max-age=31536000, immutable", path


def test_a_file_without_its_current_version_is_checked_every_time(client):
    for url in ["/common.js", "/common.js?v=0000000000", "/video/sjekka-slik-bygget.mp4", "/fonts/Libron-Bold.woff2?v=0",
                "/chains/kiwi.png?v=old", "/og.png"]:
        assert client.get(url, headers={"Range": "bytes=0-9"}).headers["cache-control"] == "no-cache", url


def test_only_files_in_static_are_assets(client):
    assert main.asset_path("/common.js") and main.asset_path("/video/sjekka-promo-desktop.mp4")
    for path in ["/index.html", "/nope.png", "/../app/main.py", "/video/../../app/main.py", "/video", "/fonts/OFL.txt"]:
        assert main.asset_path(path) is None, path
    assert "cache-control" not in client.get("/health").headers or "immutable" not in client.get("/health").headers["cache-control"]


def test_a_stylesheet_changes_version_when_a_font_it_points_at_does(monkeypatch, tmp_path):
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "f.woff2").write_bytes(b"first font")
    (tmp_path / "style.css").write_text('@font-face { src: url("/fonts/f.woff2"); }')
    monkeypatch.setattr(main, "STATIC", tmp_path)
    before = main.asset_version("/style.css")
    assert f"/fonts/f.woff2?v={main.asset_version('/fonts/f.woff2')}" in main.link_versioned_assets(
        (tmp_path / "style.css").read_text(), "/style.css")
    (tmp_path / "fonts" / "f.woff2").write_bytes(b"a second, longer font")
    assert main.asset_version("/style.css") != before




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


PAGES = ["/", "/upload.html", "/takk.html", "/produkt.html", "/personvern.html", "/om.html", "/middag.html", "/finnes-ikke.html",
         "/handleliste.html", "/tilbud.html"]


def test_every_page_has_the_logo_icons(client):
    for path in PAGES:
        html = client.get(path).text
        assert re.search(r'rel="icon" type="image/svg\+xml" href="/favicon.svg(\?v=\w+)?"', html), path
        assert re.search(r'rel="apple-touch-icon" href="/apple-touch-icon.png(\?v=\w+)?"', html), path
    # A tab icon can't load the site's font, so the letter is drawn as a shape
    icon = client.get("/favicon.svg").text
    assert "<text" not in icon and "<path" in icon
    assert client.get("/apple-touch-icon.png").headers["content-type"] == "image/png"


def test_every_page_links_to_the_privacy_and_about_pages(client):
    for path in PAGES:
        footer = client.get(path).text.split("<footer>")[1]
        assert 'href="/personvern.html"' in footer and 'href="/om.html"' in footer, path


def test_the_about_page_has_all_the_films(client):
    html = client.get("/om.html").text
    # The phone's promo is swapped in by the page's script, the rest are in the markup
    assert "sjekka-promo-mobil" in html
    for name in ["sjekka-promo-desktop", "sjekka-slik-fungerer", "sjekka-slik-bygget"]:
        assert f'/video/{name}.mp4?v=' in html and f'/video/{name}.jpg?v=' in html
    for name in ["sjekka-promo-desktop", "sjekka-promo-mobil", "sjekka-slik-fungerer", "sjekka-slik-bygget"]:
        video = client.get(f"/video/{name}.mp4")
        assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
        assert len(video.content) < 10_000_000, "keep the films small"
        assert client.get(f"/video/{name}.jpg").status_code == 200
        captions = client.get(f"/video/{name}.vtt")
        assert captions.status_code == 200 and captions.text.startswith("WEBVTT")


def test_uploaders_are_told_google_may_train_on_what_they_send(client):
    # Sjekka is on Gemini's free tier, so the notice and the upload page must say so
    notice, upload = client.get("/personvern.html").text, client.get("/upload.html").text
    assert "gratisnivå" in notice and "trene" in notice and "ansatte kan lese" in notice
    assert "gratisnivå" in upload and "trene" in upload
    assert "bruker det ikke til å forbedre" not in notice


def test_pages_load_nothing_from_google(client):
    # The privacy page says visiting sends nothing to Google; only uploads do
    for path in PAGES + ["/style.css"]:
        text = client.get(path).text
        assert "googleapis" not in text and "gstatic" not in text, path


def test_self_hosted_fonts_are_served(client):
    for name in ["Libron-Regular.woff2", "Libron-Bold.woff2"]:
        assert client.get(f"/fonts/{name}").status_code == 200, name


def add_brus(db):
    day = date(2026, 10, 20)
    add_item(db, "Brus", 29.9, day, "Kiwi", "Coca-Cola", "Zero")
    add_item(db, "Brus", 27.9, day, "Meny", "Coca-Cola", "Zero")
    add_item(db, "Brus", 24.9, day, "Kiwi", "Coca-Cola", "Original")
    add_item(db, "Brus", 19.9, day, "Meny", "Pepsi", "Max")


def test_a_product_lists_its_items_and_one_item_can_be_asked_for(client, db):
    add_brus(db)
    product = client.get("/products/Brus").json()
    assert product["item"] is None and len(product["prices"]) == 4
    assert [(i["brand"], i["variant"], i["cheapest"]["chain"], i["chains"]) for i in product["items"]] == [
        ("Coca-Cola", "Zero", "Meny", 2), ("Coca-Cola", "Original", "Kiwi", 1), ("Pepsi", "Max", "Meny", 1)]
    zero = client.get("/products/Brus", params={"merke": "coca cola", "variant": "zero"}).json()
    # Spelled as the receipts spell it, and only its own prices
    assert zero["item"] == {"brand": "Coca-Cola", "variant": "Zero", "key": "Brus|Coca-Cola|Zero"}
    assert sorted(p["store_chain"] for p in zero["prices"]) == ["Kiwi", "Meny"]
    assert client.get("/products/Brus", params={"merke": "Coca-Cola", "variant": "Creamy Vanilla"}).status_code == 404


def test_the_shopping_list_prices_an_item_or_the_cheapest_of_the_product(client, db):
    add_brus(db)
    data = client.post("/basket", json={"items": [{"name": "Brus"}, {"name": "Brus", "brand": "Coca-Cola", "variant": "Zero"},
                                                 {"name": "Brus", "brand": "Coca-Cola", "variant": "Creamy Vanilla"}]}).json()
    anything, zero = data["items"]
    assert (anything["key"], anything["cheapest"], anything["prices"]["Meny"]["brand"]) == ("Brus", "Meny", "Pepsi")
    assert (zero["key"], zero["name"], zero["brand"], zero["variant"]) == ("Brus|Coca-Cola|Zero", "Brus", "Coca-Cola", "Zero")
    assert {c: p["price"] for c, p in zero["prices"].items()} == {"Kiwi": 29.9, "Meny": 27.9}
    assert data["unknown"] == ["Brus|Coca-Cola|Creamy Vanilla"]
