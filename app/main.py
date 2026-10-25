from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from sqlalchemy.orm import Session
from collections import Counter, defaultdict, deque
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from datetime import date, timedelta
import hashlib
import io
import re
import time
import uuid
import asyncio
import sys
import logging

from app.config import (
    UPLOAD_DIR, ORIGINALS_DIR, MAX_SIZE_MB, MAX_DIMENSION, JPEG_QUALITY,
    UPLOADS_PER_IP_PER_HOUR, UPLOADS_PER_DAY, RUN_WORKER, FLYER_REFRESH_HOURS,
)
from app.database import engine, get_db
from app import models
from app import worker
from app import basket
from app import flyers
from app import meals
from app import recipes
from app import names
from app import tjek
from app import receipt_files
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
    tasks = []
    if RUN_WORKER:
        tasks.append(asyncio.create_task(worker.run_worker()))
        if FLYER_REFRESH_HOURS > 0:
            tasks.append(asyncio.create_task(tjek.run_refresh()))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(lifespan=lifespan)
# A week of flyers is a lot of JSON
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Cloudflare keeps files for hours but always fetches pages fresh, so a deploy
# could pair new pages with old scripts, or show an old film under a new
# page. Everything a page, stylesheet or script points at by its absolute path
# (scripts, styles, fonts, logos, films, captions, posters) is therefore
# linked as /name?v=<hash>: a changed file gets a new address, and an
# addressed version never changes, so it's kept for a year. A stylesheet's
# hash covers what it points at too, so a new font can't hide behind an old
# stylesheet. Pages and scripts are rewritten as they're served; a request
# without the right hash is checked again every time.
STATIC = Path("static")
ASSET_EXTENSIONS = "js|css|png|jpg|jpeg|webp|svg|ico|woff2|mp4|vtt"
ASSET_REF = re.compile(rf'(["\'(])(/[\w./-]+\.(?:{ASSET_EXTENSIONS}))(?=["\')])')


def asset_path(url_path: str) -> Path | None:
    """The file in static/ a path names, if there is one."""
    path = (STATIC / url_path.lstrip("/")).resolve()
    if path.is_file() and path.is_relative_to(STATIC.resolve()) and path.suffix.lstrip(".") in ASSET_EXTENSIONS.split("|"):
        return path
    return None


