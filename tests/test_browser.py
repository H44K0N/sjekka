"""The real site in a real browser: the app runs in its own process with a
seeded database, and Chromium drives the pages.

Needs `pip install playwright && playwright install chromium`, or set
CHROMIUM_PATH to use an installed Chromium. Skipped when Playwright is
missing, unless E2E_REQUIRED=1 (as in CI)."""
import io
import re
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
                raw_name="Q LETTMELK 1L" if brand == "Q" else None,
            ))
        # This week's flyers: a real discount on Seigmenn (12,90 before), and
        # an offer for a product no receipt has, with the flyer's own claim
        models.Base.metadata.create_all(engine)
        week = (today - timedelta(days=2), today + timedelta(days=4))
        meny = models.Flyer(chain="Meny", valid_from=week[0], valid_to=week[1], source="test")
        meny.offers.append(models.FlyerOffer(canonical_name="Seigmenn", brand="Laban", quantity=260, unit="g", pack_size=1,
                                             total_price=9.9, price_per_unit=3.81))
        rema = models.Flyer(chain="Rema 1000", valid_from=week[0], valid_to=week[1], source="test")
        rema.offers.append(models.FlyerOffer(canonical_name="Safranboller", unit="stk", pack_size=1, total_price=25,
                                             price_per_unit=25, pre_price=40, raw_name="SAFRANBOLLER 6 stk"))
        db.add_all([meny, rema])
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


AXE = Path(__file__).parent / "vendor" / "axe.min.js"  # axe-core, pinned (MPL-2.0)


@pytest.mark.parametrize("path", ["/", "/upload.html", "/takk.html", "/produkt.html?navn=Lettmelk", "/finnes-ikke.html", "/personvern.html",
                                  "/om.html", "/handleliste.html", "/handleliste.html?v=Lettmelk~2&v=Kj%C3%B8ttdeig&v=Safran",
                                  "/tilbud.html", "/middag.html", "/produkt.html?navn=Seigmenn"])
def test_pages_pass_automated_accessibility_checks(page, path):
    page.goto(path)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(900)  # let the entrance animation finish, or contrast is measured mid-fade
    page.add_script_tag(path=str(AXE))
    result = page.evaluate("axe.run(document, { runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa', 'best-practice'] })")
    problems = [
        f"{v['impact']}: {v['id']} – {v['help']} ({len(v['nodes'])}× e.g. {v['nodes'][0]['target']})"
        for v in result["violations"]
    ]
    assert problems == [], "\n".join(problems)


def test_explore_page_shows_products_and_stats(page):
    page.goto("/")
    expect(cards(page)).to_have_count(5)
    expect(page.locator("#stat-receipts")).to_have_text("7")
    expect(page.locator("#count")).to_have_text("5 produkter")
    expect(page.locator(".chips button")).to_have_text(
        ["Alle", "Annet", "Frukt og grønt", "Godteri", "Kjøtt", "Meieri"])


def test_prices_use_norwegian_formatting(page):
    page.goto("/")
    # The cheapest milk now, though the latest bought was Tine at Kiwi
    milk = cards(page).filter(has_text="Lettmelk")
    expect(milk.locator(".price")).to_have_text("22,90 kr")
    expect(milk.locator(".unit")).to_have_text("2,29 kr / 100 ml")
    expect(milk.locator(".brand")).to_have_text("Q · 1 l")
    expect(milk.locator(".where .chain img")).to_have_attribute("alt", "Rema 1000")
    expect(milk.locator(".where")).to_contain_text("3 dager siden")


def test_cards_say_how_big_the_package_is(page):
    page.goto("/")
    expect(cards(page).filter(has_text="Kjøttdeig").locator(".brand")).to_have_text("Gilde · 400 g")
    # No size to show: just the brand, or nothing
    expect(cards(page).filter(has_text="Agurk").locator(".brand")).to_have_count(0)
    described = page.evaluate("""() => [
        describe({brand: "Grandiosa", variant: "Hjemmelaget", quantity: 1000, unit: "g"}),
        describe({brand: "Tine", quantity: 1500, unit: "ml"}),
        describe({brand: null, variant: "Løsvekt", quantity: 1570, unit: "g"}),
        describe({brand: "Farris", variant: "Lime 0,5 l", quantity: 500, unit: "ml"}),
        describe({brand: "Prior", quantity: 12, unit: "stk"}),
        describe({brand: "Prior", variant: "6PK", quantity: 6, unit: "stk"}),
        describe({brand: "Gilde", unit: "stk"}),
    ]""")
    # A pack counted in pieces says how many, once
    assert described == ["Grandiosa · Hjemmelaget · 1 kg", "Tine · 1,5 l", "Løsvekt · 1,57 kg", "Farris · Lime 0,5 l",
                         "Prior · 12 stk", "Prior · 6PK", "Gilde"]
    # and what one piece costs
    assert page.evaluate("prices({unit: 'stk', quantity: 12, total_price: 56.4, pack_size: 1})") == \
        {"each": "56,40 kr", "unit": "4,70 kr / stk"}


def test_product_page_says_the_size_of_each_price(page):
    page.goto("/produkt.html?navn=Lettmelk")
    expect(page.locator(".store").nth(0).locator(".brand")).to_have_text("Q · 1 l")
    # The receipt's own words, so a misread name can be seen
    expect(page.locator(".store").nth(0).locator(".raw")).to_have_text("«Q LETTMELK 1L»")
    page.locator(".history summary").click()
    expect(page.locator("#history tr").first).to_contain_text("Tine · 1 l")
    expect(page.locator("#history tr", has_text="Q · 1 l").locator(".raw")).to_have_text("«Q LETTMELK 1L»")


def test_multipacks_show_the_price_of_one(page):
    page.goto("/")
    candy = cards(page).filter(has_text="Seigmenn")
    expect(candy.locator(".price")).to_have_text("12,90 kr")
    cucumber = cards(page).filter(has_text="Agurk")
    expect(cucumber.locator(".unit")).to_have_text("15 kr / stk")


def test_dates_read_naturally(page):
    page.goto("/")
    expect(cards(page).filter(has_text="Kjøttdeig").locator(".where")).to_contain_text("i går")
    expect(cards(page).filter(has_text="Seigmenn").locator(".where")).to_contain_text("5 dager siden")
    old = cards(page).filter(has_text="Agurk").locator(".where")
    expect(old).not_to_contain_text("dager siden")  # shown as a date instead


def test_search_matches_name_and_brand(page):
    page.goto("/")
    expect(cards(page)).to_have_count(5)
    # Any brand of the product, not just the one on the card
    page.fill("#search", "tine")
    expect(cards(page)).to_have_count(1)
    expect(cards(page).first).to_contain_text("Lettmelk")
    expect(cards(page).first.locator(".brand")).to_contain_text("Q")
    page.fill("#search", "KJØTT")
    expect(cards(page)).to_have_count(1)
    page.fill("#search", "finnes ikke")
    expect(cards(page)).to_have_count(0)
    expect(page.locator("#notice")).to_have_text("Ingen kvitteringer har det du søker etter ennå.")
    expect(page.locator("#offer-hits")).to_be_hidden()


def open_categories(page):
    page.locator("#category-toggle").click()
    expect(page.locator("#categories")).to_be_visible()


def test_the_categories_are_folded_away_until_asked_for(page):
    page.goto("/")
    expect(cards(page).first).to_be_visible()
    # A row of them takes a lot of a phone's screen, so only the toggle shows
    expect(page.locator("#category-toggle")).to_have_text(re.compile(r"Kategori:\s+alle"))
    expect(page.locator("#category-toggle")).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#categories")).to_be_hidden()
    open_categories(page)
    expect(page.locator("#category-toggle")).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Escape")
    expect(page.locator("#categories")).to_be_hidden()
    expect(page.locator("#category-toggle")).to_be_focused()


def test_category_filter(page):
    page.goto("/")
    open_categories(page)
    page.get_by_role("button", name="Meieri", exact=True).click()
    expect(cards(page)).to_have_count(1)
    # Picking one folds the list away and the toggle says which
    expect(page.locator("#categories")).to_be_hidden()
    expect(page.locator("#category-toggle")).to_have_text(re.compile(r"Kategori:\s+Meieri"))
    open_categories(page)
    expect(page.get_by_role("button", name="Meieri", exact=True)).to_have_attribute("aria-pressed", "true")
    page.get_by_role("button", name="Alle", exact=True).click()
    expect(cards(page)).to_have_count(5)


def test_product_names_are_not_rendered_as_html(page):
    page.goto("/")
    expect(cards(page).filter(has_text="<img src=x")).to_have_count(1)
    assert page.evaluate("window.pwned") is None


def test_no_horizontal_scroll_on_a_phone(browser, site):
    context = browser.new_context(base_url=site[0], viewport={"width": 360, "height": 740})
    page = context.new_page()
    for path in ["/", "/upload.html", "/takk.html", "/produkt.html?navn=Lettmelk", "/handleliste.html?v=Lettmelk&v=Agurk", "/tilbud.html", "/middag.html"]:
        page.goto(path)
        page.wait_for_load_state("networkidle")
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
    page.wait_for_url("**/takk.html?id=*.jpg")
    expect(page.locator("#state")).to_contain_text("I kø")
    assert len(set((site[1] / "uploads").iterdir()) - saved_before) == 1

    # The worker is off in these tests: mark the job read, as it would
    job_id = page.url.split("id=")[1]
    engine = create_engine(f"sqlite:///{site[1] / 'sjekka.db'}")
    with Session(engine) as db:
        db.add(models.Receipt(image_filename=job_id, store_chain="Rema 1000", purchase_date=date.today()))
        db.flush()
        rid = db.query(models.Receipt).filter_by(image_filename=job_id).one().id
        db.add(models.ReceiptItem(receipt_id=rid, canonical_name="Rømme", total_price=26.9, pack_size=1))
        db.query(models.Job).filter_by(filename=job_id).update({"status": "done"})
        db.commit()
    engine.dispose()
    expect(page.locator("#state")).to_have_text("Lest. Prisene ligger nå i oversikten.", timeout=8000)
    expect(page.locator("#lede")).to_have_text("Kvitteringen er lest, og bildet er slettet.")
    expect(page.locator("#found .chain img")).to_have_attribute("alt", "Rema 1000")
    expect(page.locator("#items a")).to_have_text("Rømme")
    expect(page.locator("#items a")).to_have_attribute("href", "/produkt.html?navn=R%C3%B8mme")

    # The photo comes from this tab, not from the server
    img = page.locator("#img")
    expect(img).to_be_visible()
    assert img.get_attribute("src").startswith("data:image/jpeg;")


