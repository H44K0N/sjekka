"""Renders a film's frames: opens its composition in Chromium, seeks the
timeline to each frame's time and screenshots it.

    python render.py <build dir> <film> desktop|mobile <frames dir> [--fps 60] [--scale 1.5] [--workers 6]

<build dir> is served over HTTP (films/, shots/, voice/, fonts/, music/,
chains/). Frames are split between several browsers, each a separate
process, and written as JPEGs; with --scale above 1 they're rendered larger
and scaled down in the final encode, which smooths text and edges. Writes
film.json (cues, sound effects, music, length) next to the frames for
mix.py. Only frames not already on disk are rendered, so a crashed run
picks up where it stopped (delete the folder after changing the film)."""
import argparse
import base64
import functools
import http.server
import json
import math
import os
import socketserver
import sys
import threading
import time
from multiprocessing import Process
from pathlib import Path

from playwright.sync_api import sync_playwright


def serve(root: Path) -> int:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    handler = functools.partial(Quiet, directory=str(root))
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def open_film(p, url, size, scale):
    browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None,
                                args=["--force-color-profile=srgb", "--disable-lcd-text", "--font-render-hinting=none"])
    page = browser.new_page(viewport=size, device_scale_factor=scale)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.goto(url)
    try:
        page.wait_for_function("window.__ready === true", timeout=60000)
    except Exception:
        sys.exit(f"The film didn't get ready: {errors}")
    return browser, page


def work(url, size, scale, fps, frames, out: Path, worker: int):
    with sync_playwright() as p:
        browser, page = open_film(p, url, size, scale)
        cdp = page.context.new_cdp_session(page)
        start = time.time()
        for n, i in enumerate(frames):
            path = out / f"{i:06d}.jpg"
            if path.exists():
                continue
            page.evaluate(f"window.__seek({i / fps})")
            shot = cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 94, "captureBeyondViewport": False})
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(base64.b64decode(shot["data"]))
            tmp.rename(path)
            if worker == 0 and n % 120 == 0:
                rate = (n + 1) / (time.time() - start)
                print(f"  frame {i} ({rate:.1f}/s per worker)", flush=True)
        browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("build")
    ap.add_argument("film")
    ap.add_argument("format")
    ap.add_argument("out")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--scale", type=float, default=1.5)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", help="render only these seconds, e.g. 10-14, for a look")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    port = serve(Path(a.build))
    size = {"width": 1080, "height": 1920} if a.format == "mobile" else {"width": 1920, "height": 1080}
    url = f"http://127.0.0.1:{port}/films/{a.film}.html?format={a.format}"

    with sync_playwright() as p:
        browser, page = open_film(p, url, size, 1)
        film = page.evaluate("window.__film")
        browser.close()
    (out / "film.json").write_text(json.dumps(film, indent=1, ensure_ascii=False))
    total = math.ceil(film["duration"] * a.fps)
    print(f"{a.film} ({a.format}): {film['duration']:.1f} s, {total} frames")

    frames = range(total)
    if a.only:
        lo, hi = (float(x) for x in a.only.split("-"))
        frames = range(int(lo * a.fps), min(total, int(hi * a.fps)))
    # Interleaved, so each worker gets a share of the busy and the quiet parts
    jobs = [Process(target=work, args=(url, size, a.scale, a.fps, list(frames)[k::a.workers], out, k)) for k in range(a.workers)]
    for j in jobs:
        j.start()
    for j in jobs:
        j.join()
    if any(j.exitcode for j in jobs):
        sys.exit("A render worker failed")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
