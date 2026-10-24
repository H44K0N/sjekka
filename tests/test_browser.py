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
                                  "/handleliste.html", "/handleliste.html?v=Lettmelk~2&v=Kj%C3%B8ttdeig&v=Safran",
                                  "/tilbud.html", "/produkt.html?navn=Seigmenn"])
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
    milk = cards(page).filter(has_text="Lettmelk")
    expect(milk.locator(".price")).to_have_text("24,90 kr")
    expect(milk.locator(".unit")).to_have_text("2,49 kr / 100 ml")
    expect(milk.locator(".brand")).to_have_text("Tine · 1 l")
    expect(milk.locator(".where .chain img")).to_have_attribute("alt", "Kiwi")
    expect(milk.locator(".where")).to_contain_text("i dag")


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
        describe({brand: "Gilde", quantity: 6, unit: "stk"}),
    ]""")
    assert described == ["Grandiosa · Hjemmelaget · 1 kg", "Tine · 1,5 l", "Løsvekt · 1,57 kg", "Farris · Lime 0,5 l", "Gilde"]


def test_product_page_says_the_size_of_each_price(page):
    page.goto("/produkt.html?navn=Lettmelk")
    expect(page.locator(".store").nth(0).locator(".brand")).to_have_text("Q · 1 l")
    page.locator(".history summary").click()
    expect(page.locator("#history tr").first).to_contain_text("Tine · 1 l")


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
    page.fill("#search", "tine")
    expect(cards(page)).to_have_count(1)
    expect(cards(page).first).to_contain_text("Lettmelk")
    page.fill("#search", "KJØTT")
    expect(cards(page)).to_have_count(1)
    page.fill("#search", "finnes ikke")
    expect(cards(page)).to_have_count(0)
    expect(page.locator("#notice")).to_have_text("Ingen produkter passer til søket.")


def test_category_filter(page):
    page.goto("/")
    page.get_by_role("button", name="Meieri", exact=True).click()
    expect(cards(page)).to_have_count(1)
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
    for path in ["/", "/upload.html", "/takk.html", "/produkt.html?navn=Lettmelk", "/handleliste.html?v=Lettmelk&v=Agurk", "/tilbud.html"]:
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
    # One item each: <img…> 10,00 · Seigmenn 38,70/3 = 12,90 · Agurk 30/2 = 15,00 · Lettmelk 24,90 · Kjøttdeig 89,00
    assert card_names(page) == ['<img src=x onerror="window.pwned=1">', "Seigmenn", "Agurk", "Lettmelk", "Kjøttdeig"]
    expect(page.locator("#sort-current")).to_have_text("lavest pris")
    sort_by(page, "Nyest")
    assert card_names(page)[-1] == "Agurk"  # 40 days old


def test_view_is_kept_in_the_url(page):
    page.goto("/")
    page.get_by_role("button", name="Meieri", exact=True).click()
    sort_by(page, "Lavest pris")
    page.fill("#search", "melk")
    expect(page).to_have_url(re.compile(r"\?q=melk&kategori=Meieri&sorter=pris$"))

    page.goto("/?kategori=Kj%C3%B8tt&sorter=nyest")
    expect(cards(page)).to_have_count(1)
    expect(page.get_by_role("button", name="Kjøtt", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#sort-current")).to_have_text("nyest")


def test_unknown_category_in_url_falls_back_to_all(page):
    page.goto("/")
    expect(cards(page).first).to_be_visible()
    total = cards(page).count()
    page.goto("/?kategori=Finnes%20ikke")
    expect(cards(page)).to_have_count(total)
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
    for path in ["/", "/upload.html", "/produkt.html?navn=Lettmelk", "/personvern.html"]:
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
    button = page.get_by_role("button", name="Legg på handlelisten")
    button.click()
    expect(button).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#list-link")).to_be_visible()
    page.locator("#list-link").click()
    page.wait_for_url("**/handleliste.html")
    assert list_names(page) == ["Kjøttdeig"]


def test_list_finds_the_cheapest_store_for_the_whole_list(page):
    page.goto("/handleliste.html")
    expect(page.locator("#empty")).to_be_visible()
    add = page.get_by_role("combobox", name="Legg til vare")
    add.fill("melk")
    expect(page.get_by_role("option").first).to_have_text(re.compile("^Lettmelk"))
    page.keyboard.press("Enter")
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
    page.get_by_role("combobox", name="Legg til vare").fill("safran")
    expect(page.get_by_role("option")).to_have_text(re.compile("«safran»"))
    page.keyboard.press("Enter")
    expect(page.locator(".list-item", has_text="Safran").locator(".hint")).to_have_text("ingen priser ennå")
    expect(page.locator("#notice")).to_contain_text("Ingen av varene har priser ennå")
    expect(page.locator("#verdict")).to_be_hidden()

    page.get_by_role("combobox", name="Legg til vare").fill("agurk")
    page.keyboard.press("Enter")
    expect(page.locator("#unknown")).to_contain_text("Safran")
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
    # An empty list just takes the shared one, and the link is tidied away
    expect(page.locator(".list-item")).to_have_count(2)
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
    expect(page.locator("#shared-text")).to_contain_text("1 vare")
    page.get_by_role("button", name="Legg til i min liste").click()
    expect(page.locator("#shared")).to_be_hidden()
    assert list_names(page) == ["Lettmelk", "Agurk", "Kjøttdeig"]

    page.goto("/handleliste.html?v=Agurk")
    page.get_by_role("button", name="Bruk den delte").click()
    assert list_names(page) == ["Agurk"]


def test_phone_shows_the_total_while_the_result_is_out_of_sight(browser, site):
    context = browser.new_context(base_url=site[0], viewport={"width": 390, "height": 700})
    page = context.new_page()
    page.goto("/handleliste.html?v=Lettmelk&v=Kj%C3%B8ttdeig&v=Agurk&v=Seigmenn")
    sticky = page.locator("#sticky")
    expect(sticky).to_be_visible()
    expect(sticky).to_contain_text("kr")
    page.locator("#verdict").scroll_into_view_if_needed()
    expect(sticky).to_be_hidden()
    context.close()


# Flyer offers

def test_offers_page_judges_each_offer(page):
    page.goto("/tilbud.html")
    offers = page.locator(".card")
    expect(offers).to_have_count(2)
    candy = offers.first
    expect(candy.locator("h3")).to_have_text("Seigmenn")
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
