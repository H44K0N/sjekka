"""Draws the demo receipt photo used in the upload scene."""
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont


def font_file(pattern):
    """A monospace font from fontconfig, or None to use Pillow's built-in one."""
    try:
        return subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True).stdout or None
    except FileNotFoundError:
        return None


def font(path, size):
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)


REG = font_file("monospace")
BOLD = font_file("monospace:bold")
W, H = 900, 1500
paper = Image.new("RGB", (W, H), "#f3efe6")
d = ImageDraw.Draw(paper)
f = font(REG, 30)
fb = font(BOLD, 44)
small = font(REG, 24)

y = 90
for text, font in [("REMA 1000", fb), ("GRÜNERLØKKA", f), ("Thorvald Meyers gate 40, Oslo", small)]:
    w = d.textlength(text, font=font)
    d.text(((W - w) / 2, y), text, font=font, fill="#1a1a1a")
    y += 60
y += 40
d.text((70, y), "23.10.2026  18:42      Kasse 2", font=small, fill="#333")
y += 60
d.line((70, y, W - 70, y), fill="#999", width=2)
y += 40
lines = [
    ("TACOKRYDDER 28G", "14,90"),
    ("TORTILLA ORIGINAL 8STK", "32,90"),
    ("RØMME LETT 300G", "26,90"),
    ("KJØTTDEIG STORFE 400G", "54,90"),
    ("AVOKADO 2STK", "49,00"),
    ("BÆREPOSE", "3,50"),
    ("PANT", "2,00"),
]
for name, price in lines:
    d.text((70, y), name, font=f, fill="#1a1a1a")
    w = d.textlength(price, font=f)
    d.text((W - 70 - w, y), price, font=f, fill="#1a1a1a")
    y += 56
y += 20
d.line((70, y, W - 70, y), fill="#999", width=2)
y += 40
d.text((70, y), "TOTALT", font=fb, fill="#1a1a1a")
w = d.textlength("184,10", font=fb)
d.text((W - 70 - w, y), "184,10", font=fb, fill="#1a1a1a")
y += 90
d.text((70, y), "BANKAXEPT        ************1234", font=small, fill="#333")
y += 120
msg = "Takk for handelen!"
d.text(((W - d.textlength(msg, font=f)) / 2, y), msg, font=f, fill="#333")

# Slight tilt on a dark table, like a phone photo
photo = Image.new("RGB", (1200, 1700), "#2b2622")
photo.paste(paper.rotate(-2, expand=True, fillcolor="#2b2622"), (120, 80))
photo.save(sys.argv[1], quality=90)
