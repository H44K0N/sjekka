from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from sqlalchemy.orm import Session
import io, uuid, asyncio
from datetime import date as date_type

from app.config import UPLOAD_DIR, MAX_SIZE_MB, MAX_DIMENSION, JPEG_QUALITY
from app.database import engine, get_db
from app import models
from app.extractor import extract_receipt

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

# --- Routes ---

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

    asyncio.create_task(extract_and_save(filepath, filename, db))

    return JSONResponse({"id": filename})


async def extract_and_save(filepath, filename, db):
    try:
        data = await asyncio.to_thread(extract_receipt, filepath)

        purchase_date = None
        if data.get("purchase_date"):
            try:
                purchase_date = date_type.fromisoformat(data["purchase_date"])
            except ValueError:
                pass

        receipt = models.Receipt(
            image_filename=filename,
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
                category=item.get("category"),
		quantity=item.get("quantity"),
                unit=item.get("unit"),
                price=item.get("price"),
                price_per_unit=item.get("price_per_unit"),
                is_member_price=int(item.get("is_member_price", False)),
		deal=item.get("deal"),
            ))

        db.commit()
        print(f"✓ Saved receipt {filename} — {receipt.store_chain}, {len(data.get('items', []))} items")

    except Exception as e:
        print(f"✗ Extraction failed for {filename}: {e}")
        db.rollback()


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
            "category": item.category,
            "unit": item.unit,
            "quantity": item.quantity,
            "price": item.price,
            "price_per_unit": item.price_per_unit,
            "store_chain": receipt.store_chain,
            "purchase_date": str(receipt.purchase_date),
        })

    return products
# --- Static files (must be last) ---

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
