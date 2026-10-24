import asyncio
import logging
import re
import time
from collections import Counter
from datetime import date, timedelta

import httpx
from google.genai import errors
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models
from app.extractor import extract_receipt, extract_receipts
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


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def find_duplicate(db: Session, data: ExtractedReceipt) -> models.Receipt | None:
    """A receipt from the same store and day with exactly the same lines."""
    purchase_date = parse_date(data.purchase_date)
    if not data.store_chain or not purchase_date:
        return None
    lines = Counter((i.raw_name, i.total_price) for i in data.items)
    candidates = db.query(models.Receipt).filter_by(store_chain=data.store_chain, purchase_date=purchase_date)
    for receipt in candidates:
        if Counter((i.raw_name, i.total_price) for i in receipt.items) == lines:
            return receipt
    return None


def save_receipt(db: Session, job: models.Job, data: ExtractedReceipt) -> models.Receipt:
    receipt = models.Receipt(
        image_filename=job.filename,
        store_chain=data.store_chain,
        store_location=data.store_location,
        region=data.region,
        purchase_date=parse_date(data.purchase_date),
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
        raw = await asyncio.to_thread(extract_receipts if is_file else extract_receipt, filepath)
        job.raw_response, job.model = raw, GEMINI_MODEL
        extraction_logger.info(f"FILE: {job.filename} ({GEMINI_MODEL})\n{raw}\n{'=' * 60}")
        found = (ExtractedReceipts.model_validate_json(raw).receipts if is_file
                 else [ExtractedReceipt.model_validate_json(raw)])

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