def test_thank_you_page_without_a_photo(page):
    page.goto("/takk.html")
    expect(page.locator("#progress")).to_be_hidden()
    expect(page.locator("h1")).to_have_text("Takk.")
    expect(page.locator("#img")).to_be_hidden()


def test_upload_rejects_non_images_before_sending(page, tmp_path):
    page.goto("/upload.html")
    text = tmp_path / "notes.txt"
    text.write_text("hei")
    page.set_input_files("#receipt", text)
    expect(page.locator("#status")).to_contain_text("må være et bilde")
    expect(page.locator("#btn")).to_be_disabled()
    # Said where the file was chosen, too
    expect(page.locator("#drop")).to_have_class(re.compile("invalid"))
    expect(page.locator("#prompt")).to_contain_text("må være et bilde")


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
    # The other stores with a price are on the product page
    expect(milk.locator(".more-stores")).to_have_text("+1 butikker til")
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
    expect(page.locator("#notice")).to_have_text("Ingen kvitteringer har denne varen ennå.")


SEEDED = {'<img src=x onerror="window.pwned=1">', "Seigmenn", "Agurk", "Lettmelk", "Kjøttdeig"}


def sort_by(page, name):
    page.locator("#sort-toggle").click()
    page.get_by_role("menuitemradio", name=name).click()


def card_names(page):
    """The seeded products in display order; other tests may add more to the shared server."""
    return [n for n in page.locator(".card h3").all_text_contents() if n in SEEDED]


def test_sorting_by_price_and_newest(page):
    page.goto("/")
    expect(cards(page).first).to_be_visible()
    sort_by(page, "Lavest pris")
    # One item each: <img…> 10,00 · Seigmenn 38,70/3 = 12,90 · Agurk 30/2 = 15,00 · Lettmelk 22,90 · Kjøttdeig 89,00
    assert card_names(page) == ['<img src=x onerror="window.pwned=1">', "Seigmenn", "Agurk", "Lettmelk", "Kjøttdeig"]
    expect(page.locator("#sort-current")).to_have_text("lavest pris")
    sort_by(page, "Nyest")
    assert card_names(page)[-1] == "Agurk"  # 40 days old


def test_view_is_kept_in_the_url(page):
    page.goto("/")
    open_categories(page)
    page.get_by_role("button", name="Meieri", exact=True).click()
    sort_by(page, "Lavest pris")
    page.fill("#search", "melk")
    expect(page).to_have_url(re.compile(r"\?q=melk&kategori=Meieri&sorter=pris$"))

    page.goto("/?kategori=Kj%C3%B8tt&sorter=nyest")
    expect(cards(page)).to_have_count(1)
    expect(page.locator("#category-toggle")).to_have_text(re.compile(r"Kategori:\s+Kjøtt"))
    open_categories(page)
    expect(page.get_by_role("button", name="Kjøtt", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#sort-current")).to_have_text("nyest")


def test_unknown_category_in_url_falls_back_to_all(page):
    page.goto("/")
    expect(cards(page).first).to_be_visible()
    total = cards(page).count()
    page.goto("/?kategori=Finnes%20ikke")
    expect(cards(page)).to_have_count(total)
    open_categories(page)
    expect(page.get_by_role("button", name="Alle", exact=True)).to_have_attribute("aria-pressed", "true")


def test_sort_menu_opens_closes_and_works_with_the_keyboard(page):
    page.goto("/")
    toggle, menu = page.locator("#sort-toggle"), page.locator("#sort-menu")
    expect(menu).to_be_hidden()
    toggle.click()
    expect(menu).to_be_visible()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(page.get_by_role("menuitemradio", name="Navn")).to_have_attribute("aria-checked", "true")

    page.locator("h1").click()  # tapping elsewhere folds it away
    expect(menu).to_be_hidden()
    expect(toggle).to_have_attribute("aria-expanded", "false")

    toggle.focus()
    page.keyboard.press("ArrowDown")  # opens with the current choice focused
    expect(page.get_by_role("menuitemradio", name="Navn")).to_be_focused()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(menu).to_be_hidden()
    expect(page.locator("#sort-current")).to_have_text("lavest pris")
    expect(toggle).to_be_focused()

    toggle.click()
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()


def test_privacy_page_reveals_the_contact_address_on_click(page):
    page.goto("/personvern.html")
    assert "hotmail.com" not in page.locator("main").inner_text()
    page.get_by_role("button", name="vis e-postadresse").click()
    expect(page.get_by_role("link", name="haameti@hotmail.com")).to_have_attribute("href", "mailto:haameti@hotmail.com")


def test_pages_make_no_requests_to_other_sites(page, site):
    seen = []
    page.on("request", lambda r: seen.append(r.url))
    for path in ["/", "/upload.html", "/produkt.html?navn=Lettmelk", "/personvern.html", "/om.html"]:
        page.goto(path)
        page.wait_for_load_state("networkidle")
    assert [u for u in seen if not u.startswith(site[0])] == []


# The shopping list

def list_names(page):
    return page.locator(".list-item .what a").all_text_contents()


def test_plus_on_a_card_puts_the_product_on_the_list(page):
    page.goto("/")
    toggle = cards(page).filter(has_text="Lettmelk").get_by_role("button", name="Legg Lettmelk på handlelisten")
    expect(toggle).to_have_attribute("aria-pressed", "false")
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".top [data-list-count]")).to_have_text("1")
    # The card still opens the product page; the button doesn't
    assert page.url.endswith("/")

    page.goto("/handleliste.html")
    assert list_names(page) == ["Lettmelk"]
    page.goto("/")
    toggle = cards(page).filter(has_text="Lettmelk").locator(".list-toggle")
    expect(toggle).to_have_attribute("aria-pressed", "true")
    toggle.click()
    expect(page.locator(".top [data-list-count]")).to_be_hidden()


def test_product_page_button(page):
    page.goto("/produkt.html?navn=Kj%C3%B8ttdeig")
    page.get_by_role("button", name="Legg på handlelisten").click()
    added = page.get_by_role("button", name="På handlelisten ✓")
    expect(added).to_have_attribute("title", "Trykk for å ta den av listen")
    expect(page.locator("#list-link")).to_be_visible()
    added.click()
    expect(page.locator("#list-link")).to_be_hidden()
    page.get_by_role("button", name="Legg på handlelisten").click()
    page.locator("#list-link").click()
    page.wait_for_url("**/handleliste.html")
    assert list_names(page) == ["Kjøttdeig"]


def test_a_saved_list_takes_a_products_new_name(page):
    # Saved when the product was called otherwise; a line already there is added to
    page.goto("/handleliste.html")
    page.evaluate("""localStorage.setItem("sjekka.handleliste", JSON.stringify(
        [{name: "Seig menn", count: 2}, {name: "Seigmenn", count: 1}, {name: "Lettmelk", count: 1}]))""")
    page.reload()
    expect(page.locator(".list-item")).to_have_count(2)
    assert list_names(page) == ["Seigmenn", "Lettmelk"]
    saved = page.evaluate("JSON.parse(localStorage.getItem('sjekka.handleliste'))")
    assert saved == [{"name": "Seigmenn", "count": 3}, {"name": "Lettmelk", "count": 1}]


def test_list_finds_the_cheapest_store_for_the_whole_list(page):
    page.goto("/handleliste.html")
    expect(page.locator("#empty")).to_be_visible()
    add = page.get_by_role("combobox", name="Legg til vare")
    add.fill("melk")
    expect(page.get_by_role("option").first).to_have_text(re.compile("^Lettmelk"))
    page.get_by_role("option").first.click()
    add.fill("kjøtt")
    page.get_by_role("option", name=re.compile("^Kjøttdeig")).click()
    expect(page.locator(".list-item")).to_have_count(2)
    expect(add).to_be_focused()

    # Rema: 22,90 + 89,00. Kiwi has milk at 24,90 and an estimated mince
    verdict = page.locator("#verdict")
    expect(verdict.locator(".chain img")).to_have_attribute("alt", "Rema 1000")
    expect(page.locator("#verdict-total")).to_have_text("111,90 kr")
    ranks = page.locator(".rank")
    expect(ranks).to_have_count(2)
    expect(ranks.nth(1).locator(".chain img")).to_have_attribute("alt", "Kiwi")
    # Rema has every price from a receipt, so there is nothing to flag
    expect(page.locator("#verdict-facts")).to_be_hidden()
    expect(page.locator("#method")).to_be_visible()
    expect(page.locator("#method .note")).to_be_hidden()

    # Each line says where it's cheapest
    milk = page.locator(".list-item", has_text="Lettmelk")
    expect(milk.locator(".hint .chain img")).to_have_attribute("alt", "Rema 1000")
    expect(milk.locator(".hint")).to_contain_text("22,90 kr")

    # Counts change the totals
    milk.get_by_role("button", name="Flere Lettmelk").click()
    expect(page.locator("#verdict-total")).to_have_text("134,80 kr")
    milk.get_by_role("button", name="Færre Lettmelk").click()
    expect(page.locator("#verdict-total")).to_have_text("111,90 kr")

    # The breakdown marks estimated prices
    kiwi = ranks.nth(1)
    kiwi.locator(".rank-row").click()
    expect(kiwi.locator(".rank-row")).to_have_attribute("aria-expanded", "true")
    expect(kiwi.locator("tr.estimated")).to_contain_text("Kjøttdeig")
    expect(kiwi.locator("tr.estimated")).to_contain_text("anslått")
    expect(kiwi.locator(".coverage")).to_have_text("1 av 2 priser fra kvitteringer, resten anslått.")

    # It survives a reload
    page.reload()
    assert list_names(page) == ["Lettmelk", "Kjøttdeig"]


