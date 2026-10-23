"""The real site in a real browser: the app runs in its own process with a
seeded database, and Chromium drives the pages.

Needs `pip install playwright && playwright install chromium`, or set
CHROMIUM_PATH to use an installed Chromium. Skipped when Playwright is
missing, unless E2E_REQUIRED=1 (as in CI)."""
import io
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

if os.getenv("E2E_REQUIRED"):
    from playwright.sync_api import expect, sync_playwright
else:
    playwright = pytest.importorskip("playwright.sync_api")
    expect, sync_playwright = playwright.expect, playwright.sync_playwright

from app import models  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def seed(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as db:
        today = date.today()
        products = [
            # name, brand, category, quantity, unit, pack, price, per unit, store, days ago
            ("Lettmelk", "Tine", "Meieri", 1000, "ml", 1, 24.9, 2.49, "Kiwi", 0),
            ("Lettmelk", "Q", "Meieri", 1000, "ml", 1, 22.9, 2.29, "Rema 1000", 3),
            ("Lettmelk", "Tine", "Meieri", 1000, "ml", 1, 23.9, 2.39, "Kiwi", 20),
            ("Kjøttdeig", "Gilde", "Kjøtt", 400, "g", 1, 89.0, 22.25, "Rema 1000", 1),
            ("Seigmenn", "Laban", "Godteri", 260, "g", 3, 38.7, 4.96, "Meny", 5),
            ("Agurk", None, "Frukt og grønt", None, "stk", 2, 30.0, 15.0, "Kiwi", 40),
            ('<img src=x onerror="window.pwned=1">', None, "Annet", None, None, 1, 10.0, None, "Extra", 0),
        ]
        for name, brand, cat, qty, unit, pack, price, ppu, store, days in products:
            receipt = models.Receipt(image_filename="r.jpg", store_chain=store,
                                     purchase_date=today - timedelta(days=days))
            db.add(receipt)
            db.flush()
            db.add(models.ReceiptItem(
                receipt_id=receipt.id, canonical_name=name, brand=brand, category=cat, quantity=qty,
                unit=unit, pack_size=pack, total_price=price, price_per_unit=ppu,
            ))
        db.commit()
    engine.dispose()


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("site")
    port = free_port()
    env = os.environ | {"DATA_DIR": str(data_dir), "RUN_WORKER": "0", "GEMINI_API_KEY": "test"}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/health")
            break
        except OSError:
            time.sleep(0.1)
    seed(data_dir / "sjekka.db")
    yield url, data_dir
    server.terminate()
    server.wait()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)
        yield b
        b.close()


@pytest.fixture
def page(browser, site):
    context = browser.new_context(base_url=site[0], locale="nb-NO")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(e))
    yield page
    context.close()
    assert errors == [], f"JavaScript errors: {errors}"


def cards(page):
    return page.locator(".card")


def test_explore_page_shows_products_and_stats(page):
    page.goto("/")
    expect(cards(page)).to_have_count(5)
    expect(page.locator("#stat-receipts")).to_have_text("7")
    expect(page.locator("#count")).to_have_text("5 produkter")
    expect(page.locator(".chips button")).to_have_text(
        ["Alle", "Annet", "Frukt og grønt", "Godteri", "Kjøtt", "Meieri"])


def test_prices_use_norwegian_formatting(page):
    page.goto("/")
    milk = cards(page).filter(has_text="Lettmelk")
    expect(milk.locator(".price")).to_have_text("24,90 kr")
    expect(milk.locator(".unit")).to_have_text("2,49 kr / 100 ml")
    expect(milk.locator(".brand")).to_have_text("Tine")
    expect(milk.locator(".where .chain img")).to_have_attribute("alt", "Kiwi")
    expect(milk.locator(".where")).to_contain_text("i dag")


def test_multipacks_show_the_price_of_one(page):
    page.goto("/")
    candy = cards(page).filter(has_text="Seigmenn")
    expect(candy.locator(".price")).to_have_text("12,90 kr")
    cucumber = cards(page).filter(has_text="Agurk")
    expect(cucumber.locator(".unit")).to_have_text("15,00 kr / stk")


def test_dates_read_naturally(page):
    page.goto("/")
    expect(cards(page).filter(has_text="Kjøttdeig").locator(".where")).to_contain_text("i går")
    expect(cards(page).filter(has_text="Seigmenn").locator(".where")).to_contain_text("5 dager siden")
    old = cards(page).filter(has_text="Agurk").locator(".where")
    expect(old).not_to_contain_text("dager siden")  # shown as a date instead


def test_search_matches_name_and_brand(page):
    page.goto("/")
    expect(cards(page)).to_have_count(5)
    page.fill("#search", "tine")
    expect(cards(page)).to_have_count(1)
    expect(cards(page).first).to_contain_text("Lettmelk")
    page.fill("#search", "KJØTT")
    expect(cards(page)).to_have_count(1)
    page.fill("#search", "finnes ikke")
    expect(cards(page)).to_have_count(0)
    expect(page.locator("#notice")).to_have_text("Ingen produkter passer søket.")


def test_category_filter(page):
    page.goto("/")
    page.get_by_role("button", name="Meieri").click()
    expect(cards(page)).to_have_count(1)
    expect(page.get_by_role("button", name="Meieri")).to_have_attribute("aria-pressed", "true")
    page.get_by_role("button", name="Alle").click()
    expect(cards(page)).to_have_count(5)


def test_product_names_are_not_rendered_as_html(page):
    page.goto("/")
    expect(cards(page).filter(has_text="<img src=x")).to_have_count(1)
    assert page.evaluate("window.pwned") is None


