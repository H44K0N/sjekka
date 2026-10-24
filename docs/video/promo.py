"""Records the promo films for the about page: a short story told by an
English voice (promo_script.py), over title cards and the real app on demo
data.

    VOICE_DIR=<tts.py output for promo_script> python promo.py <base url> desktop|mobile

Desktop is 1920x1080 with a mouse pointer; mobile 1080x1920 with a round
touch dot, the site in a phone-width iframe scaled up (Playwright records
CSS pixels). Each scene holds until its line has been spoken. Writes when
every line and sound effect starts to raw/promo-<mode>.json for
promo_mix.py, and prints the raw video's path."""
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from promo_script import CUES

HERE = Path(__file__).parent
BASE, MODE = sys.argv[1], sys.argv[2]
PHONE = MODE == "mobile"
OUT_DIR = HERE / "raw"
DURATIONS = json.loads((Path(os.environ["VOICE_DIR"]) / "durations.json").read_text())

PHONE_WIDTH, PHONE_HEIGHT = 405, 720
PHONE_PAGE = f"""<!DOCTYPE html><html><head><style>
  html, body {{ margin: 0; background: #0d0c0b; overflow: hidden; }}
  iframe {{ position: absolute; left: 0; top: 0; width: {PHONE_WIDTH}px; height: {PHONE_HEIGHT}px; border: 0;
    transform: scale({1080 / PHONE_WIDTH}); transform-origin: 0 0; }}
</style></head><body><iframe name="app" src="about:blank"></iframe></body></html>"""

ARROW = ("data:image/svg+xml," + "%3Csvg xmlns='http://www.w3.org/2000/svg' width='26' height='34' viewBox='0 0 26 34'%3E"
         "%3Cpath d='M2 2 L2 27 L8.5 21 L13 31 L17 29 L12.5 19.5 L21 19.5 Z' fill='%23ece6dc' stroke='%230d0c0b' "
         "stroke-width='2' stroke-linejoin='round'/%3E%3C/svg%3E")

