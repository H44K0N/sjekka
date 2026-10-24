"""Records the short promo videos for the about page: what Sjekka does, at
a glance, with big captions instead of a voice.

    python promo.py <base url> desktop   # 1920x1080, a mouse pointer
    python promo.py <base url> mobile    # 1080x1920, a round touch dot

Like record.py, the phone version shows the site in a phone-width iframe
scaled up, since Playwright records CSS pixels. Prints the raw video's path."""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
BASE, MODE = sys.argv[1], sys.argv[2]
PHONE = MODE == "mobile"
OUT_DIR = HERE / "raw"

PHONE_WIDTH, PHONE_HEIGHT = 405, 720
PHONE_PAGE = f"""<!DOCTYPE html><html><head><style>
  html, body {{ margin: 0; background: #0d0c0b; overflow: hidden; }}
  iframe {{ position: absolute; left: 0; top: 0; width: {PHONE_WIDTH}px; height: {PHONE_HEIGHT}px; border: 0;
    transform: scale({1080 / PHONE_WIDTH}); transform-origin: 0 0; }}
</style></head><body><iframe name="app" src="about:blank"></iframe></body></html>"""

ARROW = ("data:image/svg+xml," + "%3Csvg xmlns='http://www.w3.org/2000/svg' width='26' height='34' viewBox='0 0 26 34'%3E"
         "%3Cpath d='M2 2 L2 27 L8.5 21 L13 31 L17 29 L12.5 19.5 L21 19.5 Z' fill='%23ece6dc' stroke='%230d0c0b' "
         "stroke-width='2' stroke-linejoin='round'/%3E%3C/svg%3E")

# Sizes in video pixels; desktop pages are zoomed 1.5x, so their overlay is divided back
# The desktop site is shown zoomed 2x (laid out at 960 pixels): bigger and
# easier to read in a short clip than the walkthrough's 1.5x
ZOOM = 1 if PHONE else 2
CAPTION = "66px" if PHONE else "58px"
CURSOR = ("""width: 56px; height: 56px; margin: -28px 0 0 -28px; border-radius: 50%;
    background: rgba(236,230,220,0.85); border: 4px solid #0d0c0b; box-shadow: 0 0 0 2px rgba(236,230,220,0.5);"""
          if PHONE else f"""width: 26px; height: 34px; background: url("{ARROW}") no-repeat;
    filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5)); transform-origin: 2px 2px;""")

OVERLAY = """
(() => {
  if (window.top !== window || location.pathname.startsWith("/__card")) return;
  const Z = %(zoom)s;
  if (Z !== 1) document.addEventListener("DOMContentLoaded", () => { document.documentElement.style.zoom = String(Z); });
  const css = `
    #__scrim { position: fixed; left: 0; right: 0; bottom: 0; height: %(scrim)s; z-index: 99998; pointer-events: none;
      background: linear-gradient(to top, rgba(13,12,11,0.97) 35%%, rgba(13,12,11,0)); opacity: 0; transition: opacity 400ms; }
    #__cap { position: fixed; left: 0; right: 0; bottom: %(bottom)s; z-index: 99999; padding: 0 6vw; text-align: center;
      font: 600 %(caption)s/1.15 "Libron", Georgia, serif; letter-spacing: -0.01em; color: #ece6dc; pointer-events: none; }
    #__cap span { display: inline-block; opacity: 0; transform: translateY(24px); transition: opacity 450ms, transform 600ms cubic-bezier(.2,.8,.2,1); }
    #__cap span.on { opacity: 1; transform: none; }
    #__cap em { font-style: normal; color: #c9b48f; }
    #__cur { position: fixed; z-index: 100000; %(cursor)s
      transition: left 520ms cubic-bezier(.3,.7,.2,1), top 520ms cubic-bezier(.3,.7,.2,1), transform 120ms; pointer-events: none; }
    #__cur.down { transform: scale(%(press)s); }`;
  function ensure() {
    if (document.getElementById("__cap") || !document.body) return;
    const s = document.createElement("style"); s.textContent = css; document.head.append(s);
    const scrim = document.createElement("div"); scrim.id = "__scrim"; document.body.append(scrim);
    const c = document.createElement("div"); c.id = "__cap"; document.body.append(c);
    const k = document.createElement("div"); k.id = "__cur"; k.style.left = (window.__cx ?? -80) / Z + "px"; k.style.top = (window.__cy ?? -80) / Z + "px"; document.body.append(k);
  }
  window.__caption = html => { ensure(); const c = document.getElementById("__cap"), s = document.getElementById("__scrim");
    if (!html) { c.querySelector("span")?.classList.remove("on"); s.style.opacity = 0; return; }
    c.innerHTML = `<span>${html}</span>`; s.style.opacity = 1; requestAnimationFrame(() => requestAnimationFrame(() => c.firstChild.classList.add("on"))); };
  window.__move = (x, y) => { ensure(); const k = document.getElementById("__cur"); k.style.left = x / Z + "px"; k.style.top = y / Z + "px"; };
  window.__press = on => { ensure(); document.getElementById("__cur").classList.toggle("down", on); };
  window.__cursor = (x, y) => { window.__cx = x; window.__cy = y; };
  document.addEventListener("DOMContentLoaded", ensure);
})();
""" % dict(zoom=ZOOM, caption=f"calc({CAPTION} / {ZOOM})", bottom=f"calc({'150px' if PHONE else '64px'} / {ZOOM})",
           scrim=f"calc({'560px' if PHONE else '300px'} / {ZOOM})", cursor=CURSOR, press=0.7 if PHONE else 0.88)

