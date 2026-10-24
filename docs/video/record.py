"""Records the Sjekka showcase: the real app (demo server) plus the slides.

    python record.py <base url> desktop   # 1920x1080, a mouse pointer, the slides
    python record.py <base url> mobile    # 1080x1920, a round touch dot, features only

Scenes are timed by the narration: each cue's caption appears when its line
starts, and the scene holds until the line has finished. With VOICE_DIR set
(see tts.py) the real line lengths are used and the start of every cue is
written to raw/cues-<mode>.json for mixing; without it, lengths are estimated.

Playwright records CSS pixels, so a phone-sized page would come out at
405x720. The phone video instead shows the site in a 405-pixel-wide iframe
scaled up to fill 1080x1920: the site lays itself out for a phone, and the
text stays sharp."""
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from narration import CUES, MOBILE, spoken

HERE = Path(__file__).parent
BASE, MODE = sys.argv[1], sys.argv[2]
OUT_DIR = HERE / "raw"
SLIDES = (HERE / "slides.html").read_text()

PHONE_WIDTH, PHONE_HEIGHT = 405, 720
PHONE_SCALE = 1080 / PHONE_WIDTH
PHONE = f"""<!DOCTYPE html><html><head><style>
  html, body {{ margin: 0; background: #0d0c0b; overflow: hidden; }}
  iframe {{ position: absolute; left: 0; top: 0; width: {PHONE_WIDTH}px; height: {PHONE_HEIGHT}px; border: 0;
    transform: scale({PHONE_SCALE}); transform-origin: 0 0; }}
</style></head><body><iframe name="app" src="about:blank"></iframe></body></html>"""

# A mouse pointer, tip at the top left corner of the element
ARROW = ("data:image/svg+xml," + "%3Csvg xmlns='http://www.w3.org/2000/svg' width='26' height='34' viewBox='0 0 26 34'%3E"
         "%3Cpath d='M2 2 L2 27 L8.5 21 L13 31 L17 29 L12.5 19.5 L21 19.5 Z' fill='%23ece6dc' stroke='%230d0c0b' "
         "stroke-width='2' stroke-linejoin='round'/%3E%3C/svg%3E")

STYLE = {
    # Desktop pages are laid out as at 1280x720 but rendered at 1920x1080, so the video is sharp
    "desktop": dict(zoom=1.5, caption="16px", bottom="28px", cursor=f"""
        width: 26px; height: 34px; background: url("{ARROW}") no-repeat; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5));
        transform-origin: 2px 2px;""", press="scale(0.88)"),
    # The phone page isn't zoomed; the iframe is, so everything here is in video pixels
    "mobile": dict(zoom=1, caption="38px", bottom="72px", cursor="""
        width: 56px; height: 56px; margin: -28px 0 0 -28px; border-radius: 50%;
        background: rgba(236,230,220,0.85); border: 4px solid #0d0c0b; box-shadow: 0 0 0 2px rgba(236,230,220,0.5);""",
        press="scale(0.7)"),
}[MODE]

