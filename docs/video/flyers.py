"""Fetches this week's flyer offers as pictures for the promo film's wall of
red signs, from the catalogues Sjekka already reads (app/tjek.py).

    python flyers.py <out dir>

Writes f0.jpg ... f12.jpg and sizes.json: a few offers with a before-price
from each chain, the offer's own crop of the flyer page, taken in turns so
chains alternate. They are the chains' advertising, so they're only used to
make the film and are never committed; what ends up in the film is a few
seconds of them, blurred and dimmed behind the title. Which offers it gets
depends on the week."""
import io
import json
import os
import sys
from pathlib import Path

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("GEMINI_API_KEY", "unused")  # app.tjek imports the Gemini client

from app import tjek  # noqa: E402
from app.basket import chain_name  # noqa: E402

OUT = Path(sys.argv[1])
# How many from each chain, 13 in all
WANT = {"Extra": 3, "Rema 1000": 3, "Kiwi": 2, "Coop Mega": 3, "Coop Prix": 1, "Joker": 1}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pool: dict[str, list[tuple[str, str]]] = {chain: [] for chain in WANT}
    seen: set[str] = set()
    with httpx.Client(headers={"User-Agent": tjek.USER_AGENT}, timeout=30) as client:
        for catalog in tjek.grocery_catalogs(client, *tjek.OSLO_POINT, tjek.RADIUS_M):
            chain = chain_name(catalog["branding"]["name"])
            if chain not in pool:
                continue
            for offer in tjek.catalog_offers(client, catalog["id"]):
                if (offer.get("pricing") or {}).get("pre_price") and (offer.get("images") or {}).get("zoom"):
                    pool[chain].append((offer["id"], offer["images"]["zoom"]))
        picked: dict[str, list[Image.Image]] = {chain: [] for chain in WANT}
        # The quota from each chain, then more from whichever has some left
        for quota in (WANT, {chain: 99 for chain in WANT}):
            for chain, offers in pool.items():
                for _, url in sorted(offers):
                    if len(picked[chain]) >= quota[chain] or sum(map(len, picked.values())) >= 13:
                        break
                    if url in seen:
                        continue
                    seen.add(url)
                    image = Image.open(io.BytesIO(client.get(url).content)).convert("RGB")
                    # Big enough to look sharp, and not a long strip
                    if image.width >= 600 and 0.45 <= image.height / image.width <= 1.4:
                        picked[chain].append(image)
    # One from each chain in turn, so neighbours differ
    order = []
    for round_ in range(13):
        order += [picked[chain][round_] for chain in WANT if round_ < len(picked[chain])]
    if len(order) < 13:
        sys.exit(f"Only {len(order)} flyer offers found; the film needs 13")
    sizes = []
    for i, image in enumerate(order[:13]):
        image.save(OUT / f"f{i}.jpg", quality=92)
        sizes.append(image.size)
    (OUT / "sizes.json").write_text(json.dumps(sizes))
    print(f"Wrote {len(sizes)} flyer offers to {OUT}")


main()