# The opening and closing cards, in the site's own type and colours
CARD = """<!DOCTYPE html><html lang="nb"><head><meta charset="utf-8"><style>
  @font-face { font-family: "Libron"; src: url("/fonts/Libron-Regular.woff2") format("woff2"); font-weight: 400; }
  @font-face { font-family: "Libron"; src: url("/fonts/Libron-Bold.woff2") format("woff2"); font-weight: 700; }
  html, body { margin: 0; height: 100%%; background: #0d0c0b; color: #ece6dc; font-family: "Libron", Georgia, serif; }
  body { display: flex; flex-direction: column; justify-content: center; padding: 0 %(pad)s; box-sizing: border-box; }
  .l { opacity: 0; transform: translateY(30px); animation: up 900ms cubic-bezier(.2,.8,.2,1) forwards; }
  h1 { margin: 0; font-size: %(h1)s; line-height: 1.02; letter-spacing: -0.03em; font-weight: 700; }
  h1 em { font-style: normal; color: #c9b48f; }
  p { margin: %(gap)s 0 0; font-size: %(p)s; color: #a49c90; }
  .mark { font-size: %(mark)s; font-weight: 700; letter-spacing: -0.03em; }
  .url { margin-top: 0.6em; font-size: %(p)s; color: #c9b48f; }
  @keyframes up { to { opacity: 1; transform: none; } }
</style></head><body>%(body)s</body></html>"""
SIZES = (dict(pad="96px", h1="150px", p="54px", mark="220px", gap="48px") if PHONE
         else dict(pad="200px", h1="180px", p="58px", mark="280px", gap="44px"))
CARDS = {
    "title": """<h1 class="l">Hvor er<br>handlelisten<br><em class="l" style="animation-delay:500ms">billigst?</em></h1>""",
    "end": """<div class="mark l">sjekka</div>
        <p class="l" style="animation-delay:350ms">Matpriser fra ekte kvitteringer.</p>
        <p class="url l" style="animation-delay:700ms">sjekka.tischbein.dev</p>""",
}


class Director:
    def __init__(self, page):
        self.page = page
        self.app = page  # on the phone, the iframe once the title card is over
        self.x, self.y = (540, 1300) if PHONE else (900, 520)
        self.text = None  # the caption, shown again on every new page

    def wait(self, seconds):
        self.page.wait_for_timeout(int(seconds * 1000))

    def pointer(self, on=True):
        x, y = (self.x, self.y) if on else (-80, -80)
        self.page.evaluate(f"window.__cursor({x}, {y}); window.__move({x}, {y})")

    def goto(self, url, cursor=True):
        self.app.goto(BASE + url)
        self.app.wait_for_load_state("networkidle")
        self.pointer(cursor)
        self.caption(self.text)

    def arrived(self, glob):
        self.app.wait_for_url(glob)
        self.app.wait_for_load_state("networkidle")
        self.pointer()
        self.caption(self.text)

    def caption(self, html=None):
        self.text = html
        self.page.evaluate("h => window.__caption(h)", html)

    def card(self, name, seconds):
        self.page.goto(f"/__card?s={name}")
        self.wait(seconds)
        if PHONE:  # back to the phone frame for the site
            self.page.goto("/__phone")
            self.app = self.page.frame(name="app")

    def move_to(self, locator, pause=0.55):
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        self.x = int(box["x"] + (box["width"] / 2 if PHONE else min(box["width"] / 2, 40)))
        self.y = int(box["y"] + box["height"] / 2)
        self.page.evaluate(f"window.__move({self.x}, {self.y})")
        self.wait(pause)

    def click(self, locator, pause=0.5):
        self.move_to(locator, pause)
        self.page.evaluate("window.__press(true)")
        self.wait(0.12)
        self.page.evaluate("window.__press(false)")
        locator.click()

    def scroll(self, selector, offset=40, seconds=1.0):
        self.app.evaluate(
            "([s, o]) => window.scrollTo({ top: document.querySelector(s).getBoundingClientRect().top + scrollY - o, behavior: 'smooth' })",
            [selector, offset * ZOOM])
        self.wait(seconds)

    def top(self):
        self.app.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
        self.wait(0.7)