OVERLAY = """
(() => {
  // Only the page being recorded gets the caption and pointer, not the phone's iframe
  if (window.top !== window) return;
  const Z = %(zoom)s;
  if (Z !== 1) document.addEventListener("DOMContentLoaded", () => { document.documentElement.style.zoom = String(Z); });
  const css = `
    #__cap { position: fixed; left: 50%%; bottom: %(bottom)s; transform: translateX(-50%%); z-index: 99999;
      width: max-content; max-width: 88vw; padding: 0.75em 1.4em; border-radius: 1.6em; font: %(caption)s/1.4 "Libron", Georgia, serif;
      color: #ece6dc; background: rgba(13,12,11,0.94); border: 1px solid rgba(201,180,143,0.45);
      opacity: 0; transition: opacity 350ms; pointer-events: none; text-align: center; box-sizing: border-box; }
    #__cur { position: fixed; z-index: 100000; %(cursor)s
      transition: left 650ms cubic-bezier(.3,.7,.2,1), top 650ms cubic-bezier(.3,.7,.2,1), transform 120ms; pointer-events: none; }
    #__cur.down { transform: %(press)s; }`;
  function ensure() {
    if (document.getElementById("__cap") || !document.body) return;
    const s = document.createElement("style"); s.textContent = css; document.head.append(s);
    const c = document.createElement("div"); c.id = "__cap"; document.body.append(c);
    const k = document.createElement("div"); k.id = "__cur"; k.style.left = (window.__cx ?? -80) / Z + "px"; k.style.top = (window.__cy ?? -80) / Z + "px"; document.body.append(k);
  }
  window.__caption = text => { ensure(); const c = document.getElementById("__cap");
    if (!text) { c.style.opacity = 0; return; } c.textContent = text; c.style.opacity = 1; };
  window.__move = (x, y) => { ensure(); const k = document.getElementById("__cur"); k.style.left = x / Z + "px"; k.style.top = y / Z + "px"; };
  window.__press = on => { ensure(); document.getElementById("__cur").classList.toggle("down", on); };
  window.__cursor = (x, y) => { window.__cx = x; window.__cy = y; };
  document.addEventListener("DOMContentLoaded", ensure);
})();
""" % STYLE


class Director:
    """Drives the app like a person would. `page` is what's recorded (it has
    the caption and pointer); `app` is where the site runs: the same page on
    desktop, the phone's iframe on mobile."""

    def __init__(self, page, durations, phone=False):
        self.page = page
        self.phone = phone
        self.app = page.frame(name="app") if phone else page
        self.x, self.y = (540, 1200) if phone else (640, 400)
        self.durations = durations
        self.started = {}
        self.t0 = time.monotonic()  # the video starts when the page is created

    def wait(self, seconds):
        self.page.wait_for_timeout(int(seconds * 1000))

    def say(self, cue, caption=True):
        """Start a narration line now; show it as the caption on app pages."""
        self.started[cue] = round(time.monotonic() - self.t0, 2)
        if caption:
            self.caption({**CUES, **MOBILE}[cue])

    def done(self, cue, pad=0.5):
        """Hold until the line has been spoken, plus a short pause."""
        end = self.t0 + self.started[cue] + self.durations[cue] + pad
        if (left := end - time.monotonic()) > 0:
            self.wait(left)

    def show_pointer(self, on=True):
        if on:
            self.page.evaluate(f"window.__cursor({self.x}, {self.y}); window.__move({self.x}, {self.y})")
        else:
            self.page.evaluate("window.__move(-80, -80)")

    def goto(self, url, cursor=True):
        self.app.goto(BASE + url if url.startswith("/") else url)
        self.app.wait_for_load_state("networkidle")
        self.show_pointer(cursor)

    def arrived(self, url_glob):
        """After a click that navigates: wait for the new page to settle."""
        self.app.wait_for_url(url_glob)
        self.app.wait_for_load_state("networkidle")
        self.show_pointer()

    def caption(self, text=None):
        self.page.evaluate("t => window.__caption(t)", text)

    def step(self, n, text=None):
        self.page.evaluate("([n, t]) => window.step(n, t)", [n, text])

    def move_to(self, locator):
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        if self.phone:
            self.x, self.y = int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)
        else:
            # The tip of the arrow, not its middle, is on the spot
            self.x, self.y = int(box["x"] + min(box["width"] / 2, 40)), int(box["y"] + box["height"] / 2)
        self.page.evaluate(f"window.__move({self.x}, {self.y})")
        self.page.mouse.move(self.x, self.y)
        self.wait(0.75)

    def click(self, locator):
        self.move_to(locator)
        self.page.evaluate("window.__press(true)")
        self.wait(0.15)
        self.page.evaluate("window.__press(false)")
        locator.click()

    def type(self, locator, text):
        self.click(locator)
        locator.press_sequentially(text, delay=110)

    def clear(self, locator):
        locator.fill("")
        locator.dispatch_event("input")

    def scroll_to(self, selector, offset=40):
        """Smoothly bring an element near the top, `offset` page pixels below it."""
        zoom = 1 if self.phone else 1.5
        self.app.evaluate(
            "([s, o]) => window.scrollTo({ top: document.querySelector(s).getBoundingClientRect().top + scrollY - o, behavior: 'smooth' })",
            [selector, offset * zoom],
        )
        self.wait(1.2)

    def scroll_top(self):
        self.app.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
        self.wait(1)