def test_products_without_prices_stay_on_the_list(page):
    page.goto("/handleliste.html")
    page.get_by_role("combobox", name="Legg til vare").fill("kanel")
    expect(page.get_by_role("option")).to_have_text(re.compile("«kanel»"))
    page.keyboard.press("Enter")
    expect(page.locator(".list-item", has_text="Kanel").locator(".hint")).to_have_text("ingen priser ennå")
    expect(page.locator("#notice")).to_contain_text("Ingen av varene har priser ennå")
    expect(page.locator("#verdict")).to_be_hidden()

    page.get_by_role("combobox", name="Legg til vare").fill("agurk")
    page.keyboard.press("Enter")
    expect(page.locator("#unknown")).to_contain_text("Kanel")
    expect(page.locator("#verdict-total")).to_have_text("15 kr")


def test_removing_and_clearing_with_undo(page):
    page.goto("/handleliste.html?v=Lettmelk&v=Agurk")
    expect(page.locator(".list-item")).to_have_count(2)
    page.get_by_role("button", name="Fjern Lettmelk").click()
    assert list_names(page) == ["Agurk"]
    expect(page.get_by_role("button", name="Fjern Agurk")).to_be_focused()

    page.get_by_role("button", name="Tøm listen").click()
    expect(page.locator(".list-item")).to_have_count(0)
    expect(page.locator(".top [data-list-count]")).to_be_hidden()
    page.get_by_role("button", name="Listen er tømt. Angre").click()
    assert list_names(page) == ["Agurk"]


def test_shared_list_link(page, browser, site):
    page.goto("/handleliste.html?v=Lettmelk~3&v=Agurk")
    # An empty list just takes the shared one, and the link is tidied away.
    # Whoever opens it is told where it came from, and what the page is for
    expect(page.locator(".list-item")).to_have_count(2)
    expect(page.locator("#shared-text")).to_contain_text("Noen har delt en handleliste med deg: 2 varer")
    expect(page.get_by_role("button", name="Bruk den delte")).to_be_hidden()
    expect(page.locator("#intro .lede")).to_be_visible()
    page.get_by_role("button", name="Greit").click()
    expect(page.locator("#shared")).to_be_hidden()
    expect(page.locator(".list-item", has_text="Lettmelk").locator(".qty span")).to_have_text("3")
    expect(page).to_have_url(re.compile(r"/handleliste\.html$"))

    # Sharing builds the same kind of link
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.get_by_role("button", name="Del listen").click()
    expect(page.locator("#share-status")).to_have_text("Lenken er kopiert.")
    link = page.evaluate("navigator.clipboard.readText()")
    assert link.endswith("/handleliste.html?v=Lettmelk%7E3&v=Agurk")

    # With a list of your own, you choose
    page.goto("/handleliste.html?v=Kj%C3%B8ttdeig")
    expect(page.locator("#shared")).to_be_visible()
    expect(page.get_by_role("button", name="Behold min egen")).to_be_visible()
    expect(page.locator("#shared-text")).to_contain_text("1 vare")
    page.get_by_role("button", name="Legg til i min liste").click()
    expect(page.locator("#shared")).to_be_hidden()
    assert list_names(page) == ["Lettmelk", "Agurk", "Kjøttdeig"]

    page.goto("/handleliste.html?v=Agurk")
    page.get_by_role("button", name="Bruk den delte").click()
    assert list_names(page) == ["Agurk"]


def test_phone_keeps_the_answer_in_view_while_the_page_scrolls(browser, site):
    context = browser.new_context(base_url=site[0], viewport={"width": 390, "height": 700})
    page = context.new_page()
    page.goto("/handleliste.html?v=Lettmelk&v=Kj%C3%B8ttdeig&v=Agurk&v=Seigmenn")
    bar = page.locator("#bar")
    expect(bar).to_be_visible()
    expect(bar).to_contain_text("kr")
    # Under the heading to begin with, and at the top of the screen once you scroll
    start = bar.bounding_box()["y"]
    assert start > 100
    page.mouse.wheel(0, 900)
    page.wait_for_timeout(300)
    assert bar.bounding_box()["y"] == 0
    # It says who is cheapest and by how much over the next best
    expect(page.locator("#bar-note")).to_contain_text(re.compile(r"kr under |omtrent likt med "))
    # A tap brings the full answer into view, not hidden under the bar
    bar.click()
    page.wait_for_timeout(600)
    assert page.locator("#verdict").bounding_box()["y"] >= bar.bounding_box()["height"]
    context.close()


def test_the_bar_updates_as_items_are_added(browser, site):
    context = browser.new_context(base_url=site[0], viewport={"width": 390, "height": 700})
    page = context.new_page()
    page.goto("/handleliste.html?v=Lettmelk")
    before = page.locator("#bar-total").inner_text()
    page.locator("#pick-list button").first.click()
    expect(page.locator("#bar-total")).not_to_have_text(before)
    context.close()


def test_the_bar_is_not_shown_on_a_wide_screen(page):
    page.goto("/handleliste.html?v=Lettmelk&v=Kj%C3%B8ttdeig&v=Agurk&v=Seigmenn")
    expect(page.locator("#verdict")).to_be_visible()
    expect(page.locator("#bar")).to_be_hidden()


# Flyer offers

def test_offers_page_judges_each_offer(page):
    page.goto("/tilbud.html")
    offers = page.locator(".card")
    expect(offers).to_have_count(2)
    candy = offers.first
    # The brand leads; the product name and size come second
    expect(candy.locator("h3")).to_have_text("Laban seigmenn")
    expect(candy.locator(".brand")).to_have_text("Seigmenn · 260 g")
    # A short verdict; the explanation and the flyer's own text are folded away
    expect(candy.locator(".verdict-tag")).to_have_text("Ekte tilbud −23 %")
    expect(candy.locator(".deal-verdict")).to_be_hidden()
    candy.get_by_text("Detaljer").click()
    expect(candy.locator(".deal-verdict")).to_have_text("23 % under vanlig pris i butikken, som var 4,96 kr / 100 g.")
    expect(candy.locator(".chain img")).to_have_attribute("alt", "Meny")
    buns = offers.nth(1)
    expect(buns.locator(".verdict-tag")).to_have_text("Butikken sier −38 %")
    expect(buns.locator(".deal-verdict")).to_contain_text("Kundeavisen sier 38 % under førpris på 40 kr")
    expect(buns.locator(".raw")).to_have_text("I kundeavisen: «SAFRANBOLLER 6 stk»")

    page.get_by_role("button", name="Rema 1000", exact=True).click()
    expect(offers).to_have_count(1)
    expect(page).to_have_url(re.compile(r"kjede=Rema"))
    page.get_by_role("button", name="Alle", exact=True).click()
    page.get_by_label("Bare ekte tilbud").check()
    expect(offers).to_have_count(1)
    page.get_by_label("Bare ekte tilbud").uncheck()
    page.fill("#search", "safran")
    expect(offers).to_have_count(1)
    page.fill("#search", "")

    candy.get_by_role("button", name="Legg Seigmenn på handlelisten").click()
    expect(page.locator(".top [data-list-count]")).to_have_text("1")


def test_offer_shows_on_the_card_and_product_page(page):
    page.goto("/")
    expect(cards(page).filter(has_text="Seigmenn").locator(".deal-tag")).to_have_text("Tilbud 9,90 kr hos Meny")
    expect(cards(page).filter(has_text="Lettmelk").locator(".deal-tag")).to_have_count(0)

    page.goto("/produkt.html?navn=Seigmenn")
    expect(page.locator("#offers-now")).to_be_visible()
    expect(page.locator("#offers-now .verdict-tag")).to_contain_text("Ekte tilbud")
    expect(page.locator("#best .label")).to_have_text("Billigst nå, på tilbud")
    expect(page.locator("#best-price")).to_have_text("9,90 kr")


def test_shopping_list_uses_offers(page):
    page.goto("/handleliste.html?v=Seigmenn&v=Safranboller")
    candy = page.locator(".list-item", has_text="Seigmenn")
    expect(candy.locator(".offer-tag")).to_have_text("tilbud")
    expect(page.locator(".list-item", has_text="Safranboller").locator(".hint .chain img")).to_have_attribute("alt", "Rema 1000")
    expect(page.locator("#unknown")).to_be_hidden()
    expect(page.locator("#verdict-facts")).to_contain_text("1 vare på tilbud")


MULTI_BUY = {"canonical_name": "Energidrikk", "raw_name": "BATTERY 8 FOR 100,- + pant", "brand": "Battery", "variant": None,
             "category": "Drikke", "chain": "Obs", "quantity": 500.0, "unit": "ml", "pack_size": 8, "total_price": 100.0,
             "each": 12.5, "per_unit": 2.5, "by_weight": False, "is_member_price": False, "deal": "8 for 100", "pre_price": None,
             "valid_from": str(date.today()), "valid_to": str(date.today() + timedelta(days=5)),
             "verdict": {"kind": "cheapest_known", "usual": None, "discount": None, "cheaper_chain": None, "cheaper_price": None, "per": "100 ml"}}