# The desktop site is shown zoomed 2x (laid out at 960 pixels), so prices
# and logos read well; overlay sizes are in video pixels, divided back
ZOOM = 1 if PHONE else 2
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
      font: 600 %(caption)s/1.2 "Libron", Georgia, serif; letter-spacing: -0.01em; color: #ece6dc; pointer-events: none; }
    #__cap span { display: inline-block; opacity: 0; transform: translateY(20px); transition: opacity 400ms, transform 550ms cubic-bezier(.2,.8,.2,1); }
    #__cap span.on { opacity: 1; transform: none; }
    #__cur { position: fixed; z-index: 100000; %(cursor)s
      transition: left 520ms cubic-bezier(.3,.7,.2,1), top 520ms cubic-bezier(.3,.7,.2,1), transform 120ms; pointer-events: none; }
    #__cur.down { transform: scale(%(press)s); }
    #__veil { position: fixed; inset: 0; z-index: 100001; background: #0d0c0b; transition: opacity 450ms; pointer-events: none; }`;
  function ensure() {
    if (document.getElementById("__cap") || !document.body) return;
    const s = document.createElement("style"); s.textContent = css; document.head.append(s);
    const scrim = document.createElement("div"); scrim.id = "__scrim"; document.body.append(scrim);
    const c = document.createElement("div"); c.id = "__cap"; document.body.append(c);
    const k = document.createElement("div"); k.id = "__cur"; k.style.left = (window.__cx ?? -80) / Z + "px"; k.style.top = (window.__cy ?? -80) / Z + "px"; document.body.append(k);
    // Coming from a title card: stay dark until the page has drawn itself
    if (sessionStorage.getItem("__veil")) {
      sessionStorage.removeItem("__veil");
      const v = document.createElement("div"); v.id = "__veil"; document.body.append(v);
    }
  }
  window.__unveil = () => { const v = document.getElementById("__veil"); if (v) { v.style.opacity = 0; setTimeout(() => v.remove(), 500); } };
  window.__caption = text => { ensure(); const c = document.getElementById("__cap"), s = document.getElementById("__scrim");
    if (!text) { c.querySelector("span")?.classList.remove("on"); s.style.opacity = 0; return; }
    const span = document.createElement("span"); span.textContent = text; c.replaceChildren(span); s.style.opacity = 1;
    requestAnimationFrame(() => requestAnimationFrame(() => span.classList.add("on"))); };
  window.__move = (x, y) => { ensure(); const k = document.getElementById("__cur"); k.style.left = x / Z + "px"; k.style.top = y / Z + "px"; };
  window.__press = on => { ensure(); document.getElementById("__cur").classList.toggle("down", on); };
  window.__cursor = (x, y) => { window.__cx = x; window.__cy = y; };
  document.addEventListener("DOMContentLoaded", ensure);
})();
""" % dict(zoom=ZOOM, caption=f"calc({'60px' if PHONE else '50px'} / {ZOOM})", bottom=f"calc({'150px' if PHONE else '60px'} / {ZOOM})",
           scrim=f"calc({'560px' if PHONE else '300px'} / {ZOOM})", cursor=CURSOR, press=0.7 if PHONE else 0.88)

# Title cards, in the site's type and colours. The "spend" card fills with
# promo signs one at a time (window.sign(i)), so each can get its pop.
SIGNS = ["-50%", "2 FOR 1", "SALE", "MEGA DEAL", "3 FOR 2", "WAS 49.90", "SUPER PRICE", "LIMITED!",
         "BUY MORE, SAVE", "-30%", "HOT DEAL", "ONLY TODAY"]
CARD = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><style>
  @font-face { font-family: "Libron"; src: url("/fonts/Libron-Regular.woff2") format("woff2"); font-weight: 400; }
  @font-face { font-family: "Libron"; src: url("/fonts/Libron-Bold.woff2") format("woff2"); font-weight: 700; }
  html, body { margin: 0; height: 100%%; overflow: hidden; background: #0d0c0b; color: #ece6dc; font-family: "Libron", Georgia, serif; }
  body { display: flex; flex-direction: column; justify-content: center; padding: 0 %(pad)s; box-sizing: border-box; }
  .l { opacity: 0; transform: translateY(30px); animation: up 800ms cubic-bezier(.2,.8,.2,1) forwards; }
  h1 { margin: 0; font-size: %(h1)s; line-height: 1.04; letter-spacing: -0.03em; font-weight: 700; position: relative; z-index: 2; }
  h1 em { font-style: normal; color: #c9b48f; }
  h1.small { font-size: %(h2)s; }
  p { margin: %(gap)s 0 0; font-size: %(p)s; color: #a49c90; }
  .mark { font-size: %(mark)s; font-weight: 700; letter-spacing: -0.03em; line-height: 1; }
  .url { font-size: %(p)s; color: #c9b48f; }
  .sign { position: absolute; z-index: 1; padding: 0.25em 0.6em; font: 700 %(sign)s/1 Impact, "Arial Black", sans-serif; letter-spacing: 0.02em;
    color: #fff; background: #d62718; border: 0.08em solid #ffd400; border-radius: 0.15em; box-shadow: 0 0.2em 0.6em rgba(0,0,0,0.5);
    transform: scale(0) rotate(var(--r)); animation: pop 260ms cubic-bezier(.3,1.6,.5,1) forwards; }
  .sign.y { color: #d62718; background: #ffd400; border-color: #d62718; }
  body.cleared .sign { animation: away 500ms ease-in forwards; }
  @keyframes up { to { opacity: 1; transform: none; } }
  @keyframes pop { to { transform: scale(1) rotate(var(--r)); } }
  @keyframes away { from { transform: scale(1) rotate(var(--r)); opacity: 1; } to { transform: scale(0.2) rotate(var(--r)) translateY(80px); opacity: 0; } }
</style></head><body>%(body)s<script>
  const SIGNS = %(signs)s;
  // A zigzag: left, right, left ... working down the screen, slightly off-grid
  window.sign = i => {
    const s = document.createElement("div");
    s.className = "sign" + (i %% 3 === 1 ? " y" : "");
    s.textContent = SIGNS[i %% SIGNS.length];
    const row = i %% 6, left = (i %% 2 ? 56 : 5) + (i * 37 %% 17);
    s.style.left = left + "%%"; s.style.top = (5 + row * 15 + (i * 23 %% 7)) + "%%";
    s.style.setProperty("--r", ((i %% 2 ? 1 : -1) * (4 + i * 7 %% 9)) + "deg");
    document.body.append(s);
  };
  window.clear = () => document.body.classList.add("cleared");
</script></body></html>"""
SIZES = (dict(pad="90px", h1="128px", h2="96px", p="54px", mark="240px", gap="44px", sign="64px") if PHONE
         else dict(pad="200px", h1="150px", h2="110px", p="56px", mark="280px", gap="40px", sign="58px"))
