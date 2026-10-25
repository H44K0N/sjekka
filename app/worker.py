import asyncio
import json
import logging
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta

import httpx
from google.genai import errors
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models
from app.extractor import extract_receipt, extract_receipts
from app import receipt_files, trumf
from app.basket import chain_name
from app.schema import ExtractedItem, ExtractedReceipt, ExtractedReceipts
from app.config import GEMINI_MODEL, KEEP_FILES_DAYS, LOG_DIR, ORIGINALS_DIR, UPLOAD_DIR

logger = logging.getLogger("sjekka.worker")

extraction_logger = logging.getLogger("sjekka.extraction")
extraction_handler = logging.FileHandler(LOG_DIR / "extractions.log")
extraction_handler.setFormatter(logging.Formatter("%(asctime)s\n%(message)s\n"))
extraction_logger.addHandler(extraction_handler)
extraction_logger.setLevel(logging.INFO)

MAX_ATTEMPTS = 8
POLL_INTERVAL = 5
# Photos of failed jobs are kept for retrying by hand, but not for ever
# (the privacy page promises 30 days)
FAILED_PHOTO_DAYS = 30
CLEANUP_INTERVAL = timedelta(hours=1)
# Rate limits, timeouts and Gemini being overloaded are worth retrying.
RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}


def is_retryable(e: Exception) -> bool:
    if isinstance(e, errors.APIError):
        return e.code in RETRYABLE_CODES
    return isinstance(e, (httpx.TransportError, ConnectionError, TimeoutError))


def retry_delay(attempts: int) -> timedelta:
    """30s, 1m, 2m, 4m ... capped at 30m: about an hour over all attempts."""
    return timedelta(seconds=min(30 * 2 ** (attempts - 1), 1800))


def normalize_name(name: str) -> str:
    """'KJØTTDEIG ' and 'kjøttdeig' become 'Kjøttdeig', so they group as one product."""
    name = " ".join(name.split())
    return name[:1].upper() + name[1:].lower()


def unit_price(item: ExtractedItem) -> float | None:
    """Price per 100 g/ml, or per item for stk."""
    if item.total_price is None:
        return None
    each = item.total_price / max(item.pack_size, 1)
    if item.unit in ("g", "ml") and item.quantity:
        return round(each / item.quantity * 100, 2)
    if item.unit == "stk":
        return round(each, 2)
    return None


# "Yoghurt kokos 4x150g": several of something sold as one package
MULTIPACK = re.compile(r"(\d+)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*(kg|g|ml|cl|dl|l)\b", re.IGNORECASE)
IN_BASE_UNIT = {"kg": ("g", 1000), "g": ("g", 1), "l": ("ml", 1000), "dl": ("ml", 100), "cl": ("ml", 10), "ml": ("ml", 1)}


def multipack_quantity(raw_name: str | None, quantity: float | None, unit: str | None, pack_size: int | None) -> float | None:
    """The contents of a whole multipack: "4x150g" holds 600 g. Gemini sometimes
    gives the size of one cup instead, which makes the price per 100 g four
    times too high. Only that exact mistake is corrected; a line bought as
    several items (pack_size > 1) is priced per item already."""
    match = MULTIPACK.search(raw_name or "")
    if not match or not quantity or (pack_size or 1) != 1:
        return quantity
    count = int(match[1])
    base, factor = IN_BASE_UNIT[match[3].lower()]
    one = float(match[2].replace(",", ".")) * factor
    if count < 2 or base != unit or abs(quantity - one) > 0.5:
        return quantity
    return one * count


def pieces_are_one_pack(text: str | None, pieces: int | None) -> bool:
    """Whether a flyer's piece count is a multipack ("Yoghurt 4-pk 4x150 g",
    "15 pk", "0,33 l x 15", "6 x 1,5 liter") rather than a multi-buy ("2 for
    30"). Tjek gives both as pieces; only the text tells them apart."""
    if not text or not pieces or pieces < 2:
        return False
    n = str(pieces)
    return bool(re.search(rf"(?<![\d,.]){n}\s*(?:-?\s*pk\b|-?\s*pakk|[x×](?!\w{{2}}))|[x×]\s*{n}(?![\d,.])", text, re.IGNORECASE))


