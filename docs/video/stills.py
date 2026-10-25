"""Stills from a film, to look at before rendering it all.

    python stills.py <build dir> <film> desktop|mobile <out dir> <seconds> [...]
    python stills.py <build dir> <film> desktop|mobile <out dir> sheet [every]

"sheet" makes one contact sheet of a frame every few seconds (default 2)."""
import math
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from render import open_film, serve

_, build, film, fmt, out = sys.argv[:5]
args = sys.argv[5:]
out = Path(out)
out.mkdir(parents=True, exist_ok=True)
size = {"width": 1080, "height": 1920} if fmt == "mobile" else {"width": 1920, "height": 1080}
port = serve(Path(build))
with sync_playwright() as p:
    browser, page = open_film(p, f"http://127.0.0.1:{port}/films/{film}.html?format={fmt}", size, 1)
    duration = page.evaluate("window.__film.duration")
    if args and args[0] == "sheet":
        every = float(args[1]) if len(args) > 1 else 2.0
        times = [round(i * every, 2) for i in range(int(duration / every) + 1)]
    else:
        times = [float(a) for a in args]
    shots = []
    for t in times:
        page.evaluate(f"window.__seek({t})")
        path = out / f"{film}-{fmt}-{t:06.2f}.png"
        page.screenshot(path=str(path))
        shots.append((t, path))
    browser.close()
print(f"{film} {fmt}: {duration:.1f} s")
if args and args[0] == "sheet":
    cols = 6 if fmt == "desktop" else 8
    tw = 320 if fmt == "desktop" else 200
    th = int(tw * size["height"] / size["width"])
    rows = math.ceil(len(shots) / cols)
    sheet = Image.new("RGB", (cols * tw, rows * (th + 22)), "#222")
    from PIL import ImageDraw
    d = ImageDraw.Draw(sheet)
    for i, (t, path) in enumerate(shots):
        im = Image.open(path).convert("RGB").resize((tw, th))
        x, y = (i % cols) * tw, (i // cols) * (th + 22)
        sheet.paste(im, (x, y + 22))
        d.text((x + 6, y + 4), f"{t:.1f}s", fill="#ddd")
        path.unlink()
    sheet.save(out / f"{film}-{fmt}-sheet.png")
    print(out / f"{film}-{fmt}-sheet.png")