def card(app, name):
    return app.locator("article.card").filter(has=app.get_by_role("link", name=name, exact=True))


def film(d):
    d.card("title", 3.2)
    app = d.app  # on the phone, the iframe exists only after the title card
    nav = app.locator(".top")

    # Real prices, from real receipts
    d.goto("/")
    d.caption("Ekte priser fra <em>ekte kvitteringer</em>.")
    d.wait(1.0)
    d.scroll("#grid", offset=-40 if PHONE else 20, seconds=1.4)
    d.wait(1.3)

    # Every store, side by side
    d.caption("Se hvem som er billigst, <em>butikk for butikk</em>.")
    d.click(card(app, "Lettmelk").get_by_role("link"))
    d.arrived("**/produkt.html*")
    d.wait(0.8)
    d.scroll("#stores-section", offset=20 if PHONE else 80, seconds=1.2)
    d.move_to(app.locator(".store").first, 0.8)
    d.wait(1.0)

    # A list in a few taps
    d.caption("Legg varene på listen …")
    d.goto("/")
    d.scroll("#grid", offset=-40 if PHONE else 20, seconds=0.9)
    for name in (["Kjøttdeig", "Laksefilet"] if PHONE else ["Kjøttdeig", "Laksefilet", "Grovbrød", "Farris"]):
        d.click(card(app, name).locator(".list-toggle"), 0.4)
        d.wait(0.2)
    d.wait(0.4)

    d.caption("… og se hvor <em>hele handlelisten</em> er billigst.")
    d.top()
    d.click(nav.get_by_role("link", name="handleliste"))
    d.arrived("**/handleliste.html")
    d.scroll("#verdict" if PHONE else ".planner", offset=40, seconds=1.2)
    d.move_to(app.locator("#verdict-total"), 0.6)
    d.wait(1.6)

    # Offers, checked
    d.caption("Er tilbudet <em>egentlig</em> et tilbud? Vi sjekker.")
    d.top()
    d.click(nav.get_by_role("link", name="tilbud"))
    d.arrived("**/tilbud.html")
    d.scroll("#grid", offset=40, seconds=1.2)
    tag = app.locator("article.card.real .verdict-tag").first
    d.move_to(tag, 0.6)
    d.wait(1.4)

    # Share a receipt
    d.caption("Del kvitteringen. <em>Alle sparer.</em>")
    d.top()
    d.click(nav.get_by_role("link", name="last opp"))
    d.arrived("**/upload.html")
    d.move_to(app.locator("#drop"), 0.4)
    app.set_input_files("#receipt", str(HERE / "receipt.jpg"))
    d.wait(0.9)
    d.click(app.locator("#btn"), 0.4)
    app.wait_for_url("**/takk.html*")
    app.wait_for_load_state("networkidle")
    d.caption(None)
    d.pointer(False)
    # The receipt, read: what was found on it
    app.locator("#items li").first.wait_for(timeout=20000)
    d.wait(0.6)
    d.scroll("#found", offset=60 if PHONE else 40, seconds=1.0)
    d.wait(2.0)

    d.card("end", 3.8)


def main():
    size = {"width": 1080, "height": 1920} if PHONE else {"width": 1920, "height": 1080}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)
        context = browser.new_context(base_url=BASE, viewport=size, locale="nb-NO",
                                      record_video_dir=str(OUT_DIR), record_video_size=size)
        context.add_init_script(OVERLAY)
        context.route("**/__card*", lambda route: route.fulfill(
            content_type="text/html",
            body=CARD % dict(SIZES, body=CARDS[route.request.url.split("s=")[1]])))
        context.route("**/__phone", lambda route: route.fulfill(body=PHONE_PAGE, content_type="text/html"))
        page = context.new_page()
        film(Director(page))
        video = page.video.path()
        context.close()
        browser.close()
        print(video)


main()