# "Banane kg", "APPELSINER Pr kg. Løsvekt": weighed at the till. A size in
# the name ("Pizza 1kg", "Kjøttdeig 400 g, pr. kg 174,75") means a package.
PACKAGE_SIZE = re.compile(r"\d\s*(?:kg|g|gr)\b", re.IGNORECASE)


def sold_by_weight(raw_name: str | None, unit: str | None) -> bool:
    """Whether a line is loose produce, priced by the kilo rather than per
    package. Its weight is whatever one shopper took, so the kilo price is
    the price to show."""
    if unit != "g" or not raw_name:
        return False
    if "løsvekt" in raw_name.lower():
        return True
    return bool(re.search(r"\bkg\b", raw_name, re.IGNORECASE)) and not PACKAGE_SIZE.search(raw_name)


def repair_multipacks(db: Session) -> int:
    """Corrects multipacks saved before multipack_quantity existed. Safe to
    run again: a corrected row no longer matches."""
    fixed = 0
    for model in (models.ReceiptItem, models.FlyerOffer):
        for row in db.query(model).filter(model.pack_size == 1, model.raw_name.ilike("%x%")):
            quantity = multipack_quantity(row.raw_name, row.quantity, row.unit, row.pack_size)
            if quantity != row.quantity:
                logger.info(f"Multipack {row.raw_name!r}: {row.quantity:g} -> {quantity:g} {row.unit}")
                row.quantity = quantity
                row.price_per_unit = unit_price(row)
                fixed += 1
    # Flyer multipacks once stored as "N for X kr"
    for row in db.query(models.FlyerOffer).filter(models.FlyerOffer.pack_size > 1):
        if pieces_are_one_pack(row.raw_name, row.pack_size):
            logger.info(f"Multipack {row.raw_name!r}: {row.pack_size} for {row.total_price:g} -> one pack")
            if row.quantity:
                row.quantity *= row.pack_size
            row.pack_size, row.deal = 1, None
            row.price_per_unit = unit_price(row)
            fixed += 1
    db.commit()
    return fixed


# Enough to cover what's bought often, short enough to keep each call cheap
KNOWN_NAMES = 1500


def known_product_names(db: Session) -> list[str]:
    """The product names in use on receipts and in flyers, most common
    first, for Gemini to reuse (see extractor.known_names)."""
    counts = Counter()
    for model in (models.ReceiptItem, models.FlyerOffer):
        for name, n in db.query(model.canonical_name, func.count()).group_by(model.canonical_name):
            if name:
                counts[name] += n
    return [name for name, _ in counts.most_common(KNOWN_NAMES)]


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


# Two receipts are the same purchase when chain and day match and most
# prices do. A photo and a Trumf export of one shop differ in how the
# store and lines are written, and in a line or two (bags, deposits), so
# exact matching misses them. On production data, receipts from the same
# chain and day were either this similar or under 30 % alike.
SAME_PURCHASE = 0.8


def prices_of(items) -> Counter:
    return Counter(round(i.total_price, 2) for i in items if i.total_price is not None)


def same_purchase(a: Counter, b: Counter) -> bool:
    n = max(sum(a.values()), sum(b.values()))
    if n <= 2:  # too few lines to judge by share: all must match
        return n > 0 and a == b
    return sum((a & b).values()) / n >= SAME_PURCHASE


def is_duplicate(total_a: float | None, prices_a: Counter, total_b: float | None, prices_b: Counter) -> bool:
    """Whether two receipts from the same chain and day are one purchase:
    the same total to the øre and a price in common, or mostly the same
    prices (see same_purchase). The total matters because the lines saved
    depend on how they were read: a Trumf receipt read twice can keep a
    different line the second time ("Ukjent produkt" or not), and on a
    receipt of two or three lines that alone made it look new."""
    if total_a is not None and total_b is not None and abs(total_a - total_b) < 0.005:
        return bool(prices_a & prices_b) or not prices_a or not prices_b
    return same_purchase(prices_a, prices_b)