def test_a_multi_buy_offer_says_how_many_you_must_buy(page):
    page.route("**/offers*", lambda route: route.fulfill(json=[MULTI_BUY]))
    page.goto("/tilbud.html")
    card = page.locator("#grid .card").first
    expect(card.locator(".price")).to_have_text("8 for 100 kr")
    expect(card.locator(".must-buy")).to_have_text("Må kjøpe 8 · 12,50 kr stk")


def test_the_shopping_list_offers_a_multi_buy_instead_of_pricing_one_by_it(page):
    def answer(route):
        count = route.request.post_data_json["items"][0].get("count", 1)
        obs = 100.0 if count >= 8 else 30.0 * count
        line = {"name": "Energidrikk", "count": count, "size": "500 ml", "cheapest": "Obs" if count >= 8 else "Kiwi",
                "prices": {"Kiwi": {"price": 27.5 * count, "estimated": False, "date": None, "brand": None, "offer_until": None, "deal": None},
                           "Obs": {"price": obs, "estimated": False, "date": None, "brand": None,
                                   "offer_until": MULTI_BUY["valid_to"] if count >= 8 else None, "deal": None}},
                "multi_buys": [] if count >= 8 else [{"chain": "Obs", "deal": "8 for 100", "needs": 8, "each": 12.5, "until": MULTI_BUY["valid_to"]}]}
        stores = sorted([{"chain": c, "total": p["price"], "known": 1, "estimated": 0, "offers": 0, "index": 1.0}
                         for c, p in line["prices"].items()], key=lambda s: s["total"])
        route.fulfill(json={"items": [line], "unknown": [], "stores": stores, "split": None})
    page.route("**/basket", answer)
    page.goto("/handleliste.html?v=Energidrikk")
    item = page.locator(".list-item", has_text="Energidrikk")
    expect(item.locator(".multi-buy")).to_contain_text("8 for 100 kr: 12,50 kr stk hvis du tar 8")
    item.get_by_role("button", name="Ta 8 Energidrikk for tilbudet hos Obs").click()
    expect(item.locator(".multi-buy")).to_have_count(0)
    assert page.evaluate("JSON.parse(localStorage.getItem('sjekka.handleliste'))") == [{"name": "Energidrikk", "count": 8}]


def test_whole_prices_drop_the_decimals(page):
    page.goto("/")
    formatted = page.evaluate("() => [15, 15.001, 24.9, 0.5, 99.99].map(kr.format)")
    assert formatted == ["15", "15", "24,90", "0,50", "99,99"]


def test_every_offer_verdict_has_a_tag_and_a_sentence(page):
    page.goto("/tilbud.html")
    verdicts = page.evaluate("""() => {
        const base = {usual: 30, per: "stk", discount: null, cheaper_chain: null, cheaper_price: null};
        return [
            {kind: "real", discount: 0.3}, {kind: "small", discount: 0.05}, {kind: "not", discount: -0.1},
            {kind: "not", discount: 0.01}, {kind: "cheaper_elsewhere", cheaper_chain: "Kiwi", cheaper_price: 20},
            {kind: "cheapest_known"}, {kind: "claimed", discount: 0.25, pre_price: 40}, {kind: "unknown"},
        ].map(v => {
            const o = {verdict: {...base, ...v}};
            return [verdictTag(o).textContent, offerVerdict(o).textContent];
        });
    }""")
    assert verdicts == [
        ["Ekte tilbud −30 %", "30 % under vanlig pris i butikken, som var 30 kr / stk."],
        ["Bare −5 %", "Bare 5 % under vanlig pris i butikken, som var 30 kr / stk."],
        ["Dyrere enn vanlig", "Dyrere enn vanlig pris i butikken, som var 30 kr / stk."],
        ["Vanlig pris", "Omtrent vanlig pris i butikken (30 kr / stk)."],
        ["Billigere hos Kiwi", "Uten tilbud er den billigere hos  20 kr / stk"],
        ["Lavest kjente pris", "Lavere enn prisene vi kjenner fra andre butikker. Vi har ingen kvittering fra denne butikken å sammenligne med."],
        ["Butikken sier −25 %", "Kundeavisen sier 25 % under førpris på 40 kr. Ikke sjekket mot kvitteringer ennå."],
        ["Ikke sjekket ennå", "Ingen kvitteringer å sammenligne med ennå."],
    ]


def test_offers_page_shows_a_page_at_a_time_and_remembers_the_filter(page):
    page.goto("/tilbud.html?ekte=1")
    expect(page.get_by_label("Bare ekte tilbud")).to_be_checked()
    expect(page.locator(".card")).to_have_count(1)
    page.get_by_label("Bare ekte tilbud").uncheck()
    # Fifty copies of the two offers: 48 at first, the rest on request
    page.evaluate("() => { offers = Array.from({length: 50}, (_, i) => offers[i % 2]); render(); }")
    expect(page.locator(".card")).to_have_count(48)
    page.get_by_role("button", name="Vis flere (2 til)").click()
    expect(page.locator(".card")).to_have_count(50)
    expect(page.locator("#more")).to_be_hidden()


def test_explanations_are_folded_away_until_asked_for(page):
    page.goto("/tilbud.html")
    method = page.locator("details.method")
    expect(method.locator("summary")).to_have_text("Slik sjekker vi tilbudene")
    expect(method).not_to_have_attribute("open", "")
    method.locator("summary").click()
    expect(method).to_have_attribute("open", "")


@pytest.mark.parametrize("path", ["/", "/tilbud.html", "/handleliste.html", "/produkt.html?navn=Lettmelk", "/personvern.html"])
def test_upload_link_stands_out_in_the_menu(page, path):
    page.goto(path)
    expect(page.locator(".top nav a.cta")).to_have_attribute("href", "/upload.html")


def test_list_says_how_many_prices_are_estimated(page):
    # Rema has milk but no cucumber, and is still cheapest with an estimate for it
    page.goto("/handleliste.html?v=Lettmelk&v=Agurk")
    expect(page.locator("#verdict .chain img")).to_have_attribute("alt", "Rema 1000")
    expect(page.locator("#verdict-facts")).to_have_text("1 pris anslått")


# Chains we have no logo for yet; they show their name instead
NO_LOGO = set()


def test_every_known_chain_has_a_logo_that_loads(page):
    from app.basket import CHAINS
    page.goto("/")
    names = [name for _, name in CHAINS]
    badges = page.evaluate("""async names => Promise.all(names.map(async name => {
        const img = chainBadge(name).querySelector("img");
        if (!img) return [name, null];
        await img.decode().catch(() => {});
        return [name, img.naturalWidth > 0];
    }))""", names)
    assert dict(badges) == {name: None if name in NO_LOGO else True for name in names}


def test_a_card_only_flags_an_offer_that_beats_its_price(page):
    page.goto("/")
    beats = page.evaluate("""() => [
        beats({each: 9.9, per_unit: 3.81, unit: "g"}, {total_price: 38.7, pack_size: 3, price_per_unit: 4.96, unit: "g"}),
        beats({each: 10, per_unit: 12.82, unit: "g"}, {total_price: 10, pack_size: 1, price_per_unit: 12.82, unit: "g"}),
        beats({each: 25, per_unit: 25, unit: "stk"}, {total_price: 30, pack_size: 1, price_per_unit: 30, unit: "stk"}),
        beats({each: 25, per_unit: 25, unit: "stk"}, {total_price: 20, pack_size: 1, price_per_unit: 20, unit: "stk"}),
    ]""")
    assert beats == [True, False, True, False]


def test_receipt_counts_wait_until_there_are_enough(page):
    page.goto("/")
    expect(page.locator("#stat-receipts")).not_to_have_text("–")  # loaded, under 50
    expect(page.locator("#stats")).to_be_hidden()


def test_one_price_has_no_timeline(page):
    page.goto("/produkt.html?navn=Kj%C3%B8ttdeig")
    expect(page.locator(".store")).to_have_count(1)
    expect(page.locator(".store svg")).to_have_count(0)
    expect(page.locator("#axis")).to_be_hidden()
    page.goto("/produkt.html?navn=Lettmelk")
    expect(page.locator(".store svg")).to_have_count(2)


def test_an_offer_seen_on_a_receipt_says_so(page):
    page.goto("/produkt.html?navn=Lettmelk")
    expect(page.locator(".store")).to_have_count(2)
    found = page.evaluate("""() => {
        const offer = {chain: "Kiwi", each: 24.9, valid_from: "2000-01-01", valid_to: "2999-12-31"};
        return [!!receiptDuring(offer), !!receiptDuring({...offer, each: 19.9}), !!receiptDuring({...offer, chain: "Meny"}),
                sameChain("KIWI 505 STORO", "Kiwi"), sameChain("Coop Mega", "Coop Prix")];
    }""")
    assert found == [True, False, False, True, False]


def test_list_explanation_shrinks_once_there_is_a_list(page):
    page.goto("/handleliste.html")
    expect(page.locator("#intro .lede")).to_be_visible()
    # A list from a link keeps it until its notice has been read
    page.goto("/handleliste.html?v=Lettmelk")
    expect(page.locator("#intro .lede")).to_be_visible()
    page.get_by_role("button", name="Greit").click()
    expect(page.locator("#intro")).to_have_class(re.compile("compact"))
    expect(page.locator("#intro .lede")).to_be_hidden()
    page.reload()
    expect(page.locator("#intro .lede")).to_be_hidden()