CARDS = {
    "goal": """<h1 class="l">Every week,<br>the big chains<br>have <em>one goal.</em></h1>""",
    "spend": """<h1 class="l">Your <em>money.</em></h1>""",
    "war": """<h1 class="l">We're declaring<br><em>war</em> on the<br>big chains.</h1>""",
    "meet": """<div class="mark l">sjekka</div>""",
    "question": """<p class="l" style="margin:0 0 0.4em">One question:</p>
        <h1 class="l" style="animation-delay:250ms">Where is my<br>grocery list<br><em>cheapest?</em></h1>""",
    "end": """<div class="mark l">sjekka</div>
        <h1 class="l small" style="animation-delay:300ms; margin-top:0.3em">Stop overpaying.</h1>
        <p class="url l" style="animation-delay:600ms">sjekka.tischbein.dev</p>""",
}


class Director:
    def __init__(self, page):
        self.page = page
        self.app = page  # on the phone: the iframe, once there's a site to show
        self.x, self.y = (540, 1300) if PHONE else (900, 520)
        self.text = None
        self.t0 = time.monotonic()  # the video starts when the page is created
        self.cues, self.sfx = {}, []

    def now(self):
        return round(time.monotonic() - self.t0, 2)

    def wait(self, seconds):
        self.page.wait_for_timeout(int(seconds * 1000))

    # ---- sound ----

    def say(self, cue, caption=True):
        self.cues[cue] = self.now()
        if caption:
            self.caption(CUES[cue])

    def done(self, cue, pad=0.4):
        left = self.t0 + self.cues[cue] + DURATIONS[cue] + pad - time.monotonic()
        if left > 0:
            self.wait(left)

    def fx(self, name):
        self.sfx.append([name, self.now()])

    # ---- picture ----

    def on_card(self):
        return "/__card" in self.page.url

    def card(self, name):
        self.caption(None)
        self.fx("whoosh")
        self.page.goto(f"/__card?s={name}")
        self.app = self.page

    def site(self, url):
        """From a card to the site (on the phone, inside the phone frame),
        behind a veil until it has loaded."""
        self.fx("whoosh")
        self.page.evaluate("sessionStorage.setItem('__veil', '1')")
        if PHONE:
            self.page.goto("/__phone")
            self.app = self.page.frame(name="app")
        self.goto(url)
        self.wait(0.3)
        self.page.evaluate("window.__unveil()")

    def pointer(self, on=True):
        x, y = (self.x, self.y) if on else (-80, -80)
        self.page.evaluate(f"window.__cursor({x}, {y}); window.__move({x}, {y})")

    def goto(self, url):
        self.app.goto(BASE + url)
        self.app.wait_for_load_state("networkidle")
        self.pointer()
        self.caption(self.text)

    def arrived(self, glob):
        self.app.wait_for_url(glob)
        self.app.wait_for_load_state("networkidle")
        self.pointer()
        self.caption(self.text)

    def caption(self, text=None):
        self.text = text
        if not self.on_card():
            self.page.evaluate("t => window.__caption(t)", text)

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
        self.fx("click")
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
        self.wait(0.6)


def card(app, name):
    return app.locator("article.card").filter(has=app.get_by_role("link", name=name, exact=True))


