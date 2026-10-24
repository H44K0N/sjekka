from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from sqlalchemy.orm import Session
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date, timedelta
import io
import time
import uuid
import asyncio
import sys
import logging

from app.config import (
    UPLOAD_DIR, MAX_SIZE_MB, MAX_DIMENSION, JPEG_QUALITY,
    UPLOADS_PER_IP_PER_HOUR, UPLOADS_PER_DAY, RUN_WORKER,
)
from app.database import engine, get_db
from app import models
from app import worker
from app import basket
from app import flyers
from app.migrations import migrate

# iPhones save photos as HEIC
register_heif_opener()
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF"}

logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
logger = logging.getLogger("sjekka")

models.Base.metadata.create_all(bind=engine)
for column in migrate(engine):
    logger.info(f"Added column {column}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(worker.run_worker()) if RUN_WORKER else None
    yield
    if task:
        task.cancel()


app = FastAPI(lifespan=lifespan)
# A week of flyers is a lot of JSON
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Fonts, logos and images rarely change, so browsers (and Cloudflare) may keep
# them for a week. Pages, CSS and JS keep the default and revalidate, since
# their names don't change between deploys.
LONG_CACHE = ("/fonts/", "/chains/", "/favicon.svg", "/og.png")


@app.middleware("http")
async def cache_static_assets(request: Request, call_next):
    response = await call_next(request)
    if response.status_code == 200 and request.url.path.startswith(LONG_CACHE):
        response.headers["Cache-Control"] = "public, max-age=604800"
    return response

# Every upload costs a Gemini call, so uploads are limited per client and per
# day. The per-client window lives in memory: there is only one app process.
recent_uploads: dict[str, deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    # cloudflared sets CF-Connecting-IP; the port is only published on
    # loopback, so the header can't be sent from outside Cloudflare.
    return request.headers.get("cf-connecting-ip") or request.client.host


def check_upload_limits(ip: str, db: Session):
    window = recent_uploads[ip]
    now = time.monotonic()
    while window and now - window[0] > 3600:
        window.popleft()
    if len(window) >= UPLOADS_PER_IP_PER_HOUR:
        raise HTTPException(429, "Du har lastet opp mange kvitteringer på kort tid. Prøv igjen om en time.")

    since = models.utcnow() - timedelta(days=1)
    if db.query(models.Job).filter(models.Job.created_at >= since).count() >= UPLOADS_PER_DAY:
        raise HTTPException(429, "Sjekka har nådd dagens grense for opplastinger. Prøv igjen i morgen.")


@app.post("/upload")
async def upload_receipt(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    ip = client_ip(request)
    check_upload_limits(ip, db)

    data = await file.read()

    if len(data) > MAX_SIZE_MB * 1024 * 1024:
        raise HTTPException(400, f"Bildet er for stort. Maks {MAX_SIZE_MB} MB.")

    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ALLOWED_FORMATS:
            raise HTTPException(400, "Bildet må være JPEG, PNG, WebP eller HEIC.")
        # Phones store rotation as an EXIF tag; apply it so Gemini sees the
        # receipt upright.
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, "Kunne ikke lese bildet. Prøv et annet bilde.") from None
    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

    filename = f"{uuid.uuid4()}.jpg"
    filepath = UPLOAD_DIR / filename
    img.save(filepath, format="JPEG", quality=JPEG_QUALITY, optimize=True)

    job = models.Job(filename=filename)
    db.add(job)
    db.commit()
    recent_uploads[ip].append(time.monotonic())

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

    store_counts = dict(
        db.query(models.ReceiptItem.canonical_name, func.count(func.distinct(models.Receipt.store_chain)))
        .join(models.Receipt, models.ReceiptItem.receipt_id == models.Receipt.id)
        .group_by(models.ReceiptItem.canonical_name)
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
            "store_count": store_counts.get(item.canonical_name, 1),
        })

    return products


@app.get("/products/{name}")
def get_product(name: str, db: Session = Depends(get_db)):
    """Every registered price of one product, oldest first, for the product page."""
    rows = (
        db.query(models.ReceiptItem, models.Receipt)
        .join(models.Receipt, models.ReceiptItem.receipt_id == models.Receipt.id)
        .filter(models.ReceiptItem.canonical_name == name)
        .order_by(models.Receipt.purchase_date, models.ReceiptItem.id)
        .all()
    )
    if not rows:
        raise HTTPException(404, "Fant ikke produktet.")

    latest = rows[-1][0]
    return {
        "canonical_name": name,
        "category": latest.category,
        "prices": [
            {
                "store_chain": receipt.store_chain,
                "store_location": receipt.store_location,
                "purchase_date": str(receipt.purchase_date) if receipt.purchase_date else None,
                "brand": item.brand,
                "variant": item.variant,
                "quantity": item.quantity,
                "unit": item.unit,
                "pack_size": item.pack_size,
                "total_price": item.total_price,
                "price_per_unit": item.price_per_unit,
                "is_member_price": bool(item.is_member_price),
                "deal": item.deal,
            }
            for item, receipt in rows
        ],
    }

@app.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    """How an upload is doing, for the thank-you page. The id is the random
    filename /upload returned, so it can't be guessed."""
    job = db.query(models.Job).filter_by(filename=job_id).first()
    if not job:
        raise HTTPException(404, "Fant ikke opplastingen.")

    result = {"status": job.status, "retrying": job.status == "pending" and job.attempts > 0}
    if job.status == "pending":
        result["queue_position"] = (
            db.query(models.Job)
            .filter(models.Job.status == "pending", models.Job.created_at <= job.created_at)
            .count()
        )
    if job.status == "done":
        receipt = db.query(models.Receipt).filter_by(image_filename=job.filename).first()
        if receipt:
            result["receipt"] = {
                "store_chain": receipt.store_chain,
                "purchase_date": str(receipt.purchase_date) if receipt.purchase_date else None,
                "items": [
                    {"canonical_name": i.canonical_name, "total_price": i.total_price, "pack_size": i.pack_size}
                    for i in receipt.items
                ],
            }
    return result


class ListItem(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    count: int = Field(default=1, ge=1, le=99)


class ShoppingList(BaseModel):
    items: list[ListItem] = Field(max_length=100)


@app.post("/basket")
def price_basket(shopping_list: ShoppingList, db: Session = Depends(get_db)):
    """What a shopping list costs at each chain, for the shopping-list page.
    Every price is read, since a chain's estimates depend on all its prices."""
    wanted: dict[str, int] = {}
    for item in shopping_list.items:
        wanted[item.name] = wanted.get(item.name, 0) + item.count
    rows = (
        db.query(models.ReceiptItem, models.Receipt)
        .join(models.Receipt, models.ReceiptItem.receipt_id == models.Receipt.id)
        .filter(models.ReceiptItem.total_price.isnot(None))
        .all()
    )
    deals = basket.flyer_deals(flyers.active_offers(db, date.today()))
    return basket.price_list(list(wanted.items()), basket.latest_offers(rows), deals)


@app.get("/offers")
def get_offers(navn: str | None = None, best: bool = False, db: Session = Depends(get_db)):
    """This week's flyer offers, each judged against what receipts say the
    product usually costs, best first. `navn` keeps one product's offers;
    `best` keeps only the best offer per product (for the product cards)."""
    offers = flyers.offer_list(db, date.today(), name=navn)
    if best:
        seen = set()
        offers = [o for o in offers if o["canonical_name"] not in seen and not seen.add(o["canonical_name"])]
    return offers


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

# Receipt photos are deliberately not served: they can show card digits.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