def find_duplicate(db: Session, data: ExtractedReceipt) -> models.Receipt | None:
    """A receipt already saved for the same purchase: same chain and day,
    and the same total or prices, however the store and lines were written."""
    purchase_date = parse_date(data.purchase_date)
    if not data.store_chain or not purchase_date:
        return None
    chain, prices = chain_name(data.store_chain), prices_of(data.items)
    for receipt in db.query(models.Receipt).filter_by(purchase_date=purchase_date):
        if chain_name(receipt.store_chain) == chain and is_duplicate(data.total, prices, receipt.total, prices_of(receipt.items)):
            return receipt
    return None


def fill_receipt_totals(db: Session) -> int:
    """Gives receipts read from a kept Trumf history the total printed in
    it, for those saved before receipts had one, so a history uploaded
    again is recognised by it (see is_duplicate). Each is matched to the
    file's receipt from the same store and day with the most prices in
    common. Safe to run again."""
    filled = 0
    for original in sorted(ORIGINALS_DIR.glob("*.json")):
        try:
            history = trumf.receipts_in(json.loads(original.read_bytes()))
        except json.JSONDecodeError:
            continue
        if not history:
            continue
        in_file: dict[tuple, list[tuple[float, Counter]]] = {}
        for r in history:
            if r.get("totaltBelop") in (None, ""):
                continue
            key = (datetime.strptime(r["dato"], "%d.%m.%Y").date(), r["butikknavn"])
            prices = Counter(round(trumf.number(line["vareBelop"]), 2) for line in r["varelinjer"])
            in_file.setdefault(key, []).append((trumf.number(r["totaltBelop"]), prices))
        filenames = [job.filename for job in db.query(models.Job).filter_by(batch=original.stem)]
        for receipt in db.query(models.Receipt).filter(models.Receipt.image_filename.in_(filenames),
                                                       models.Receipt.total.is_(None)):
            candidates = in_file.get((receipt.purchase_date, receipt.store_location), [])
            if not candidates:
                continue
            prices = prices_of(receipt.items)
            receipt.total = max(candidates, key=lambda c: sum((c[1] & prices).values()))[0]
            filled += 1
    db.commit()
    return filled


def reread_trumf_histories(db: Session) -> int:
    """Reads again the Trumf histories that were read before app/trumf.py
    existed (by Gemini reading every receipt, which missed lines and could
    cut off): their receipts go, and the kept original is split and queued
    again for the parser. A history is read the old way when one of its
    jobs has a model not marked trumf:; jobs queued again have no model
    until they're read, so this runs once per history."""
    reread = 0
    for original in sorted(ORIGINALS_DIR.glob("*.json")):
        batch = original.stem
        jobs = db.query(models.Job).filter_by(batch=batch).all()
        if not any(job.model and not job.model.startswith(trumf.MODEL_PREFIX) for job in jobs):
            continue
        data = original.read_bytes()
        try:
            if trumf.receipts_in(json.loads(data)) is None:
                continue
        except json.JSONDecodeError:
            continue
        filenames = [job.filename for job in jobs]
        receipts = db.query(models.Receipt).filter(models.Receipt.image_filename.in_(filenames)).all()
        for receipt in receipts:
            for item in receipt.items:
                db.delete(item)
            db.delete(receipt)
        for job in jobs:
            (UPLOAD_DIR / job.filename).unlink(missing_ok=True)
            db.delete(job)
        db.flush()
        parts = receipt_files.split("json", data)
        for i, part in enumerate(parts, start=1):
            filename = f"{batch}-{i:03d}.json"
            (UPLOAD_DIR / filename).write_bytes(part)
            db.add(models.Job(filename=filename, batch=batch))
        db.commit()
        logger.info(f"Reading Trumf history {batch} again: {len(receipts)} receipts removed, {len(parts)} parts queued")
        reread += 1
    return reread


def repair_chain_names(db: Session) -> int:
    """Gives receipts saved before save_receipt did it one name per chain
    ("MENY" and "MENY Sandvika Storsenter" become "Meny"). Safe to run again."""
    fixed = 0
    for receipt in db.query(models.Receipt).filter(models.Receipt.store_chain.isnot(None)):
        name = chain_name(receipt.store_chain)
        if name != receipt.store_chain:
            receipt.store_location = receipt.store_location or receipt.store_chain
            receipt.store_chain = name
            fixed += 1
    db.commit()
    if fixed:
        logger.info(f"Gave {fixed} receipts their chain's name")
    return fixed


