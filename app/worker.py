import asyncio
import logging
import sys
from collections import Counter
from datetime import date, timedelta

import httpx
from google.genai import errors
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models
from app.extractor import extract_receipt
from app.schema import ExtractedItem, ExtractedReceipt
from app.config import UPLOAD_DIR, LOG_DIR

logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
logger = logging.getLogger("sjekka.worker")

extraction_logger = logging.getLogger("sjekka.extraction")
extraction_handler = logging.FileHandler(LOG_DIR / "extractions.log")
extraction_handler.setFormatter(logging.Formatter("%(asctime)s\n%(message)s\n"))
extraction_logger.addHandler(extraction_handler)
extraction_logger.setLevel(logging.INFO)

MAX_ATTEMPTS = 8
POLL_INTERVAL = 5
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

    try:
        data = await asyncio.to_thread(extract_receipt, filepath)

        extraction_logger.info(f"FILE: {job.filename}\n{data.model_dump_json(indent=2)}\n{'=' * 60}")

        duplicate = find_duplicate(db, data)
        if duplicate:
            job.status = "duplicate"
            job.error = f"Same as receipt {duplicate.id} ({duplicate.image_filename})"
            logger.info(f"Duplicate: {job.filename} is the same as receipt {duplicate.id}")
        else:
            receipt = save_receipt(db, job, data)
            job.status = "done"
            logger.info(f"Done: {job.filename} — {receipt.store_chain}, {len(data.items)} items")

        job.updated_at = models.utcnow()
        db.commit()

    except Exception as e:
        db.rollback()

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
    db.close()

    while True:
        db = SessionLocal()
        try:
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


if __name__ == "__main__":
    asyncio.run(run_worker())
