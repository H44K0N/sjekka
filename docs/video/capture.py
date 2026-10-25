"""Captures the real app, on demo data, as sharp stills for the films.

    python capture.py <base url> desktop|mobile <out dir>

The films don't record the screen: they move these stills, so camera moves
are smooth and text stays sharp when the camera zooms in. Each state of a
page (the list before and after an item is added, an offer with its details
open) is one full-page screenshot, at two or three times the pixels shown,
and index.json says where the elements the films point at are, in CSS
pixels from the top of the page:

    {"home-0": {"width": 1440, "height": 2532, "scale": 2,
                "boxes": {"add-Kjøttdeig": [x, y, w, h], ...}}, ...}

Desktop is a 1440x900 window, the phone a 390x844 screen. Run against a
fresh demo server (demo_server.py): the demo receipt is uploaded once."""
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

BASE, FORMAT, OUT = sys.argv[1], sys.argv[2], Path(sys.argv[3])
PHONE = FORMAT == "mobile"
VIEWPORT = {"width": 390, "height": 844} if PHONE else {"width": 1440, "height": 900}
SCALE = 3 if PHONE else 2
RECEIPT = Path(__file__).parent / "receipt.jpg"
# What goes on the list, in order, from the explore page's cards
SHOPPING = ["Kjøttdeig", "Lettmelk", "Laksefilet", "Grovbrød", "Bananer", "Gulost"]

BOXES = """(named) => {
  const box = el => { const r = el.getBoundingClientRect();
    return [Math.round(r.x + scrollX), Math.round(r.y + scrollY), Math.round(r.width), Math.round(r.height)]; };
  const out = {};
  for (const [key, selector] of Object.entries(named)) {
    const all = [...document.querySelectorAll(selector)].filter(e => e.getClientRects().length);
    if (all.length === 1) out[key] = box(all[0]);
    else all.forEach((e, i) => { out[`${key}.${i}`] = box(e); });
  }
  return out;
}"""

index = {}


def snap(page, state, named):
    """A full-page still of the page as it is, and its elements' boxes."""
    page.wait_for_load_state("networkidle")
    # Let transitions (a toggled button, a folding panel) finish first
    page.wait_for_timeout(700)
    page.evaluate("window.scrollTo(0, 0)")
    path = OUT / f"{state}.png"
    page.screenshot(path=str(path), full_page=True, animations="disabled", caret="hide")
    size = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.scrollHeight]")
    index[state] = {"width": size[0], "height": size[1], "scale": SCALE, "boxes": page.evaluate(BOXES, named)}
    print(f"{state:14} {size[0]}x{size[1]}  {len(index[state]['boxes'])} boxes")


def card(name):
    return f'article.card:has(h3 a[href$="navn={quote(name)}"])'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)
        context = browser.new_context(base_url=BASE, viewport=VIEWPORT, device_scale_factor=SCALE,
                                      locale="nb-NO", is_mobile=PHONE, has_touch=PHONE)
        # Every intro shown in full, as to a first-time visitor
        context.add_init_script("""try { if (!sessionStorage.getItem("__fresh")) {
            localStorage.clear(); sessionStorage.setItem("__fresh", "1"); } } catch {}""")
        page = context.new_page()
        page.goto("/")

        nav = {"nav-list": ".top nav a[href='/handleliste.html']", "nav-count": "[data-list-count]",
               "nav-offers": ".top nav a[href='/tilbud.html']", "nav-upload": ".top nav a.cta"}
        home = {**nav, "hero": ".intro h1", "search": "#search", "grid": "#grid", "cards": "article.card",
                **{f"card-{n}": card(n) for n in SHOPPING}, **{f"add-{n}": f"{card(n)} .list-toggle" for n in SHOPPING},
                **{f"price-{n}": f"{card(n)} .price" for n in SHOPPING}}
        snap(page, "home-0", home)
        for i, name in enumerate(SHOPPING, 1):
            page.locator(f"{card(name)} .list-toggle").click()
            snap(page, f"home-{i}", home)
        # The intro is folded on a second visit: keep the first-visit page
        page.evaluate("for (const k of Object.keys(localStorage)) if (k.startsWith('sjekka.sett')) localStorage.removeItem(k)")

        page.goto("/handleliste.html")
        page.locator("#ranking .rank-row").first.wait_for()
        listed = {**nav, "title": "main h1", "add": "#add", "items": "#items .list-item", "verdict": "#verdict",
                  "verdict-total": "#verdict-total", "verdict-chain": "#verdict-chain", "verdict-note": "#verdict-note",
                  "ranking": "#ranking", "rows": "#ranking .rank-row", "bars": "#ranking .gap-bar", "split": "#split"}
        snap(page, "list", listed)
        page.locator("#ranking .rank-row").first.click()
        snap(page, "list-open", {**listed, "detail": ".rank-detail"})

        page.goto("/tilbud.html")
        page.locator("article.card").first.wait_for()
        offers = {**nav, "title": "main h1", "cards": "article.card", "real": "article.card.real",
                  "real-tag": "article.card.real .verdict-tag", "real-details": "article.card.real details summary",
                  "tags": "article.card .verdict-tag"}
        snap(page, "offers", offers)
        page.locator("article.card.real details summary").first.click()
        snap(page, "offers-open", {**offers, "real-verdict": "article.card.real .deal-verdict"})

        page.goto("/produkt.html?navn=Kjøttdeig")
        page.locator("#stores .store").first.wait_for()
        snap(page, "product", {**nav, "title": "#name", "best": "#best", "best-price": "#best-price",
                               "add": "#list-button", "offers": "#offers-now", "stores": "#stores .store",
                               "sparks": "#stores .spark", "section": "#stores-section"})

        page.goto("/upload.html")
        upload = {**nav, "title": "main h1", "drop": "#drop", "button": "#btn", "privacy": "#privacy"}
        snap(page, "upload", upload)
        page.set_input_files("#receipt", str(RECEIPT))
        page.wait_for_timeout(800)
        snap(page, "upload-file", {**upload, "file": "#file"})
        page.locator("#btn").click()
        page.wait_for_url("**/takk.html*")
        snap(page, "thanks-wait", {**nav, "title": "main h1", "progress": "#progress", "state": "#state"})
        page.locator("#items li").first.wait_for(timeout=30000)
        snap(page, "thanks", {**nav, "title": "main h1", "found": "#found", "lines": "#items li", "state": "#state"})

        context.close()
        browser.close()
    (OUT / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))


main()
