"""Imports a chain's weekly flyer (kundeavis): Gemini reads the offers from a
PDF or page images, and they're stored with the dates they apply. Each file
is one Gemini call. The files themselves are not kept.

    python util/import_flyer.py kiwi-uke-43.pdf
    python util/import_flyer.py --chain Kiwi --from 2026-10-20 --to 2026-10-26 side1.jpg side2.jpg

Chain and dates are read from the flyer when it says them; give them to be
sure. Importing the same file again replaces its offers.

On the server, from /opt/sjekka (the files must be inside the container):

    sudo docker compose cp kiwi-uke-43.pdf app:/tmp/
    sudo docker compose exec app python util/import_flyer.py /tmp/kiwi-uke-43.pdf
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.flyers import import_flyer  # noqa: E402


def main(argv=None, read=None) -> int:
    parser = argparse.ArgumentParser(description="Import offers from a kundeavis")
    parser.add_argument("files", nargs="+", type=Path, help="PDF, JPEG, PNG or WebP")
    parser.add_argument("--chain", help='e.g. "Kiwi", "Rema 1000" (default: read from the flyer)')
    parser.add_argument("--from", dest="valid_from", type=date.fromisoformat, help="first day, YYYY-MM-DD")
    parser.add_argument("--to", dest="valid_to", type=date.fromisoformat, help="last day, YYYY-MM-DD")
    args = parser.parse_args(argv)

    models.Base.metadata.create_all(engine)
    failed = 0
    with SessionLocal() as db:
        for path in args.files:
            if not path.is_file():
                print(f"{path}: no such file", file=sys.stderr)
                failed += 1
                continue
            try:
                kwargs = {"read": read} if read else {}
                flyer = import_flyer(db, path, args.chain, args.valid_from, args.valid_to, **kwargs)
            except Exception as e:  # report it and go on with the next file
                db.rollback()
                print(f"{path}: {e}", file=sys.stderr)
                failed += 1
                continue
            priced = sum(1 for o in flyer.offers if o.total_price is not None)
            print(f"{path}: {len(flyer.offers)} offers ({priced} with a price) from {flyer.chain}, "
                  f"{flyer.valid_from} to {flyer.valid_to}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