def test_real_offer_filter_is_hidden_while_no_offer_is_checked(page):
    def without_real(route):
        offers = route.fetch().json()
        for o in offers:
            o["verdict"]["kind"] = "unknown"
        route.fulfill(json=offers)
    page.route("**/offers", without_real)
    page.goto("/tilbud.html?ekte=1")
    expect(page.locator(".card")).to_have_count(2)
    expect(page.get_by_label("Bare ekte tilbud")).to_be_hidden()
    expect(page.locator("#count")).to_have_text("2 tilbud")


def test_upload_offers_the_camera_and_photo_tips(page):
    page.goto("/upload.html")
    expect(page.locator("#camera")).to_have_attribute("capture", "environment")
    expect(page.locator(".tips")).to_contain_text("hele kvitteringen")


def test_loose_produce_shows_its_kilo_price(page):
    page.goto("/")
    shown = page.evaluate("""() => {
        const oranges = {by_weight: true, unit: "g", quantity: 1570, total_price: 70.49, price_per_unit: 4.49, pack_size: 1};
        const offer = {by_weight: true, unit: "g", quantity: 1000, pack_size: 1, each: 29.9, per_unit: 2.99, total_price: 29.9};
        return [prices(oranges), describe({...oranges, variant: "Løsvekt"}), offerPrice(offer), Math.round(unitPrice(oranges) * 100) / 100];
    }""")
    assert shown == [{"each": "44,90 kr / kg", "unit": "1,57 kg for 70,49 kr"}, "Løsvekt", "29,90 kr / kg", 44.9]


def test_product_page_prices_loose_produce_per_kilo(page):
    def weighed(route):
        product = route.fetch().json()
        for p in product["prices"]:
            p.update(by_weight=True, unit="g", quantity=1570, total_price=70.49, price_per_unit=4.49, pack_size=1)
        route.fulfill(json=product)
    page.route("**/products/Lettmelk", weighed)
    page.goto("/produkt.html?navn=Lettmelk")
    expect(page.locator("#best-price")).to_have_text("44,90 kr / kg")
    expect(page.locator("#best-unit")).to_have_text(re.compile(r"^1,57 kg for 70,49 kr · "))
    expect(page.locator("#scale-note")).to_contain_text("kr / kg")


def test_list_suggests_products_that_are_only_on_offer(page):
    page.goto("/handleliste.html")
    add = page.get_by_role("combobox", name="Legg til vare")
    add.fill("safran")
    # No receipt has buns yet, but Rema's flyer does
    expect(page.get_by_role("option").first).to_have_text(re.compile("^Safranboller.*tilbud hos Rema 1000"))
    add.fill("seig")
    expect(page.get_by_role("option").first).to_have_text(re.compile("^Seigmenn.*Meny · tilbud"))
    page.keyboard.press("Enter")
    assert list_names(page) == ["Seigmenn"]


def test_a_close_call_on_estimates_is_not_called_a_win(page):
    def mostly_estimated(route):
        result = route.fetch().json()
        rema, kiwi = result["stores"]
        rema.update(total=162.89, known=1, estimated=4)
        kiwi.update(total=163.26, known=2, estimated=3)
        route.fulfill(json=result)
    page.route("**/basket", mostly_estimated)
    page.goto("/handleliste.html?v=Lettmelk&v=Kj%C3%B8ttdeig")
    expect(page.locator("#verdict-total")).to_have_text("≈ 163 kr")
    expect(page.locator("#verdict .label").first).to_have_text("Trolig billigst for hele listen")
    expect(page.locator("#verdict-note")).to_have_text("Omtrent likt med Kiwi. Flere kvitteringer gir et sikrere svar.")
    expect(page.locator(".rank").nth(1).locator(".amount")).to_have_text("≈ 163 kromtrent likt")


def test_a_clear_gap_on_estimates_is_rounded(page):
    def apart(route):
        result = route.fetch().json()
        rema, kiwi = result["stores"]
        rema.update(total=150.2, known=1, estimated=4)
        kiwi.update(total=190.7, known=4, estimated=1)
        route.fulfill(json=result)
    page.route("**/basket", apart)
    page.goto("/handleliste.html?v=Lettmelk&v=Kj%C3%B8ttdeig")
    expect(page.locator("#verdict-note")).to_have_text("Rundt 41 kr billigere enn Kiwi.")
    expect(page.locator(".rank").nth(1).locator(".amount")).to_have_text("190,70 kr+41 kr")


def test_the_verdict_compares_with_the_next_best_store_and_the_dearest(page):
    def three(route):
        result = route.fetch().json()
        rema, kiwi = result["stores"]
        rema.update(total=100.0, known=5, estimated=0, estimated_prices=0)
        kiwi.update(total=111.8, known=5, estimated=0)
        joker = {**kiwi, "chain": "Joker", "total": 160.8}
        result["stores"] = [rema, kiwi, joker]
        for line in result["items"]:
            line["prices"]["Joker"] = line["prices"][kiwi["chain"]]
        route.fulfill(json=result)
    page.route("**/basket", three)
    page.goto("/handleliste.html?v=Lettmelk&v=Kj%C3%B8ttdeig")
    expect(page.locator("#verdict-note")).to_have_text(
        "11,80 kr billigere enn Kiwi, som er nest billigst. Mot Joker, som er dyrest, sparer du 60,80 kr.")


def test_about_page_shows_the_films_without_loading_them(page):
    page.goto("/om.html")
    videos = page.locator(".films video")
    expect(videos).to_have_count(3)
    # Nothing is downloaded until someone presses play
    for i in range(3):
        expect(videos.nth(i)).to_have_attribute("preload", "none")
        expect(videos.nth(i).locator("track")).to_have_attribute("srclang", "en")
    # The words are in the picture: the player's captions are there but off, and the sound is at half
    assert page.evaluate("[...document.querySelectorAll('.films video')].map(v => [v.volume, v.textTracks[0].mode])") \
        == [[0.5, "disabled"]] * 3
    page.get_by_role("contentinfo").get_by_role("link", name="om").click()
    expect(page).to_have_url(re.compile("/om.html$"))


def test_the_promo_film_is_the_one_for_the_screen_it_is_watched_on(browser, site):
    for size, film, tall in [({"width": 1280, "height": 800}, "sjekka-promo-desktop", False),
                             ({"width": 390, "height": 844}, "sjekka-promo-mobil", True)]:
        screen = browser.new_page(viewport=size, base_url=site[0])
        screen.goto("/om.html")
        video = screen.locator("#promo video")
        # Each file is addressed by its contents, so a new film is fetched at once
        expect(video.locator("source")).to_have_attribute("src", re.compile(rf"^/video/{film}\.mp4\?v=\w+$"))
        expect(video.locator("track")).to_have_attribute("src", re.compile(rf"^/video/{film}\.vtt\?v=\w+$"))
        assert re.fullmatch(rf"/video/{film}\.jpg\?v=\w+", video.get_attribute("poster"))
        box = video.bounding_box()
        assert (box["height"] > box["width"]) is tall
        screen.close()


def test_a_promo_film_already_playing_is_not_swapped_when_the_screen_turns(browser, site):
    screen = browser.new_page(viewport={"width": 1280, "height": 800}, base_url=site[0])
    screen.goto("/om.html")
    # Pretend it has been playing: the page must leave its source alone
    screen.evaluate("Object.defineProperty(document.querySelector('#promo video'), 'currentTime', { value: 5 })")
    screen.set_viewport_size({"width": 390, "height": 844})
    expect(screen.locator("#promo video source")).to_have_attribute("src", re.compile(r"^/video/sjekka-promo-desktop\.mp4\?v=\w+$"))
    screen.close()


def test_a_search_that_matches_offers_links_to_them(page):
    page.goto("/")
    expect(cards(page).first).to_be_visible()
    # No receipt has saffron buns, but Rema's flyer does
    page.fill("#search", "safran")
    expect(cards(page)).to_have_count(0)
    link = page.locator("#offer-hits")
    expect(link).to_have_text("1 vare på tilbud denne uka passer «safran» →")
    link.click()
    expect(page).to_have_url(re.compile(r"/tilbud\.html\?q=safran$"))
    expect(page.locator(".card")).to_have_count(1)


def test_a_product_only_in_flyers_can_go_on_the_list(page):
    # No receipt has saffron buns; Rema's flyer does
    page.goto("/produkt.html?navn=Safranboller")
    expect(page.locator("#offers-now .offer-line")).to_have_count(1)
    expect(page.locator("#notice")).to_have_text("Ingen kvitteringer ennå. Prisene over er fra kundeavisene.")
    page.get_by_role("button", name="Legg på handlelisten").click()
    expect(page.get_by_role("button", name="På handlelisten ✓")).to_be_visible()
    page.goto("/handleliste.html")
    assert list_names(page) == ["Safranboller"]


def test_a_name_without_prices_suggests_ones_that_have_them(page):
    page.goto("/handleliste.html?v=Melk~2")
    item = page.locator(".list-item", has_text="Melk")
    expect(item.locator(".hint .none")).to_have_text("ingen priser ennå")
    swap = item.get_by_role("button", name="Bytt Melk med Lettmelk")
    expect(swap).to_have_text("Lettmelk")
    swap.click()
    # Swapped, with its count, and now priced
    assert list_names(page) == ["Lettmelk"]
    expect(page.locator(".list-item", has_text="Lettmelk").locator(".qty")).to_contain_text("2")
    expect(page.locator(".list-item", has_text="Lettmelk").locator(".hint")).to_contain_text("billigst: Q hos")