@lru_cache(maxsize=None)
def file_hash(path: str, mtime_ns: int, size: int) -> str:
    """A file's contents hash, kept while the file is as it was."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:10]


def asset_version(url_path: str) -> str:
    path = asset_path(url_path)
    if path.suffix in (".css", ".js"):
        return hashlib.sha256(link_versioned_assets(path.read_text(encoding="utf-8"), url_path).encode()).hexdigest()[:10]
    stat = path.stat()
    return file_hash(str(path), stat.st_mtime_ns, stat.st_size)


def link_versioned_assets(text: str, own_path: str = "") -> str:
    """The text with every file it points at given its version."""
    def link(m):
        if m[2] == own_path or not asset_path(m[2]):
            return m[0]
        return f"{m[1]}{m[2]}?v={asset_version(m[2])}"
    return ASSET_REF.sub(link, text)


class Pages(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if not isinstance(response, FileResponse):
            return response
        url_path = "/" + str(path).lstrip("/")
        if str(response.path).endswith(".html"):
            with open(response.path, encoding="utf-8") as f:
                html = link_versioned_assets(f.read())
            return HTMLResponse(html, status_code=response.status_code, headers={"Cache-Control": "no-cache"})
        if str(response.path).endswith((".css", ".js")) and asset_path(url_path):
            with open(response.path, encoding="utf-8") as f:
                text = link_versioned_assets(f.read(), url_path)
            return Response(text, status_code=response.status_code, media_type=response.media_type)
        return response


@app.middleware("http")
async def cache_static_assets(request: Request, call_next):
    response = await call_next(request)
    # A partial answer (206) is what browsers ask for when they play a film
    if response.status_code not in (200, 206) or not asset_path(request.url.path):
        return response
    versioned = request.query_params.get("v") == asset_version(request.url.path)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "no-cache"
    return response

# Every upload costs a Gemini call, so uploads are limited per client and per
# day. The per-client window lives in memory: there is only one app process.
recent_uploads: dict[str, deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    # cloudflared sets CF-Connecting-IP; the port is only published on
    # loopback, so the header can't be sent from outside Cloudflare.
    return request.headers.get("cf-connecting-ip") or request.client.host


def jobs_today(db: Session) -> int:
    """Gemini calls queued in the last 24 hours: a photo is one, a receipt
    file one per part."""
    since = models.utcnow() - timedelta(days=1)
    return db.query(models.Job).filter(models.Job.created_at >= since).count()


def check_upload_limits(ip: str, db: Session):
    window = recent_uploads[ip]
    now = time.monotonic()
    while window and now - window[0] > 3600:
        window.popleft()
    if len(window) >= UPLOADS_PER_IP_PER_HOUR:
        raise HTTPException(429, "Du har lastet opp mange kvitteringer på kort tid. Prøv igjen om en time.")

    if jobs_today(db) >= UPLOADS_PER_DAY:
        raise HTTPException(429, "Sjekka har nådd dagens grense for opplastinger. Prøv igjen i morgen.")


@app.post("/upload")
async def upload_receipt(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    ip = client_ip(request)
    check_upload_limits(ip, db)

    data = await file.read()

    # A PDF, CSV or JSON receipt is stored as it is (checked, text as UTF-8)
    # and read by Gemini from the file; anything else must be a photo
    kind = receipt_files.kind_of(file.filename, data)
    if kind:
        try:
            stored = receipt_files.check(kind, data)
            parts = receipt_files.split(kind, stored)
        except receipt_files.ReceiptFileError as e:
            raise HTTPException(400, str(e)) from None
        # Every part is a Gemini call, so all of them must fit in today's limit
        if jobs_today(db) + len(parts) > UPLOADS_PER_DAY:
            raise HTTPException(429, "Filen er for stor for det Sjekka har igjen av dagens grense. Prøv igjen i morgen.")
        batch = str(uuid.uuid4())
        (ORIGINALS_DIR / f"{batch}.{kind}").write_bytes(stored)
        filenames = []
        for i, part in enumerate(parts, start=1):
            filename = f"{batch}-{i:03d}.{kind}"
            (UPLOAD_DIR / filename).write_bytes(part)
            db.add(models.Job(filename=filename, batch=batch))
            filenames.append(filename)
        db.commit()
        recent_uploads[ip].append(time.monotonic())
        logger.info(f"📥 Receipt file queued as {len(parts)} part(s), batch {batch}")
        return JSONResponse({"id": filenames[0], "batch": batch, "parts": len(parts)})

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
    return queue_job(db, ip, filename)


def queue_job(db: Session, ip: str, filename: str) -> JSONResponse:
    """Queues a stored upload for the worker and counts it towards the limits."""
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
    """One card per product. A product is generic ("Pizza") with brands and
    sizes inside it, so the card shows what the shopping list prices it by:
    the cheapest a shopper can pick now, at its usual size, at the cheapest
    chain (see basket.choices). Not the latest line, which might be a family
    pizza. A product only bought outside grocery chains shows its latest."""
    rows = (
        db.query(models.ReceiptItem, models.Receipt)
        .join(models.Receipt, models.ReceiptItem.receipt_id == models.Receipt.id)
        .filter(models.ReceiptItem.canonical_name.isnot(None))
        .all()
    )
    latest, chains, newest, brands = {}, {}, {}, {}
    for item, receipt in rows:
        name = item.canonical_name
        if item.brand:
            brands.setdefault(name, set()).add(item.brand)
        if receipt.store_chain:
            chains.setdefault(name, set()).add(receipt.store_chain)
        if receipt.purchase_date and (name not in newest or receipt.purchase_date > newest[name]):
            newest[name] = receipt.purchase_date
        if name not in latest or (receipt.purchase_date or date.min) >= (latest[name][1].purchase_date or date.min):
            latest[name] = (item, receipt)

    shown = dict(latest)
    for name, offers in basket.by_product(basket.latest_offers(rows)).items():
        price_of, _, lines = basket.choices(offers)
        if lines:
            best = min(lines.values(), key=price_of)
            shown[name] = (best.line, best.source)

    products = []
    for name, (item, receipt) in shown.items():
        products.append({
            "canonical_name": name,
            "brand": item.brand,
            # Every brand bought, so a search for one finds the product
            "brands": sorted(brands.get(name, ())),
            "variant": item.variant,
            "category": item.category,
            "unit": item.unit,
            "quantity": item.quantity,
            "pack_size": item.pack_size,
            "total_price": item.total_price,
            "price_per_unit": item.price_per_unit,
            "by_weight": worker.sold_by_weight(item.raw_name, item.unit),
            "store_chain": receipt.store_chain,
            "purchase_date": str(receipt.purchase_date),
            # When it was last bought anywhere, for sorting by newest
            "latest_date": str(newest[name]) if name in newest else None,
            "store_count": max(len(chains.get(name, ())), 1),
        })

    return products


@app.get("/products/{name}")
def get_product(name: str, db: Session = Depends(get_db)):
    """Every registered price of one product, oldest first, for the product
    page. A link with a name the product no longer has still finds it."""
    def prices_of(name):
        return (
            db.query(models.ReceiptItem, models.Receipt)
            .join(models.Receipt, models.ReceiptItem.receipt_id == models.Receipt.id)
            .filter(models.ReceiptItem.canonical_name == name)
            .order_by(models.Receipt.purchase_date, models.ReceiptItem.id)
            .all()
        )
    rows = prices_of(name)
    if not rows and (now := names.Names(db).resolve(name)) != name:
        name, rows = now, prices_of(now)
    if not rows:
        raise HTTPException(404, "Fant ikke produktet.")

    latest = rows[-1][0]
    # The line each chain is priced by, as on the shopping list, and that
    # price at the product's usual size, so stores selling it in different
    # sizes or units rank fairly
    price_of, size, lines = basket.choices({c: o for (_, c), o in basket.latest_offers(rows).items()})
    priced = {id(o.line): price_of(o) for o in lines.values()}
    return {
        "canonical_name": name,
        "category": latest.category,
        "size": size,
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
                "by_weight": worker.sold_by_weight(item.raw_name, item.unit),
                "is_member_price": bool(item.is_member_price),
                "deal": item.deal,
                "compared": round(priced[id(item)], 2) if id(item) in priced else None,
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


@app.get("/jobs/batch/{batch}")
def get_batch(batch: str, db: Session = Depends(get_db)):
    """How an uploaded receipt file is doing, over all its parts: for the
    thank-you page. The batch id is random, so it can't be guessed."""
    jobs = db.query(models.Job).filter_by(batch=batch).all()
    if not jobs:
        raise HTTPException(404, "Fant ikke opplastingen.")
    status = Counter(job.status for job in jobs)
    receipts = (
        db.query(models.Receipt)
        .filter(models.Receipt.image_filename.in_([job.filename for job in jobs]))
        .all()
    )
    dates = sorted(r.purchase_date for r in receipts if r.purchase_date)
    duplicates = sum(int(m[1]) for job in jobs if job.error and (m := re.match(r"\d+ saved, (\d+) duplicates", job.error)))
    return {
        "parts": len(jobs),
        "read": status["done"] + status["duplicate"],
        "failed": status["failed"],
        "waiting": status["pending"] + status["processing"],
        "receipts": len(receipts),
        "duplicates": duplicates + status["duplicate"],
        "items": sum(len(r.items) for r in receipts),
        "chains": dict(Counter(r.store_chain for r in receipts if r.store_chain).most_common()),
        "from": str(dates[0]) if dates else None,
        "to": str(dates[-1]) if dates else None,
    }