def repair_duplicates(db: Session) -> int:
    """Removes receipts saved twice before find_duplicate knew a photo and a
    Trumf export of one shop are the same purchase. Keeps the first saved.
    Safe to run again."""
    removed = 0
    groups: dict[tuple, list[models.Receipt]] = {}
    for receipt in db.query(models.Receipt).filter(models.Receipt.purchase_date.isnot(None)).order_by(models.Receipt.id):
        groups.setdefault((chain_name(receipt.store_chain), receipt.purchase_date), []).append(receipt)
    for group in groups.values():
        kept: list[models.Receipt] = []
        for receipt in group:
            original = next((k for k in kept if is_duplicate(k.total, prices_of(k.items), receipt.total, prices_of(receipt.items))), None)
            if original is None:
                kept.append(receipt)
                continue
            logger.info(f"Duplicate receipt {receipt.id} ({receipt.image_filename}) of {original.id} "
                        f"({original.image_filename}), {receipt.store_chain} {receipt.purchase_date}: removed")
            for item in receipt.items:
                db.delete(item)
            db.delete(receipt)
            removed += 1
    db.commit()
    return removed


def save_receipt(db: Session, job: models.Job, data: ExtractedReceipt) -> models.Receipt:
    receipt = models.Receipt(
        image_filename=job.filename,
        # One name per chain ("KIWI 305 Kjørbo" is Kiwi); the store as written stays
        store_chain=chain_name(data.store_chain),
        store_location=data.store_location or data.store_chain,
        region=data.region,
        purchase_date=parse_date(data.purchase_date),
        total=data.total,
    )
    db.add(receipt)
    db.flush()

    for item in data.items:
        item.quantity = multipack_quantity(item.raw_name, item.quantity, item.unit, item.pack_size)
        db.add(models.ReceiptItem(
            receipt_id=receipt.id,
            raw_name=item.raw_name,
            canonical_name=normalize_name(item.canonical_name),
            brand=item.brand.strip() if item.brand else None,
            variant=item.variant,
            category=item.category,
            quantity=item.quantity,
            unit=item.unit,
            pack_size=max(item.pack_size, 1),
            total_price=item.total_price,
            price_per_unit=unit_price(item),
            is_member_price=int(item.is_member_price),
            deal=item.deal,
            confidence=item.confidence,
        ))
    return receipt


async def process_job(job: models.Job, db: Session):
    filepath = UPLOAD_DIR / job.filename

    job.status = "processing"
    job.attempts += 1
    job.updated_at = models.utcnow()
    db.commit()

    # A receipt file (PDF, CSV, JSON) can hold many receipts; a photo is one
    is_file = filepath.suffix.lower() in (".pdf", ".csv", ".json")
    raw = None
    try:
        known = known_product_names(db)
        history = trumf.receipts_in(json.loads(filepath.read_text(encoding="utf-8"))) if filepath.suffix == ".json" else None
        if history:
            # A Trumf history: prices from the file, only the names from Gemini
            found, raw = await asyncio.to_thread(trumf.read, history, lambda texts: trumf.gemini_names(texts, known))
            job.raw_response, job.model = raw, trumf.MODEL_PREFIX + GEMINI_MODEL
        else:
            raw = await asyncio.to_thread(extract_receipts if is_file else extract_receipt, filepath, known)
            job.raw_response, job.model = raw, GEMINI_MODEL
            found = (ExtractedReceipts.model_validate_json(raw).receipts if is_file
                     else [ExtractedReceipt.model_validate_json(raw)])
        extraction_logger.info(f"FILE: {job.filename} ({job.model})\n{raw}\n{'=' * 60}")

        saved = duplicates = 0
        for data in found:
            if is_file and not data.items:  # an empty entry in a history
                continue
            duplicate = find_duplicate(db, data)
            if duplicate:
                duplicates += 1
                job.error = f"Same as receipt {duplicate.id} ({duplicate.image_filename})"
                logger.info(f"Duplicate: in {job.filename}, the same as receipt {duplicate.id}")
                continue
            receipt = save_receipt(db, job, data)
            db.flush()  # so a repeat later in the same file is found as a duplicate
            saved += 1
            logger.info(f"Done: {job.filename} — {receipt.store_chain}, {len(data.items)} items")
        # Only duplicates (or, from a file part, nothing new): say so
        job.status = "done" if saved or (is_file and not duplicates) else "duplicate"
        if is_file:
            job.error = f"{saved} saved, {duplicates} duplicates" if duplicates else None

        job.updated_at = models.utcnow()
        db.commit()
        # The extracted data is saved, so the photo is no longer needed. It can
        # show personal details; photos of failed jobs stay for retrying.
        filepath.unlink(missing_ok=True)

    except Exception as e:
        db.rollback()
        if raw is not None:
            job.raw_response, job.model = raw, GEMINI_MODEL

        if is_retryable(e) and job.attempts < MAX_ATTEMPTS:
            job.status = "pending"
            logger.warning(
                f"{e.__class__.__name__} on {job.filename}, attempt {job.attempts}/{MAX_ATTEMPTS}, "
                f"retrying in {retry_delay(job.attempts)}"
            )
        else:
            job.status = "failed"
            if isinstance(e, ValidationError):
                job.error = f"Gemini returned an invalid receipt: {e}"[:500]
            else:
                job.error = str(e)[:500]
            logger.error(f"Failed: {job.filename} — {job.error[:100]}")

        job.updated_at = models.utcnow()
        db.commit()