def test_a_product_of_several_items_lets_you_pick_one(page):
    page.goto("/produkt.html?navn=Lettmelk")
    # Lettmelk is Tine's and Q's: the page says its prices are the cheapest of them
    expect(page.locator("#items .item-row a")).to_have_text(["Tine lettmelk", "Q lettmelk"])
    expect(page.locator("#best-what")).to_have_text("Q · 1 l")
    expect(page.locator("#stores-title")).to_have_text("Billigste lettmelk per butikk")
    expect(page.locator("#list-button")).to_have_text("Legg billigste lettmelk på handlelisten")
    page.locator("#items a", has_text="Tine lettmelk").click()
    # Tine's own page: only Tine's prices, and that is what goes on the list
    expect(page.locator("h1")).to_have_text("Tine lettmelk")
    expect(page.locator("#product-link a")).to_have_text("Lettmelk")
    expect(page.locator("#items-section")).to_be_hidden()
    expect(page.locator(".store")).to_have_count(1)
    page.locator("#list-button").click()
    assert page.evaluate("JSON.parse(localStorage.getItem('sjekka.handleliste'))") == [
        {"name": "Lettmelk", "count": 1, "brand": "Tine", "variant": None}]


def test_the_list_prices_an_item_apart_from_the_cheapest_of_its_product(page):
    page.goto("/handleliste.html?v=Lettmelk&v=Lettmelk|Tine~2")
    anything, tine = page.locator(".list-item").nth(0), page.locator(".list-item").nth(1)
    expect(anything.locator(".what a")).to_have_text("Lettmelk")
    expect(anything.locator(".hint")).to_contain_text("billigst: Q hos")
    expect(tine.locator(".what a")).to_have_text("Tine lettmelk")
    expect(tine.locator(".what a")).to_have_attribute("href", "/produkt.html?navn=Lettmelk&merke=Tine")
    expect(tine.locator(".hint")).to_contain_text("billigst hos")
    expect(tine.locator(".hint")).to_contain_text("24,90 kr")  # its latest at Kiwi
    expect(tine.locator(".qty")).to_contain_text("2")
    tine.get_by_role("button", name="Flere Tine lettmelk").click()
    expect(tine.locator(".qty")).to_contain_text("3")
    # Shared on as it is
    assert page.evaluate("listLink()").endswith("?v=Lettmelk&v=Lettmelk%7CTine%7E3")


def test_offers_ask_for_receipts_while_most_cannot_be_checked(page):
    page.goto("/tilbud.html")
    # One checked, one not: no need to ask
    expect(page.locator(".card")).to_have_count(2)
    expect(page.locator("#ask")).to_be_hidden()
    # Rema's only offer is the flyer's own claim
    page.get_by_role("button", name="Rema 1000", exact=True).click()
    ask = page.locator("#ask")
    expect(ask).to_contain_text("Har du en kvittering fra Rema 1000?")
    expect(ask.get_by_role("link", name="Last den opp")).to_have_attribute("href", "/upload.html")
    page.get_by_role("button", name="Meny", exact=True).click()
    expect(ask).to_be_hidden()


def test_a_receipt_can_be_uploaded_as_a_file(page, tmp_path):
    page.goto("/upload.html")
    expect(page.locator("#hint")).to_have_text("Bilde (JPEG, PNG, WebP, HEIC), PDF, CSV eller JSON · maks 10 MB")
    receipt = tmp_path / "kvittering.csv"
    receipt.write_text("Vare;Pris\nTine Lettmelk 1 l;24,90\n", encoding="utf-8")
    page.set_input_files("#receipt", str(receipt))
    expect(page.locator("#prompt")).to_have_text("Fil valgt")
    expect(page.locator("#file-name")).to_have_text("kvittering.csv")
    page.locator("#btn").click()
    page.wait_for_url("**/takk.html?batch=*")
    expect(page.locator("#lede")).to_contain_text("Vi tar vare på den i inntil 12 måneder")
    expect(page.locator("#state")).to_have_text("Gemini leser filen …")  # no worker in these tests


def test_the_upload_page_turns_away_other_files(page, tmp_path):
    page.goto("/upload.html")
    other = tmp_path / "notes.txt"
    other.write_text("hei")
    page.set_input_files("#receipt", str(other))
    expect(page.locator("#status")).to_contain_text("PDF, CSV eller JSON")
    expect(page.locator("#btn")).to_be_disabled()


def test_the_thank_you_page_follows_a_whole_history(page):
    answers = iter([
        {"parts": 12, "read": 3, "failed": 0, "waiting": 9, "receipts": 31, "duplicates": 0, "items": 402,
         "chains": {"Kiwi": 20, "Meny": 11}, "from": "2025-11-02", "to": "2026-01-30"},
        {"parts": 12, "read": 11, "failed": 1, "waiting": 0, "receipts": 140, "duplicates": 6, "items": 1890,
         "chains": {"Kiwi": 90, "Meny": 50}, "from": "2025-11-02", "to": "2026-10-20"},
    ])
    state = {"answer": next(answers)}
    page.route("**/jobs/batch/abc", lambda route: route.fulfill(json=state["answer"]))
    page.goto("/takk.html?batch=abc")
    expect(page.locator("#state")).to_have_text("Leser filen: del 4 av 12 …")
    expect(page.locator("#found")).to_contain_text("31 kvitteringer · 402 varer · 2. november 2025 til 30. januar 2026")
    state["answer"] = next(answers)
    expect(page.locator("#state")).to_have_text(
        "Lest. Prisene ligger nå i oversikten. 6 var allerede registrert og telles ikke to ganger. 1 av 12 deler kunne ikke leses.",
        timeout=10000)
    expect(page.locator("#found .chain img")).to_have_count(2)


def test_a_win_by_øre_on_an_estimate_is_not_called_a_win(page):
    # The real case: 39,89 against 39,90, with one of two prices estimated
    def one_øre(route):
        result = route.fetch().json()
        rema, kiwi = result["stores"]
        rema.update(total=39.89, known=1, estimated=1)
        kiwi.update(total=39.90, known=1, estimated=1)
        route.fulfill(json=result)
    page.route("**/basket", one_øre)
    page.goto("/handleliste.html?v=Lettmelk&v=Kj%C3%B8ttdeig")
    expect(page.locator("#verdict .label").first).to_have_text("Trolig billigst for hele listen")
    expect(page.locator("#verdict-note")).to_have_text("Omtrent likt med Kiwi. Flere kvitteringer gir et sikrere svar.")


def test_list_prices_say_what_size_they_are_for(page):
    page.goto("/handleliste.html?v=Lettmelk")
    expect(page.locator(".list-item .hint")).to_contain_text("22,90 kr for 1 l")
    page.locator(".rank-row").first.click()
    expect(page.locator(".rank-detail tbody tr").first).to_contain_text("Lettmelk Q for 1 l")


def test_enter_adds_what_was_typed_not_a_longer_name(page):
    # The real case: "melk" + Enter put Melkeglede (sweets) on the list
    names = [{"canonical_name": n, "brand": None, "store_count": c, "store_chain": "Kiwi"}
             for n, c in [("Melkeglede", 1), ("Kokosmelk", 1), ("Lettmelk", 2), ("Melkesjokolade", 3), ("Smør og olivenolje", 1),
                          ("Litago melk", 1)]]
    page.route("**/products", lambda route: route.fulfill(json=names))
    page.goto("/handleliste.html")
    add = page.get_by_role("combobox", name="Legg til vare")
    add.fill("melk")
    # A kind of milk before milk chocolate, the commoner first
    expect(page.get_by_role("option")).to_have_text([
        re.compile("^Lettmelk"), re.compile("^Kokosmelk"), re.compile("^Litago melk"), re.compile("^Melkesjokolade"),
        re.compile("^Melkeglede"), re.compile("«melk»")])
    expect(page.get_by_role("option").last).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Enter")
    assert list_names(page) == ["Melk"]
    # ... and then offers the names that have prices: kinds of milk, the commoner first
    expect(page.locator(".list-item .did-you-mean").first).to_have_text("Lettmelk")

    # A name that is known is added as it is
    add.fill("kokosmelk")
    page.keyboard.press("Enter")
    assert list_names(page) == ["Melk", "Kokosmelk"]
    # A whole word in a name ranks above the start of one, but is still only a suggestion
    add.fill("smør")
    expect(page.get_by_role("option").first).to_have_text(re.compile("^Smør og olivenolje"))
    page.keyboard.press("Enter")
    assert list_names(page) == ["Melk", "Kokosmelk", "Smør"]


def test_the_total_bar_goes_when_the_list_is_cleared(browser, site):
    context = browser.new_context(base_url=site[0], viewport={"width": 390, "height": 700})
    page = context.new_page()
    page.goto("/handleliste.html?v=Lettmelk&v=Kj%C3%B8ttdeig&v=Agurk&v=Seigmenn")
    expect(page.locator("#bar")).to_be_visible()
    page.get_by_role("button", name="Tøm listen").click()
    expect(page.locator("#bar")).to_be_hidden()
    context.close()


def test_an_old_cheapest_price_is_not_called_the_price_now(page):
    # The real case: "Billigst nå" for a price from February 2025
    def old(route):
        product = route.fetch().json()
        for p in product["prices"]:
            p["purchase_date"] = "2025-02-15"
        route.fulfill(json=product)
    page.route("**/products/Lettmelk", old)
    page.goto("/produkt.html?navn=Lettmelk")
    expect(page.locator("#best .label")).to_have_text("Billigst sist vi så")
    expect(page.locator("#best-unit")).to_contain_text("15. feb. 2025")


def test_stores_selling_different_sizes_lead_with_the_unit_price(page):
    # The real case: Kiwi's 1,75 l for 29,90 kr is cheapest, but looked dearer
    # than Meny's 1 l for 21,90 kr
    def sizes(route):
        product = route.fetch().json()
        first = product["prices"][0]
        product["prices"] = [
            {**first, "store_chain": "Meny", "quantity": 1000, "total_price": 21.9, "price_per_unit": 2.19},
            {**first, "store_chain": "Kiwi", "quantity": 1750, "total_price": 29.9, "price_per_unit": 1.71},
        ]
        route.fulfill(json=product)
    page.route("**/products/Lettmelk", sizes)
    page.goto("/produkt.html?navn=Lettmelk")
    expect(page.locator("#best-chain img")).to_have_attribute("alt", "Kiwi")
    expect(page.locator("#best-price")).to_have_text("1,71 kr / 100 ml")
    expect(page.locator("#best-unit")).to_have_text(re.compile(r"^29,90 kr for 1,75 l · "))


