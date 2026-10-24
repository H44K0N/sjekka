"""Records the Sjekka showcase: the real app (demo server) plus the slides.

Scenes are timed by the narration: each cue's caption appears when its line
starts, and the scene holds until the line has finished. With VOICE_DIR set
(see tts.py) the real line lengths are used and the start of every cue is
written to raw/cues.json for mixing; without it, lengths are estimated."""
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from narration import CUES, spoken

HERE = Path(__file__).parent
BASE = sys.argv[1]
OUT_DIR = HERE / "raw"
SLIDES = (HERE / "slides.html").read_text()

OVERLAY = """
(() => {
  // Lay pages out as at 1280x720 but render at 1920x1080, so the video is sharp
  const Z = 1.5;
  document.addEventListener("DOMContentLoaded", () => { document.documentElement.style.zoom = String(Z); });
  const css = `
    #__cap { position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%); z-index: 99999;
      max-width: 1040px; padding: 12px 22px; border-radius: 999px; font: 16px/1.4 "Libron", Georgia, serif;
      color: #ece6dc; background: rgba(13,12,11,0.94); border: 1px solid rgba(201,180,143,0.45);
      opacity: 0; transition: opacity 350ms; pointer-events: none; text-align: center; }
    #__cur { position: fixed; z-index: 100000; width: 20px; height: 20px; margin: -10px 0 0 -10px; border-radius: 50%;
      background: rgba(236,230,220,0.9); border: 2px solid #0d0c0b; box-shadow: 0 0 0 1px rgba(236,230,220,0.5);
      transition: left 650ms cubic-bezier(.3,.7,.2,1), top 650ms cubic-bezier(.3,.7,.2,1), transform 120ms; pointer-events: none; }
    #__cur.down { transform: scale(0.7); }`;
  function ensure() {
    if (document.getElementById("__cap") || !document.body) return;
    const s = document.createElement("style"); s.textContent = css; document.head.append(s);
    const c = document.createElement("div"); c.id = "__cap"; document.body.append(c);
    const k = document.createElement("div"); k.id = "__cur"; k.style.left = (window.__cx ?? -50) / Z + "px"; k.style.top = (window.__cy ?? -50) / Z + "px"; document.body.append(k);
  }
  window.__caption = text => { ensure(); const c = document.getElementById("__cap");
    if (!text) { c.style.opacity = 0; return; } c.textContent = text; c.style.opacity = 1; };
  window.__move = (x, y) => { ensure(); const k = document.getElementById("__cur"); k.style.left = x / Z + "px"; k.style.top = y / Z + "px"; };
  window.__press = on => { ensure(); document.getElementById("__cur").classList.toggle("down", on); };
  window.__cursor = (x, y) => { window.__cx = x; window.__cy = y; };
  document.addEventListener("DOMContentLoaded", ensure);
})();
"""