class ListItem(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    count: int = Field(default=1, ge=1, le=99)


class ShoppingList(BaseModel):
    items: list[ListItem] = Field(max_length=100)


@app.post("/basket")
def price_basket(shopping_list: ShoppingList, db: Session = Depends(get_db)):
    """What a shopping list costs at each chain, for the shopping-list page.
    Every price is read, since a chain's estimates depend on all its prices.
    A name on a saved list that a product no longer has is priced as the
    product, and `renamed` tells the page to update the list."""
    in_use = names.Names(db)
    wanted: dict[str, int] = {}
    renamed = {}
    for item in shopping_list.items:
        name = in_use.resolve(item.name)
        if name != item.name and name.lower() != item.name.lower():
            renamed[item.name] = name
        else:
            name = item.name
        wanted[name] = wanted.get(name, 0) + item.count
    rows = (
        db.query(models.ReceiptItem, models.Receipt)
        .join(models.Receipt, models.ReceiptItem.receipt_id == models.Receipt.id)
        .filter(models.ReceiptItem.total_price.isnot(None))
        .all()
    )
    deals = basket.flyer_deals(flyers.active_offers(db, date.today()))
    return {**basket.price_list(list(wanted.items()), basket.latest_offers(rows), deals), "renamed": renamed}


@app.get("/meals")
def get_meals(portions: int = 4, ids: str | None = None, db: Session = Depends(get_db)):
    """Dinner suggestions priced from receipts and this week's offers, cheapest
    first, and a plan for the week. `ids` is a chosen set of dinners to plan
    (after the visitor swapped one)."""
    rows = (
        db.query(models.ReceiptItem, models.Receipt)
        .join(models.Receipt, models.ReceiptItem.receipt_id == models.Receipt.id)
        .filter(models.ReceiptItem.total_price.isnot(None))
        .all()
    )
    deals = basket.flyer_deals(flyers.active_offers(db, date.today()))
    # Unknown names are ignored; with none left, the plan picks for itself
    chosen = [i for i in (ids or "").split(",") if i in recipes.BY_ID] or None
    return meals.suggest(basket.latest_offers(rows), deals, portions, chosen)


@app.get("/offers")
def get_offers(navn: str | None = None, best: bool = False, db: Session = Depends(get_db)):
    """This week's flyer offers, each judged against what receipts say the
    product usually costs, best first. `navn` keeps one product's offers;
    `best` keeps only the best offer per product (for the product cards)."""
    offers = flyers.offer_list(db, date.today(), name=navn and names.Names(db).resolve(navn))
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
app.mount("/", Pages(directory="static", html=True), name="static")