def test_one_store_is_not_called_the_cheapest(page):
    page.goto("/produkt.html?navn=Kj%C3%B8ttdeig")
    expect(page.locator(".store")).to_have_count(1)
    expect(page.locator(".store .amount")).not_to_contain_text("billigst")
    expect(page.locator("#best .label")).to_have_text("Pris nå")


def test_a_price_per_item_is_not_repeated_as_a_unit_price(page):
    def per_item(route):
        product = route.fetch().json()
        for p in product["prices"]:
            p.update(unit="stk", quantity=None, price_per_unit=p["total_price"])
        route.fulfill(json=product)
    page.route("**/products/Kj%C3%B8ttdeig", per_item)
    page.goto("/produkt.html?navn=Kj%C3%B8ttdeig")
    expect(page.locator("#best-price")).to_have_text("89 kr")
    expect(page.locator("#best-unit")).not_to_contain_text("/ stk")


def test_a_page_seen_before_folds_its_introduction(page):
    page.goto("/")
    expect(page.locator(".intro .lede")).to_be_visible()
    page.goto("/tilbud.html")
    expect(page.locator(".intro .lede")).to_be_visible()  # each page explains itself once
    page.goto("/")
    expect(page.locator(".intro")).to_have_class(re.compile("compact"))
    expect(page.locator(".intro .lede")).to_be_hidden()
    expect(page.locator(".intro .actions")).to_be_hidden()
    expect(page.locator("header .cta")).to_be_visible()  # uploading is still a tap away
    page.goto("/tilbud.html")
    expect(page.locator(".intro .lede")).to_be_hidden()


def test_explore_shows_products_a_page_at_a_time(page):
    def many(route):
        first = route.fetch().json()[0]
        route.fulfill(json=[{**first, "canonical_name": f"Vare {n:03d}"} for n in range(130)])
    page.route("**/products", many)
    page.goto("/")
    expect(cards(page)).to_have_count(48)
    expect(page.locator("#count")).to_have_text("130 produkter")
    page.get_by_role("button", name="Vis flere (82 til)").click()
    expect(cards(page)).to_have_count(96)
    # A search starts from the first page again
    page.locator("#search").fill("vare 1")
    expect(cards(page)).to_have_count(30)  # Vare 100-129
    expect(page.locator("#more")).to_be_hidden()


def test_a_tap_on_a_suggestion_adds_it_even_when_the_field_loses_focus_first(page):
    # On a phone a tap is pointerdown, then focus leaving the field, then click.
    # The list used to be hidden on that blur, and the click fell through to
    # the product link underneath: the tap opened the product page instead.
    page.goto("/handleliste.html?v=Lettmelk&v=Agurk")
    page.locator("#add").click()
    page.keyboard.type("kjø")
    option = page.locator("#suggestions li").first
    expect(option).to_contain_text("Kjøttdeig")
    option.dispatch_event("pointerdown")
    page.evaluate("document.getElementById('add').blur()")
    expect(option).to_be_visible()
    option.click()
    expect(page).to_have_url(re.compile(r"/handleliste\.html"))  # not on a product page
    expect(page.locator("#items .list-item")).to_have_count(3)
    expect(page.locator("#items li").last).to_contain_text("Kjøttdeig")
    # And the field is ready for the next one
    expect(page.locator("#add")).to_be_focused()


def test_a_suggestion_can_be_added_many_times_in_a_row(page):
    page.goto("/handleliste.html")
    page.locator("#add").click()
    for word, name in [("lett", "Lettmelk"), ("kjø", "Kjøttdeig"), ("agu", "Agurk")]:
        page.keyboard.type(word)  # the field is still focused after each one
        page.locator("#suggestions li").filter(has_text=name).first.click()
    expect(page.locator("#items .list-item")).to_have_count(3)
    expect(page).to_have_url(re.compile(r"/handleliste\.html$"))


def test_suggested_products_are_added_with_one_tap_and_the_page_stays(page):
    page.goto("/handleliste.html?v=Lettmelk")
    chips = page.locator("#pick-list button")
    expect(chips.first).to_be_visible()
    # What's on offer comes first, and what's on the list is not suggested
    expect(page.locator("#pick-list button.offer").first).to_be_visible()
    expect(page.locator("#pick-list").get_by_text("Lettmelk")).to_have_count(0)
    first = chips.first.get_attribute("aria-label")
    name = first.removeprefix("Legg ").removesuffix(" på handlelisten")
    before = chips.count()
    chips.first.click()
    expect(page).to_have_url(re.compile(r"/handleliste\.html"))
    expect(page.locator("#items .list-item")).to_have_count(2)
    expect(page.locator("#items")).to_contain_text(name)
    expect(page.locator("#pick-list").get_by_label(first)).to_have_count(0)
    # It's gone from the suggestions (the seed has fewer than a dozen products)
    expect(chips).to_have_count(before - 1)
    # The suggested panel answers to the keyboard like any button
    chips.first.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#items .list-item")).to_have_count(3)


# Dinners (the page, with /meals answered by a fixed set)

def a_dinner(id, name, kind="kjøtt", per=20.0, offers=0, saving=0.0, minutes=30, rough=False, ends=None, compared=2):
    at = [{"chain": "Kiwi", "total": per * 2, "per_portion": per}, {"chain": "Meny", "total": per * 2 + 10, "per_portion": per + 5}]
    return {"id": id, "name": name, "blurb": f"{name} er god.", "minutes": minutes, "kind": kind, "portions": 2, "chain": "Kiwi",
            "total": per * 2, "per_portion": per, "compare": at[:compared], "compared": compared, "buy_total": per * 3, "saving": saving, "offers": offers, "rough": rough,
            "ingredients": [{"label": "400 g kjøttdeig", "product": "Kjøttdeig", "count": 1, "price": per, "estimated": False,
                             "offer": bool(offers), "offer_until": ends, "deal": None, "brand": None},
                            {"label": "Ris", "product": "Ris", "count": 1, "price": per, "estimated": rough, "offer": False,
                             "offer_until": None, "deal": None, "brand": None}],
            "unpriced": [], "pantry": ["salt", "olje"], "steps": ["Stek det.", "Server det."]}


DINNERS = [a_dinner("bolo", "Bolognese", per=18, offers=2, saving=12, minutes=35, ends=str(date.today() + timedelta(days=1))), a_dinner("pizza", "Pizza", "svin", per=24, minutes=25),
           a_dinner("laks", "Laks", "fisk", per=40, offers=1, saving=4, compared=1), a_dinner("linser", "Linsegryte", "vegetar", per=11, minutes=20)]


def plan_of(ids, portions=2):
    chosen = [d for d in DINNERS if d["id"] in ids]
    return {"portions": portions, "chain": "Kiwi", "total": 300.0, "per_portion": 15.0, "compared": 2,
            "compare": [{"chain": "Kiwi", "total": 300.0, "per_portion": 15.0}, {"chain": "Meny", "total": 342.0, "per_portion": 17.1}],
            "shared_saving": 41.0, "offer_saving": 12.0,
            "rough": False, "used_total": 240.0, "used_per_portion": 12.0, "days": [{"day": ["Mandag", "Tirsdag", "Onsdag"][i], "id": d["id"], "name": d["name"], "minutes": d["minutes"],
                                      "kind": d["kind"], "per_portion": d["per_portion"], "offers": d["offers"],
                                      "alternatives": [x["id"] for x in DINNERS if x["id"] not in ids]} for i, d in enumerate(chosen)],
            "shopping": [{"product": "Kjøttdeig", "count": 2, "price": 120.0, "size": "400 g", "estimated": False, "offer": True,
                          "used_in": ["Bolognese", "Pizza"]},
                         {"product": "Ris", "count": 1, "price": 30.0, "size": "1000 g", "estimated": False, "offer": False,
                          "used_in": ["Bolognese"]}]}


@pytest.fixture
def dinners_page(page):
    asked = []

    def answer(route):
        url = route.request.url
        asked.append(url)
        ids = re.search(r"ids=([^&]*)", url)
        wanted = ids[1].split(",") if ids else ["bolo", "pizza", "linser"]
        route.fulfill(json={"portions": 2, "recipes": DINNERS, "plan": plan_of(wanted)})
    page.route("**/meals*", answer)
    page.goto("/middag.html")
    expect(page.locator("#grid .card").first).to_be_visible()
    page.asked = asked
    return page


def test_the_dinners_page_shows_a_plan_for_the_week_and_every_dinner_cheapest_first(dinners_page):
    page = dinners_page
    expect(page.locator("#plan-title")).to_have_text("3 middager for 300 kr")
    expect(page.locator("#plan-days .plan-name")).to_have_text(["Bolognese", "Pizza", "Linsegryte"])
    expect(page.locator("#plan-days .plan-weekday")).to_have_text(["Mandag", "Tirsdag", "Onsdag"])
    expect(page.locator("#plan-facts")).to_contain_text("Delte pakker sparer 41 kr. Tilbud sparer 12 kr.")
    expect(page.locator("#grid .card h3")).to_have_text(["Bolognese", "Pizza", "Laks", "Linsegryte"])  # as the server ordered them