def durations():
    voice = os.getenv("VOICE_DIR")
    if voice:
        return json.loads((Path(voice) / "durations.json").read_text())
    # About 15 characters a second, like the real voice
    return {cue: len(spoken(text)) / 15 for cue, text in {**CUES, **MOBILE}.items()}


def card(app, name):
    return app.locator("article.card").filter(has=app.get_by_role("link", name=name, exact=True))


def desktop(d):
    app = d.app
    search = app.locator("#search")

    # Title
    d.goto("/__slides?s=title", cursor=False)
    d.wait(0.8)
    d.say("title", caption=False)
    d.done("title", 0.8)

    # Explore
    d.goto("/")
    d.say("explore")
    d.wait(2)
    d.scroll_to(".explore")
    d.done("explore")
    d.say("search")
    d.type(search, "melk")
    d.wait(1)
    d.clear(search)
    d.type(search, "gilde")
    d.done("search", 0.8)

    # A product page, and onto the list
    d.click(card(app, "Kjøttdeig").get_by_role("link"))
    d.arrived("**/produkt.html*")
    d.say("product")
    d.wait(2.5)
    d.scroll_to("#stores-section", offset=60)
    d.move_to(app.locator(".store").nth(2).locator(".spark"))
    d.done("product", 0.3)
    d.scroll_top()
    d.say("add")
    d.click(app.get_by_role("button", name="Legg på handlelisten"))
    d.wait(0.8)
    d.click(app.locator(".back"))
    d.arrived("**/")
    d.clear(search)
    d.scroll_to(".explore")
    for name in ["Lettmelk", "Grovbrød", "Farris", "Bananer"]:
        d.click(card(app, name).locator(".list-toggle"))
        d.wait(0.3)
    d.done("add", 0.4)

    # The list
    d.click(app.locator(".top").get_by_role("link", name="handleliste"))
    d.arrived("**/handleliste.html")
    d.say("list")
    d.wait(1)
    d.scroll_to(".planner", offset=30)
    d.move_to(app.locator("#verdict-total"))
    d.done("list", 0.3)
    d.say("estimate")
    d.click(app.locator(".rank-row").nth(1))
    d.wait(1.5)
    d.move_to(app.locator(".rank-detail:not([hidden]) tr.estimated").first)
    d.done("estimate", 0.6)

    # Offers
    d.click(app.locator(".top").get_by_role("link", name="tilbud"))
    d.arrived("**/tilbud.html")
    d.say("offers")
    d.wait(1.5)
    d.scroll_to(".explore")
    d.move_to(card(app, "Kjøttdeig").locator(".verdict-tag"))
    d.done("offers", 0.3)
    d.say("real")
    d.move_to(card(app, "Lettmelk").locator(".verdict-tag"))
    d.wait(0.8)
    # Filtering redraws the cards, so open the details after
    d.click(app.get_by_label("Bare ekte tilbud"))
    d.wait(0.8)
    d.click(card(app, "Kjøttdeig").locator(".fold summary"))
    d.done("real", 1.0)

    # Upload
    d.caption()
    d.scroll_top()
    d.click(app.locator(".top").get_by_role("link", name="last opp"))
    d.arrived("**/upload.html")
    d.say("upload")
    d.wait(1)
    d.move_to(app.locator("#drop"))
    app.set_input_files("#receipt", str(HERE / "receipt.jpg"))
    d.wait(1.2)
    d.scroll_to(".actions", offset=420)
    d.done("upload", 0.2)
    d.click(app.locator("#btn"))
    app.wait_for_url("**/takk.html*")
    app.wait_for_load_state("networkidle")
    d.show_pointer(False)
    d.say("thanks")
    d.wait(3)
    d.scroll_to("#img", offset=160)
    d.done("thanks", 0.8)

    # Architecture, one step per line
    d.goto("/__slides?s=arch&manual", cursor=False)
    d.wait(1)
    for n in range(1, 9):
        d.step(n, CUES[f"arch{n}"])  # the slide shows the spoken line
        d.say(f"arch{n}", caption=False)
        d.done(f"arch{n}", 0.5)
    d.wait(0.8)

    # The uploaded receipt, processed
    d.goto("/?q=avokado")
    d.scroll_to(".explore")
    d.say("result")
    d.wait(2)
    d.clear(search)
    d.type(search, "tortilla")
    d.done("result", 1.0)
    d.caption()
    d.wait(0.6)

    # Pipeline and end
    d.goto("/__slides?s=pipeline&manual", cursor=False)
    d.wait(0.5)
    d.say("pipeline1", caption=False)
    d.done("pipeline1", 0.4)
    d.step(2)
    d.say("pipeline2", caption=False)
    d.done("pipeline2", 1.2)
    d.goto("/__slides?s=end", cursor=False)
    d.wait(0.6)
    d.say("end", caption=False)
    d.done("end", 2.5)


