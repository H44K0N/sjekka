"""Records the Sjekka showcase: the real app (demo server) plus the slides."""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

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
      max-width: 1040px; padding: 12px 22px; border-radius: 999px; font: 15px/1.4 "Google Sans Code", ui-monospace, monospace;
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
    def __init__(self, page):
        self.page = page
        self.x, self.y = 640, 400

    def wait(self, seconds):
        self.page.wait_for_timeout(int(seconds * 1000))

    def goto(self, url, cursor=True):
        self.page.goto(url)
        self.page.wait_for_load_state("networkidle")
        if cursor:
            self.page.evaluate(f"window.__cursor({self.x}, {self.y}); window.__move({self.x}, {self.y})")
        else:
            self.page.evaluate("window.__move(-50, -50)")

    def caption(self, text=None):
        self.page.evaluate("t => window.__caption(t)", text)

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
        d = Director(page)

        # 1. Title
        d.goto("/__slides?s=title", cursor=False)
        d.wait(5)

        # 2. Explore page
        d.goto("/")
        d.caption("The explore page shows the latest price of every product, from receipts people share.")
        d.wait(3.5)
        d.scroll_to(".explore")
        d.wait(2.5)
        search = page.locator("#search")
        d.caption("Search matches product names and brands.")
        d.type(search, "melk")
        d.wait(2)
        d.clear(search)
        d.type(search, "tine")
        d.wait(2.2)
        d.clear(search)
        d.caption("Prices are compared per 100 g, per 100 ml or per item.")
        d.click(page.get_by_role("button", name="Kjøtt", exact=True))
        d.wait(3)
        d.click(page.get_by_role("button", name="Alle"))
        d.wait(1)

        # 3. Upload
        d.caption()
        page.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
        d.wait(1)
        d.click(page.get_by_role("link", name="last opp", exact=True))
        page.wait_for_load_state("networkidle")
        page.evaluate(f"window.__cursor({d.x}, {d.y}); window.__move({d.x}, {d.y})")
        d.caption("Uploading a receipt: the photo is checked in the browser before it's sent.")
        d.wait(2)
        d.move_to(page.locator("#drop"))
        page.set_input_files("#receipt", str(HERE / "receipt.jpg"))
        d.wait(1.5)
        d.scroll_to(".actions", offset=420)
        d.wait(1.5)
        d.click(page.locator("#btn"))
        page.wait_for_url("**/takk.html")
        page.wait_for_load_state("networkidle")
        page.evaluate(f"window.__cursor({d.x}, {d.y}); window.__move(-50, -50)")
        d.caption("It's queued at once. The photo stays private; this copy comes from your own browser tab.")
        d.wait(3.5)
        d.scroll_to("#img", offset=160)
        d.wait(2.5)

        # 4. Architecture
        d.goto("/__slides?s=arch", cursor=False)
        d.wait(27.5)

        # 5. Result
        d.goto("/")
        d.caption("Meanwhile the worker has read the receipt, and its prices are live.")
        d.scroll_to(".explore")
        d.wait(1.5)
        d.type(search := page.locator("#search"), "kjøttdeig")
        d.caption("“kjøttdeig” from the new receipt is grouped with the existing product: today's price at Rema 1000.")
        d.wait(4)
        d.clear(search)
        d.type(search, "avokado")
        d.caption("Multipacks show the price of one item.")
        d.wait(3.5)
        d.clear(search)
        d.caption()
        d.wait(0.8)

        # 6. Pipeline, 7. End
        d.goto("/__slides?s=pipeline", cursor=False)
        d.wait(12)
        d.goto("/__slides?s=end", cursor=False)
        d.wait(6)

        video = page.video.path()
        context.close()
        browser.close()
        print(video)


main()