def film(d):
    # The chains want your money ...
    d.wait(0.6)
    d.say("goal", caption=False)
    d.done("goal", 0.2)
    d.card("spend")
    d.wait(0.3)
    d.say("spend", caption=False)
    # ... and fill the week with signs, in a zigzag
    for i in range(12):
        d.page.evaluate(f"window.sign({i})")
        d.fx("pop")
        d.wait(0.42 if i < 5 else 0.3)
        if i == 4:
            d.say("maze", caption=False)
    d.done("maze", 0.3)

    # ... so: war
    d.page.evaluate("window.clear()")
    d.wait(0.4)
    d.card("war")
    d.fx("boom")
    d.wait(0.3)
    d.say("war", caption=False)
    d.done("war", 0.5)

    d.card("meet")
    d.fx("chime")
    d.cues["reveal"] = d.now()  # where the music turns bright
    d.wait(0.2)
    d.say("meet", caption=False)
    d.done("meet", 0.6)
    d.card("question")
    d.wait(0.3)
    d.say("question", caption=False)
    d.done("question", 0.6)

    # Add what you need ...
    d.site("/")
    app = d.app
    d.scroll("#grid", offset=-40 if PHONE else 20, seconds=0.8)
    d.say("add")
    for name in (["Kjøttdeig", "Laksefilet"] if PHONE else ["Kjøttdeig", "Laksefilet", "Grovbrød", "Farris"]):
        d.click(card(app, name).locator(".list-toggle"), 0.35)
        d.wait(0.15)
    d.done("add", 0.2)

    # ... and see the whole list, in every store. Straight there: scrolling
    # back up to the menu would flash the site's Norwegian headings
    d.say("list")
    d.goto("/handleliste.html")
    d.scroll("#verdict" if PHONE else ".planner", offset=40, seconds=1.0)
    d.move_to(app.locator("#verdict-total"), 0.5)
    d.fx("chime")
    d.done("list", 0.5)

    # Real receipts
    d.say("receipts")
    d.goto("/produkt.html?navn=Lettmelk")
    d.scroll("#stores-section", offset=20 if PHONE else 80, seconds=1.0)
    d.move_to(app.locator(".store").first, 0.6)
    d.done("receipts", 0.4)

    # Offers, checked
    d.say("offers")
    d.goto("/tilbud.html")
    d.scroll("#grid", offset=40, seconds=1.0)
    d.move_to(app.locator("article.card.real .verdict-tag").first, 0.6)
    d.done("offers", 0.4)

    # Share
    d.say("share")
    d.goto("/upload.html")
    d.move_to(app.locator("#drop"), 0.3)
    app.set_input_files("#receipt", str(HERE / "receipt.jpg"))
    d.wait(0.6)
    d.click(app.locator("#btn"), 0.3)
    app.wait_for_url("**/takk.html*")
    app.wait_for_load_state("networkidle")
    d.caption(d.text)
    d.pointer(False)
    app.locator("#items li").first.wait_for(timeout=20000)
    d.scroll("#found", offset=60 if PHONE else 40, seconds=0.8)
    d.done("share", 1.0)

    d.card("end")
    d.fx("chime")
    d.wait(0.3)
    d.say("end", caption=False)
    d.done("end", 2.6)


def main():
    size = {"width": 1080, "height": 1920} if PHONE else {"width": 1920, "height": 1080}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)
        context = browser.new_context(base_url=BASE, viewport=size, locale="nb-NO",
                                      record_video_dir=str(OUT_DIR), record_video_size=size)
        context.add_init_script(OVERLAY)
        context.route("**/__card*", lambda route: route.fulfill(content_type="text/html", body=CARD % dict(
            SIZES, body=CARDS[route.request.url.split("s=")[1]], signs=json.dumps(SIGNS))))
        context.route("**/__phone", lambda route: route.fulfill(body=PHONE_PAGE, content_type="text/html"))
        page = context.new_page()
        d = Director(page)  # the video's clock starts with the page
        page.goto("/__card?s=goal")  # the story starts on the first card
        film(d)
        video = page.video.path()
        context.close()
        browser.close()
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / f"promo-{MODE}.json").write_text(json.dumps({"cues": d.cues, "sfx": d.sfx}, indent=2))
        print(video)


main()
