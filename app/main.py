from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import date
import io, uuid

from app.config import UPLOAD_DIR, MAX_SIZE_MB, MAX_DIMENSION, JPEG_QUALITY
from app.database import engine, get_db
from app import models

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

# --- Pydantic schemas ---

class ItemIn(BaseModel):
    raw_name: str
    canonical_name: str
    quantity: float
    unit: str
    price: float
    price_per_unit: Optional[float] = None
    is_member_price: bool = False

class ReceiptIn(BaseModel):
    image_filename: str
    store_chain: str
    store_location: Optional[str] = None
    region: Optional[str] = None
    purchase_date: date
    items: List[ItemIn]

# --- Routes ---

@app.post("/upload")
async def upload_receipt(file: UploadFile = File(...)):
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

    return JSONResponse({"id": filename, "size_kb": filepath.stat().st_size // 1024})

@app.post("/receipts")
def save_receipt(data: ReceiptIn, db: Session = Depends(get_db)):
    receipt = models.Receipt(
        image_filename=data.image_filename,
        store_chain=data.store_chain,
        store_location=data.store_location,
        region=data.region,
        purchase_date=data.purchase_date,
    )
    db.add(receipt)
    db.flush()

    for item in data.items:
        db.add(models.ReceiptItem(
            receipt_id=receipt.id,
            raw_name=item.raw_name,
            canonical_name=item.canonical_name,
            quantity=item.quantity,
            unit=item.unit,
            price=item.price,
            price_per_unit=item.price_per_unit,
            is_member_price=int(item.is_member_price),
        ))

    db.commit()
    return {"receipt_id": receipt.id}

@app.get("/health")
async def health():
    return {"status": "ok"}

# --- Static files (must be last) ---

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