def mobile(d):
    app = d.app

    d.goto("/")
    d.say("m_intro")
    d.done("m_intro", 0.6)
    d.say("m_explore")
    d.scroll_to(".listbar", offset=8)
    d.wait(1)
    d.scroll_to("#grid", offset=-220)
    d.done("m_explore", 0.3)

    d.say("m_add")
    for name in ["Kjøttdeig", "Laksefilet", "Lettmelk"]:
        d.click(card(app, name).locator(".list-toggle"))
        d.wait(0.4)
    d.done("m_add", 0.4)

    d.scroll_top()
    d.click(app.locator(".top").get_by_role("link", name="handleliste"))
    d.arrived("**/handleliste.html")
    d.say("m_list")
    d.wait(1)
    d.scroll_to("#items", offset=16)
    d.wait(1.5)
    d.scroll_to("#verdict", offset=60)
    d.move_to(app.locator("#verdict-total"))
    d.done("m_list", 0.6)

    d.scroll_top()
    d.click(app.locator(".top").get_by_role("link", name="tilbud"))
    d.arrived("**/tilbud.html")
    d.say("m_offers")
    d.scroll_to("#grid", offset=60)
    d.click(card(app, "Kjøttdeig").locator(".fold summary"))
    d.wait(1.2)
    d.done("m_offers", 0.6)

    d.scroll_top()
    d.click(app.locator(".top").get_by_role("link", name="last opp"))
    d.arrived("**/upload.html")
    d.say("m_upload")
    d.move_to(app.locator("#drop"))
    app.set_input_files("#receipt", str(HERE / "receipt.jpg"))
    d.wait(1.2)
    d.click(app.locator("#btn"))
    app.wait_for_url("**/takk.html*")
    app.wait_for_load_state("networkidle")
    d.show_pointer(False)
    d.done("m_upload", 1.5)

    d.say("m_end")
    d.wait(1)
    d.scroll_to("#img", offset=80)
    d.done("m_end", 2.0)


def main():
    phone = MODE == "mobile"
    size = {"width": 1080, "height": 1920} if phone else {"width": 1920, "height": 1080}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)
        context = browser.new_context(
            base_url=BASE, viewport=size, locale="nb-NO",
            record_video_dir=str(OUT_DIR), record_video_size=size,
        )
        context.add_init_script(OVERLAY)
        context.route("**/__slides*", lambda route: route.fulfill(body=SLIDES, content_type="text/html"))
        context.route("**/__phone", lambda route: route.fulfill(body=PHONE, content_type="text/html"))
        page = context.new_page()
        if phone:
            page.goto("/__phone")
        d = Director(page, durations(), phone)
        (mobile if phone else desktop)(d)

        video = page.video.path()
        context.close()
        browser.close()
        (OUT_DIR / f"cues-{MODE}.json").write_text(json.dumps(d.started, indent=2))
        print(video)


main()
