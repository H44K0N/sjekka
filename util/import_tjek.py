"""Imports this week's grocery flyers from eTilbudsavis (Tjek): every
catalog near a point from a chain Sjekka knows (Kiwi, Rema 1000, Meny,
Extra, ...). Names go through Gemini, one text-only call per flyer; with
--no-gemini, names already in the database are matched instead.

    python util/import_tjek.py                  # around Oslo
    python util/import_tjek.py --lat 60.39 --lng 5.32 --radius 30000   # Bergen
    python util/import_tjek.py --chain Kiwi --no-gemini

On the server, from /opt/sjekka:

    sudo docker compose exec app python util/import_tjek.py

Running it again replaces each flyer's offers. Check the terms of the source
before running it on a schedule.
"""
import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models, tjek  # noqa: E402
from app.basket import chain_name  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402


def main(argv=None, client=None, namer=None) -> int:
    parser = argparse.ArgumentParser(description="Import grocery flyers from eTilbudsavis")
    parser.add_argument("--lat", type=float, default=59.9139)
    parser.add_argument("--lng", type=float, default=10.7522)
    parser.add_argument("--radius", type=int, default=20000, help="metres")
    parser.add_argument("--chain", action="append", help="only this chain (can be repeated)")
    parser.add_argument("--no-gemini", action="store_true", help="match known product names instead")
    args = parser.parse_args(argv)

    models.Base.metadata.create_all(engine)
    client = client or httpx.Client(timeout=30, headers={"User-Agent": "sjekka (+https://sjekka.tischbein.dev)"})
    wanted = {chain_name(c) for c in args.chain or []}
    failed = 0
    with SessionLocal() as db:
        if namer is None and args.no_gemini:
            known = [n for (n,) in db.query(models.ReceiptItem.canonical_name).distinct() if n]
            namer = lambda offers: tjek.matched_names(offers, known)  # noqa: E731
        namer = namer or tjek.gemini_names
        for catalog in tjek.grocery_catalogs(client, args.lat, args.lng, args.radius):
            chain = chain_name(catalog["branding"]["name"])
            if wanted and chain not in wanted:
                continue
            label = f"{chain} «{catalog.get('label') or catalog['id']}»"
            try:
                flyer = tjek.import_catalog(db, catalog, tjek.catalog_offers(client, catalog["id"]), namer)
            except Exception as e:  # report it and go on with the next flyer
                db.rollback()
                print(f"{label}: {e}", file=sys.stderr)
                failed += 1
                continue
            if flyer:
                print(f"{label}: {len(flyer.offers)} offers, {flyer.valid_from} to {flyer.valid_to}")
            else:
                print(f"{label}: no priced offers")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
