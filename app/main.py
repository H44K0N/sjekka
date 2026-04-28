from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from sqlalchemy.orm import Session
import io, uuid, asyncio, sys, logging
from datetime import date as date_type

from app.config import UPLOAD_DIR, MAX_SIZE_MB, MAX_DIMENSION, JPEG_QUALITY
from app.database import engine, get_db
from app import models
from app.extractor import extract_receipt
from app import worker

logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
logger = logging.getLogger("sjekka")

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(worker.run_worker())

@app.post("/upload")
async def upload_receipt(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if file.content_type not in ["image/jpeg", "image/png", "image/webp", "image/heic"]:
        raise HTTPException(400, "Only images allowed")

    data = await file.read()

    if len(data) > MAX_SIZE_MB * 1024 * 1024:
        raise HTTPException(400, f"Max {MAX_SIZE_MB}MB")

    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

    filename = f"{uuid.uuid4()}.jpg"
    filepath = UPLOAD_DIR / filename
    img.save(filepath, format="JPEG", quality=JPEG_QUALITY, optimize=True)

    job = models.Job(filename=filename)
    db.add(job)
    db.commit()

    logger.info(f"📥 Job created for {filename}")

    return JSONResponse({"id": filename})

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/products")
def get_products(db: Session = Depends(get_db)):
    from sqlalchemy import func

    subquery = (
        db.query(
            models.ReceiptItem.canonical_name,
            func.max(models.Receipt.purchase_date).label("latest_date")
        )
        .join(models.Receipt, models.ReceiptItem.receipt_id == models.Receipt.id)
        .group_by(models.ReceiptItem.canonical_name)
        .subquery()
    )

    items = (
        db.query(models.ReceiptItem, models.Receipt)
        .join(models.Receipt, models.ReceiptItem.receipt_id == models.Receipt.id)
        .join(subquery, (models.ReceiptItem.canonical_name == subquery.c.canonical_name) &
                        (models.Receipt.purchase_date == subquery.c.latest_date))
        .all()
    )

    seen = set()
    products = []
    for item, receipt in items:
        if item.canonical_name in seen:
            continue
        seen.add(item.canonical_name)
        products.append({
            "canonical_name": item.canonical_name,
            "brand": item.brand,
            "category": item.category,
            "unit": item.unit,
            "quantity": item.quantity,
            "pack_size": item.pack_size,
            "total_price": item.total_price,
            "price_per_unit": item.price_per_unit,
            "store_chain": receipt.store_chain,
            "purchase_date": str(receipt.purchase_date),
        })

    return products

@app.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    total_receipts = db.query(models.Receipt).count()
    total_items = db.query(models.ReceiptItem).count()
    pending_jobs = db.query(models.Job).filter(models.Job.status == "pending").count()
    return {
        "total_receipts": total_receipts,
        "total_items": total_items,
        "pending_jobs": pending_jobs,
    }

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