def delete_old_originals() -> int:
    """Deletes kept receipt files older than KEEP_FILES_DAYS (the privacy
    page promises 12 months)."""
    cutoff = time.time() - KEEP_FILES_DAYS * 86400
    deleted = 0
    for path in ORIGINALS_DIR.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            deleted += 1
    if deleted:
        logger.info(f"Deleted {deleted} receipt files kept over {KEEP_FILES_DAYS} days")
    return deleted


def delete_old_failed_photos(db: Session) -> int:
    """Deletes photos of jobs that failed more than FAILED_PHOTO_DAYS ago."""
    cutoff = models.utcnow() - timedelta(days=FAILED_PHOTO_DAYS)
    deleted = 0
    for job in db.query(models.Job).filter(models.Job.status == "failed", models.Job.updated_at < cutoff):
        path = UPLOAD_DIR / job.filename
        if path.exists():
            path.unlink()
            deleted += 1
    if deleted:
        logger.info(f"Deleted {deleted} photos of jobs that failed over {FAILED_PHOTO_DAYS} days ago")
    return deleted


def next_job(db: Session) -> models.Job | None:
    """The oldest pending job whose retry delay has passed. Jobs waiting to
    retry don't hold up the ones behind them."""
    now = models.utcnow()
    pending = (
        db.query(models.Job)
        .filter(models.Job.status == "pending")
        .order_by(models.Job.created_at.asc())
    )
    for job in pending:
        if job.attempts == 0 or job.updated_at + retry_delay(job.attempts) <= now:
            return job
    return None


async def run_worker():
    logger.info("Worker started")

    # Reset any jobs stuck in processing from a previous crash
    db = SessionLocal()
    stuck = db.query(models.Job).filter(models.Job.status == "processing").all()
    for job in stuck:
        job.status = "pending"
        logger.info(f"Reset stuck job: {job.filename}")
    db.commit()
    repair_multipacks(db)
    repair_chain_names(db)
    fill_receipt_totals(db)
    repair_duplicates(db)
    reread_trumf_histories(db)
    db.close()

    last_cleanup = None
    while True:
        db = SessionLocal()
        try:
            if last_cleanup is None or models.utcnow() - last_cleanup > CLEANUP_INTERVAL:
                delete_old_failed_photos(db)
                delete_old_originals()
                last_cleanup = models.utcnow()
            job = next_job(db)
            if job:
                await process_job(job, db)
            else:
                await asyncio.sleep(POLL_INTERVAL)
        except Exception as e:
            logger.error(f"Worker error: {e}")
            await asyncio.sleep(POLL_INTERVAL)
        finally:
            db.close()
