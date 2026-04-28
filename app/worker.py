import asyncio
import logging
import sys
import json
from datetime import datetime
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app import models
from app.extractor import extract_receipt
from app.config import UPLOAD_DIR

logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
logger = logging.getLogger("sjekka.worker")

extraction_logger = logging.getLogger("sjekka.extraction")
extraction_handler = logging.FileHandler("/home/haakon/sjekka/logs/extractions.log")
extraction_handler.setFormatter(logging.Formatter("%(asctime)s\n%(message)s\n"))
extraction_logger.addHandler(extraction_handler)
extraction_logger.setLevel(logging.INFO)

MAX_ATTEMPTS = 10
POLL_INTERVAL = 5


async def process_job(job: models.Job, db: Session):
    filepath = UPLOAD_DIR / job.filename

    job.status = "processing"
    job.attempts += 1
    job.updated_at = datetime.utcnow()
    db.commit()

    try:
        data = await asyncio.to_thread(extract_receipt, filepath)

        extraction_logger.info(
            f"FILE: {job.filename}\n"
            f"STORE: {data.get('store_chain')} — {data.get('store_location')}\n"
            f"DATE: {data.get('purchase_date')}\n"
            f"ITEMS:\n{json.dumps(data.get('items', []), indent=2, ensure_ascii=False)}\n"
            f"TOTAL: {data.get('total')}\n"
            f"{'='*60}"
        )

        from datetime import date as date_type
        purchase_date = None
        if data.get("purchase_date"):
            try:
                purchase_date = date_type.fromisoformat(data["purchase_date"])
            except ValueError:
                pass

        receipt = models.Receipt(
            image_filename=job.filename,
            store_chain=data.get("store_chain"),
            store_location=data.get("store_location"),
            region=data.get("region"),
            purchase_date=purchase_date,
        )
        db.add(receipt)
        db.flush()

        for item in data.get("items", []):
            db.add(models.ReceiptItem(
                receipt_id=receipt.id,
                raw_name=item.get("raw_name"),
                canonical_name=item.get("canonical_name"),
                brand=item.get("brand"),
                variant=item.get("variant"),
                category=item.get("category"),
                quantity=item.get("quantity"),
                unit=item.get("unit"),
                pack_size=item.get("pack_size", 1),
                total_price=item.get("total_price"),
                price_per_unit=item.get("price_per_unit"),
                is_member_price=int(item.get("is_member_price", False)),
                deal=item.get("deal"),
                confidence=item.get("confidence", "high"),
            ))

        job.status = "done"
        job.updated_at = datetime.utcnow()
        db.commit()
        logger.info(f"✓ Done: {job.filename} — {receipt.store_chain}, {len(data.get('items', []))} items")

    except Exception as e:
        db.rollback()
        error_str = str(e)
        is_503 = "503" in error_str or "UNAVAILABLE" in error_str

        if is_503 and job.attempts < MAX_ATTEMPTS:
            job.status = "pending"
            logger.warning(f"⏳ 503 on {job.filename}, attempt {job.attempts}/{MAX_ATTEMPTS}, requeueing")
        else:
            job.status = "failed"
            job.error = error_str[:500]
            logger.error(f"✗ Failed: {job.filename} — {error_str[:100]}")

        job.updated_at = datetime.utcnow()
        db.commit()


async def run_worker():
    logger.info("🚀 Worker started")

    # Reset any jobs stuck in processing from a previous crash
    db = SessionLocal()
    stuck = db.query(models.Job).filter(models.Job.status == "processing").all()
    for job in stuck:
        job.status = "pending"
        logger.info(f"↩️ Reset stuck job: {job.filename}")
    db.commit()
    db.close()

    while True:
        db = SessionLocal()
        try:
            job = (
                db.query(models.Job)
                .filter(models.Job.status == "pending")
                .order_by(models.Job.created_at.asc())
                .first()
            )
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