class Director:
    def __init__(self, page, durations):
        self.page = page
        self.x, self.y = 640, 400
        self.durations = durations
        self.started = {}
        self.t0 = time.monotonic()  # the video starts when the page is created

    def wait(self, seconds):
        self.page.wait_for_timeout(int(seconds * 1000))

    def say(self, cue, caption=True):
        """Start a narration line now; show it as the caption on app pages."""
        self.started[cue] = round(time.monotonic() - self.t0, 2)
        if caption:
            self.caption(CUES[cue])

    def done(self, cue, pad=0.5):
        """Hold until the line has been spoken, plus a short pause."""
        end = self.t0 + self.started[cue] + self.durations[cue] + pad
        if (left := end - time.monotonic()) > 0:
            self.wait(left)

    def goto(self, url, cursor=True):
        self.page.goto(url)
        self.page.wait_for_load_state("networkidle")
        if cursor:
            self.page.evaluate(f"window.__cursor({self.x}, {self.y}); window.__move({self.x}, {self.y})")
        else:
            self.page.evaluate("window.__move(-50, -50)")

    def caption(self, text=None):
        self.page.evaluate("t => window.__caption(t)", text)

    def step(self, n, text=None):
        self.page.evaluate("([n, t]) => window.step(n, t)", [n, text])

    def move_to(self, locator):
        box = locator.bounding_box()
        self.x, self.y = int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)
        self.page.evaluate(f"window.__move({self.x}, {self.y})")
        self.page.mouse.move(self.x, self.y)
        self.wait(0.75)

    def click(self, locator):
        self.move_to(locator)
        self.page.evaluate("window.__press(true)")
        self.wait(0.12)
        self.page.evaluate("window.__press(false)")
        locator.click()

    def type(self, locator, text):
        self.click(locator)
        locator.press_sequentially(text, delay=110)

    def clear(self, locator):
        locator.fill("")
        locator.dispatch_event("input")

    def scroll_to(self, selector, offset=40):
        self.page.evaluate(
            "([s, o]) => window.scrollTo({ top: document.querySelector(s).getBoundingClientRect().top + scrollY - o, behavior: 'smooth' })",
            [selector, offset * 1.5],
        )
        self.wait(1.2)


def durations():
    voice = os.getenv("VOICE_DIR")
    if voice:
        return json.loads((Path(voice) / "durations.json").read_text())
    # About 15 characters a second, like the real voice
    return {cue: len(spoken(text)) / 15 for cue, text in CUES.items()}


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)
        context = browser.new_context(
            base_url=BASE, viewport={"width": 1920, "height": 1080},
            locale="nb-NO", record_video_dir=str(OUT_DIR), record_video_size={"width": 1920, "height": 1080},
        )
        context.add_init_script(OVERLAY)
        context.route("**/__slides*", lambda route: route.fulfill(body=SLIDES, content_type="text/html"))
        page = context.new_page()
        d = Director(page, durations())
        search = page.locator("#search")

        # Title
        d.goto("/__slides?s=title", cursor=False)
        d.wait(0.8)
        d.say("title", caption=False)
        d.done("title", 0.8)

        # Explore page
        d.goto("/")
        d.say("explore")
        d.wait(2)
        d.scroll_to(".explore")
        d.done("explore")
        d.say("search")
        d.type(search, "melk")
        d.wait(0.8)
        d.clear(search)
        d.type(search, "tine")
        d.done("search", 1.2)
        d.clear(search)
        d.say("filter")
        d.click(page.get_by_role("button", name="Kjøtt", exact=True))
        d.done("filter", 0.8)
        d.click(page.get_by_role("button", name="Alle"))

        # Upload
        d.caption()
        page.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
        d.wait(1)
        d.click(page.get_by_role("link", name="last opp", exact=True))
        page.wait_for_load_state("networkidle")
        page.evaluate(f"window.__cursor({d.x}, {d.y}); window.__move({d.x}, {d.y})")
        d.say("upload")
        d.wait(1)
        d.move_to(page.locator("#drop"))
        page.set_input_files("#receipt", str(HERE / "receipt.jpg"))
        d.wait(1.2)
        d.scroll_to(".actions", offset=420)
        d.done("upload", 0.2)
        d.click(page.locator("#btn"))
        page.wait_for_url("**/takk.html")
        page.wait_for_load_state("networkidle")
        page.evaluate(f"window.__cursor({d.x}, {d.y}); window.__move(-50, -50)")
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
        d.goto("/")
        d.say("result")
        d.scroll_to(".explore")
        d.done("result", 0.3)
        d.say("mince")
        d.type(search, "kjøttdeig")
        d.done("mince", 0.8)
        d.clear(search)
        d.say("multipack")
        d.type(search, "avokado")
        d.done("multipack", 1.0)
        d.clear(search)
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

        video = page.video.path()
        context.close()
        browser.close()
        (OUT_DIR / "cues.json").write_text(json.dumps(d.started, indent=2))
        print(video)


main()