def test_no_horizontal_scroll_on_a_phone(browser, site):
    context = browser.new_context(base_url=site[0], viewport={"width": 360, "height": 740})
    page = context.new_page()
    for path in ["/", "/upload.html", "/takk.html", "/produkt.html?navn=Lettmelk"]:
        page.goto(path)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), path
    context.close()


def jpeg_file(tmp_path):
    path = tmp_path / "kvittering.jpg"
    Image.new("RGB", (300, 600), "white").save(path)
    return path


def test_upload_flow(page, site, tmp_path):
    page.goto("/upload.html")
    button = page.locator("#btn")
    expect(button).to_be_disabled()
    # Uploaders are told where the photo goes before they send it
    expect(page.locator("#privacy")).to_contain_text("Google Gemini")

    page.set_input_files("#receipt", jpeg_file(tmp_path))
    expect(page.locator("#drop img")).to_be_visible()
    expect(page.locator("#file-name")).to_have_text("kvittering.jpg")
    expect(button).to_be_enabled()

    saved_before = set((site[1] / "uploads").iterdir())
    button.click()
    page.wait_for_url("**/takk.html")
    expect(page.locator("#queue")).to_contain_text("i kø")
    assert len(set((site[1] / "uploads").iterdir()) - saved_before) == 1

    # The photo comes from this tab, not from the server
    img = page.locator("#img")
    expect(img).to_be_visible()
    assert img.get_attribute("src").startswith("data:image/jpeg;")


def test_thank_you_page_without_a_photo(page):
    page.goto("/takk.html")
    expect(page.locator("h1")).to_have_text("Takk.")
    expect(page.locator("#img")).to_be_hidden()


def test_upload_rejects_non_images_before_sending(page, tmp_path):
    page.goto("/upload.html")
    text = tmp_path / "notes.txt"
    text.write_text("hei")
    page.set_input_files("#receipt", text)
    expect(page.locator("#status")).to_contain_text("må være et bilde")
    expect(page.locator("#btn")).to_be_disabled()


def test_upload_shows_server_errors(page):
    page.goto("/upload.html")
    # Looks like a JPEG to the browser, but isn't one
    page.set_input_files("#receipt", files=[{"name": "r.jpg", "mimeType": "image/jpeg", "buffer": b"nope"}])
    page.locator("#btn").click()
    expect(page.locator("#status")).to_contain_text("Kunne ikke lese bildet")
    expect(page.locator("#btn")).to_be_enabled()


def test_heic_is_accepted_without_preview(page):
    page.goto("/upload.html")
    page.set_input_files("#receipt", files=[{"name": "r.heic", "mimeType": "image/heic", "buffer": b"x"}])
    expect(page.locator("#prompt")).to_have_text("Bilde valgt")
    expect(page.locator("#drop img")).to_have_count(0)
    expect(page.locator("#btn")).to_be_enabled()


def test_drag_and_drop(page, tmp_path):
    page.goto("/upload.html")
    buf = io.BytesIO()
    Image.new("RGB", (50, 50)).save(buf, format="PNG")
    data = list(buf.getvalue())
    page.evaluate("""data => {
        const dt = new DataTransfer();
        dt.items.add(new File([new Uint8Array(data)], "dropped.png", { type: "image/png" }));
        document.getElementById("drop").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true }));
    }""", data)
    expect(page.locator("#file-name")).to_have_text("dropped.png")
    expect(page.locator("#btn")).to_be_enabled()


def test_cards_show_chain_logos_or_colours(page):
    page.goto("/")
    expect(cards(page).filter(has_text="Kjøttdeig").locator(".chain img")).to_have_attribute("alt", "Rema 1000")
    expect(cards(page).filter(has_text="Seigmenn").locator(".chain img")).to_have_attribute("alt", "Meny")
    expect(cards(page).filter(has_text="<img src=x").locator(".chain img")).to_have_attribute("alt", "Extra")
    # Every logo file the badges use actually loads
    for img in page.locator(".chain img").all():
        assert img.evaluate("i => i.complete && i.naturalWidth > 0"), img.get_attribute("src")


def test_card_opens_the_product_page(page):
    page.goto("/")
    milk = cards(page).filter(has_text="Lettmelk")
    expect(milk.locator(".stores")).to_have_text("Sammenlign 2 butikker")
    milk.click()
    page.wait_for_url("**/produkt.html?navn=Lettmelk")
    expect(page.locator("h1")).to_have_text("Lettmelk")


def test_product_page_ranks_stores_cheapest_first(page):
    page.goto("/produkt.html?navn=Lettmelk")
    expect(page.locator("#summary")).to_have_text("3 registrerte priser fra 2 butikker.")
    stores = page.locator(".store")
    expect(stores).to_have_count(2)
    # Rema's latest price (22,90) beats Kiwi's latest (24,90), not Kiwi's older 23,90
    expect(stores.nth(0).locator(".chain img")).to_have_attribute("alt", "Rema 1000")
    expect(stores.nth(0).locator(".amount")).to_contain_text("billigst")
    expect(stores.nth(1).locator(".amount")).to_contain_text("+0,20 kr / 100 ml")
    expect(page.locator("#best-price")).to_have_text("22,90 kr")
    # Kiwi's timeline has both of its prices
    expect(stores.nth(1).locator("circle:not(.hit)")).to_have_count(2)


def test_product_page_tooltip_and_table(page):
    page.goto("/produkt.html?navn=Lettmelk")
    page.locator(".store").nth(1).locator("circle.hit").first.hover()
    expect(page.locator("#tip")).to_contain_text("23,90 kr")
    page.locator(".history summary").click()
    expect(page.locator("#history tr")).to_have_count(3)


def test_unknown_product_page(page):
    page.goto("/produkt.html?navn=Finnes%20ikke")
    expect(page.locator("#notice")).to_have_text("Fant ingen priser for dette produktet.")