def test_a_dinner_says_what_it_costs_at_the_chains_it_was_compared_at(dinners_page):
    page = dinners_page
    pizza = page.locator("#grid .card").filter(has_text="Pizza")
    # Cheapest only of the chains with enough prices, and the others right there
    expect(pizza.locator(".where")).to_contain_text("billigst av 2 butikker med nok priser")
    expect(pizza.locator(".meal-compare")).to_have_text("Kiwi 24 kr · Meny 29 kr per porsjon")
    laks = page.locator("#grid .card").filter(has_text="Laks")
    expect(laks.locator(".where")).to_contain_text("eneste butikk med nok priser")
    expect(laks.locator(".meal-compare")).to_have_count(0)
    expect(page.locator("#plan-sub")).to_contain_text("billigst av 2 butikker med nok priser")
    expect(page.locator("#plan-sub .meal-compare")).to_have_text("Kiwi 300 kr · Meny 342 kr")


def test_dinners_with_offers_get_their_own_row_with_the_saving(dinners_page):
    page = dinners_page
    # Only dinners with at least two offers and a real saving
    expect(page.locator("#deal-grid .card h3")).to_have_text(["Bolognese"])
    expect(page.locator("#deal-grid .verdict-tag")).to_have_text("2 på tilbud · spar 12 kr")


def test_a_dinner_unfolds_into_ingredients_and_steps_and_goes_on_the_list(dinners_page):
    page = dinners_page
    card = page.locator("#grid .card").filter(has_text="Pizza")
    card.locator("summary").click()
    expect(card.locator(".ingredients li")).to_have_count(2)
    expect(card.locator(".steps li")).to_have_text(["Stek det.", "Server det."])
    expect(card).to_contain_text("Fra skapet: salt, olje.")
    card.get_by_role("button", name="Legg på handlelisten").click()
    expect(card.locator(".status")).to_contain_text("2 varer lagt til")
    assert page.evaluate("JSON.parse(localStorage.getItem('sjekka.handleliste')).map(i => i.name)") == ["Kjøttdeig", "Ris"]
    expect(page.locator("[data-list-count]")).to_have_text("2")


def test_the_whole_week_goes_on_the_list_in_whole_packages(dinners_page):
    page = dinners_page
    page.get_by_role("button", name=re.compile("Legg alt på handlelisten")).click()
    expect(page.locator("#plan-status")).to_have_text("2 varer lagt til.")
    expect(page.locator("#plan-list")).to_be_visible()
    assert page.evaluate("JSON.parse(localStorage.getItem('sjekka.handleliste'))") == [
        {"name": "Kjøttdeig", "count": 2}, {"name": "Ris", "count": 1}]
    page.locator("#plan-shopping summary").click()
    expect(page.locator("#shopping li").first).to_contain_text("2 × Kjøttdeig (400 g)")
    expect(page.locator("#shopping li").first).to_contain_text("Bolognese, Pizza")


def test_a_dinner_in_the_plan_can_be_swapped_for_another(dinners_page):
    page = dinners_page
    swap = page.get_by_role("button", name="Bytt Tirsdag: Pizza")
    expect(swap).to_have_attribute("aria-expanded", "false")
    swap.click()
    options = page.locator('li[data-id="pizza"] .swap-list button')
    expect(options).to_have_text(["Laks · 40 kr"])
    options.first.click()
    expect(page.locator("#plan-days .plan-name")).to_have_text(["Bolognese", "Laks", "Linsegryte"])
    assert "ids=bolo,laks,linser" in page.asked[-1] and "portions=2" in page.asked[-1]
    # Focus stays with the person, on the day they changed
    expect(page.get_by_role("button", name=re.compile("^Bytt Tirsdag"))).to_be_focused()


def test_the_number_of_portions_is_asked_for_and_remembered(dinners_page):
    page = dinners_page
    expect(page.locator("#portions button[aria-pressed=true]")).to_have_text("2")
    page.locator("#portions button", has_text="4").click()
    expect(page.locator("#portions button[aria-pressed=true]")).to_have_text("4")
    assert "portions=4" in page.asked[-1] and "ids" not in page.asked[-1]
    assert page.evaluate("localStorage.getItem('sjekka.porsjoner')") == "4"
    page.reload()
    expect(page.locator("#portions button[aria-pressed=true]")).to_have_text("4")


def test_dinners_can_be_filtered_by_kind_and_time(dinners_page):
    page = dinners_page
    page.locator("#kinds button", has_text="Fisk").click()
    expect(page.locator("#grid .card h3")).to_have_text(["Laks"])
    page.locator("#kinds button", has_text="Kjøtt").click()  # pork is meat
    expect(page.locator("#grid .card h3")).to_have_text(["Bolognese", "Pizza"])
    page.locator("#kinds button", has_text="Alle").click()
    page.get_by_label("Under 30 min").check()  # 30 counts: only the 35-minute bolognese goes
    expect(page.locator("#grid .card h3")).to_have_text(["Pizza", "Laks", "Linsegryte"])
    page.locator("#kinds button", has_text="Kylling").click()
    expect(page.locator("#none")).to_be_visible()


def test_a_dinner_in_the_plan_opens_its_recipe_even_when_the_list_is_filtered(dinners_page):
    page = dinners_page
    page.locator("#kinds button", has_text="Fisk").click()
    page.locator("#plan-days .plan-name", has_text="Bolognese").click()
    card = page.locator("#meal-bolo")
    expect(card).to_be_visible()
    expect(card.locator("details")).to_have_attribute("open", "")


def test_the_dinners_page_says_so_when_there_are_no_prices_to_go_on(page):
    page.route("**/meals*", lambda route: route.fulfill(json={"portions": 2, "recipes": [], "plan": None}))
    page.goto("/middag.html")
    expect(page.locator("#notice")).to_contain_text("ikke nok priser")
    expect(page.locator("#plan")).to_be_hidden()
    expect(page.locator("#grid .card")).to_have_count(0)


def test_the_dinners_page_says_so_when_prices_cannot_be_fetched(page):
    page.route("**/meals*", lambda route: route.fulfill(status=500, body="no"))
    page.goto("/middag.html")
    expect(page.locator("#notice")).to_contain_text("Kunne ikke hente middager")


def test_the_dinners_page_is_in_every_menu(page):
    for path in ["/", "/tilbud.html", "/handleliste.html", "/om.html"]:
        page.goto(path)
        expect(page.get_by_role("navigation").get_by_role("link", name="middag")).to_be_visible()


def test_a_dinner_on_offer_says_when_the_offer_ends(dinners_page):
    expect(dinners_page.locator("#meal-bolo")).to_contain_text("Tilbud t.o.m. i morgen")
    expect(dinners_page.locator("#meal-pizza")).not_to_contain_text("Tilbud t.o.m.")


def test_the_plan_says_what_the_food_uses_when_the_packages_bought_cost_more(dinners_page):
    # 240 kr of food in 300 kr of packages: the leftovers are explained, not left to look like a mistake
    expect(dinners_page.locator("#plan-sub")).to_contain_text("Maten bruker 12 kr per porsjon. Resten av innkjøpet er pakker som holder til senere")
    expect(dinners_page.locator("#plan-facts")).to_contain_text("lag hele oppskriften og ta med rester")  # for two


def test_guessed_prices_in_the_plan_invite_a_receipt(page):
    def rough(route):
        plan = plan_of(["bolo", "pizza"], portions=4)
        plan["rough"] = True
        plan["used_total"] = plan["total"]
        route.fulfill(json={"portions": 4, "recipes": DINNERS, "plan": plan})
    page.route("**/meals*", rough)
    page.goto("/middag.html")
    expect(page.locator("#plan-title")).to_contain_text("ca. 300 kr")
    expect(page.locator("#plan-sub")).not_to_contain_text("Maten bruker")
    expect(page.locator("#plan-facts")).to_contain_text("anslått")
    expect(page.locator("#plan-facts").get_by_role("link", name="Last opp en kvittering")).to_have_attribute("href", "/upload.html")
    expect(page.locator("#plan-facts")).not_to_contain_text("ta med rester")  # for four


def test_the_number_of_dinners_is_chosen_and_remembered(dinners_page):
    page = dinners_page
    expect(page.locator("#days button[aria-pressed=true]")).to_have_text("5")
    page.locator("#days button", has_text="3").click()
    expect(page.locator("#days button[aria-pressed=true]")).to_have_text("3")
    assert "days=3" in page.asked[-1]
    assert page.evaluate("localStorage.getItem('sjekka.middager')") == "3"
    page.reload()
    expect(page.locator("#days button[aria-pressed=true]")).to_have_text("3")


def test_the_phone_menu_has_room_for_every_page(browser, site):
    # Five things in one row once left "sjekka" touching "tilbud" and hid "utforsk"
    for width in (320, 360, 412):
        context = browser.new_context(base_url=site[0], viewport={"width": width, "height": 700}, has_touch=True)
        page = context.new_page()
        page.goto("/handleliste.html?v=Lettmelk&v=Agurk&v=Seigmenn&v=Kj%C3%B8ttdeig&v=Smør&v=Egg&v=Brød&v=Ost&v=Skinke&v=Salat")
        links = page.locator(".top nav a:not(.cta)")
        expect(links).to_have_text(["utforsk", "tilbud", "middag", re.compile("^handleliste")])
        boxes = [links.nth(i).bounding_box() for i in range(links.count())]
        assert all(b["height"] >= 40 for b in boxes), width  # easy to tap
        for left, right in zip(boxes, boxes[1:], strict=False):
            assert right["x"] - (left["x"] + left["width"]) >= 12, (width, "links touch")
        assert boxes[-1]["x"] + boxes[-1]["width"] <= width - 16, (width, "runs off the edge")
        top = page.locator(".top .mark").bounding_box(), page.locator(".top .cta").bounding_box()
        assert top[0]["y"] < boxes[0]["y"] and top[1]["y"] < boxes[0]["y"], width  # logo and button above the links
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
        context.close()
