from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base


def utcnow() -> datetime:
    """Current UTC time without tzinfo, matching the timestamps already stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Receipt(Base):
    __tablename__ = "receipts"

    id = Column(Integer, primary_key=True)
    image_filename = Column(String, nullable=False)
    store_chain = Column(String)        # "Rema 1000"
    store_location = Column(String)     # "Grünerløkka"
    region = Column(String)             # "Oslo"
    purchase_date = Column(Date)
    uploaded_at = Column(DateTime, default=utcnow)
    items = relationship("ReceiptItem", back_populates="receipt")

class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)   # "Dairy"
    products = relationship("Product", back_populates="category")

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"))
    name = Column(String, nullable=False)   # "Milk"
    category = relationship("Category", back_populates="products")
    items = relationship("ReceiptItem", back_populates="product")

class ReceiptItem(Base):
    __tablename__ = "receipt_items"

    id = Column(Integer, primary_key=True)
    receipt_id = Column(Integer, ForeignKey("receipts.id"))
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    raw_name = Column(String)       # "GILDE KJØTTDEIG 400G" — what receipt actually said
    canonical_name = Column(String) # "chicken mince" — normalized
    category = Column(String, nullable=True)  # "Godteri", "Kjøtt", "Meieri" etc
    quantity = Column(Float)
    unit = Column(String)           # "g", "ml", "stk"
    pack_size = Column(Integer, default=1)
    total_price = Column(Float, nullable=True)
    price_per_unit = Column(Float, nullable=True)
    is_member_price = Column(Integer, default=0)  # boolean
    receipt = relationship("Receipt", back_populates="items")
    product = relationship("Product", back_populates="items")
    deal = Column(String, nullable=True)
    brand = Column(String, nullable=True)
    variant = Column(String, nullable=True)
    confidence = Column(String, default="high")

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    filename = Column(String, nullable=False)
    status = Column(String, default="pending")  # pending, processing, done, failed
    attempts = Column(Integer, default=0)
    error = Column(String, nullable=True)
    # Gemini's answer exactly as returned, and the model that gave it, kept
    # even when it fails validation (see app/migrations.py)
    raw_response = Column(String, nullable=True)
    model = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Flyer(Base):
    """One imported kundeavis (a chain's weekly offers), with Gemini's raw
    answer kept like jobs.raw_response, so offers can be re-read later."""
    __tablename__ = "flyers"

    id = Column(Integer, primary_key=True)
    chain = Column(String, nullable=False)  # grouped name, see basket.chain_name
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=False)
    source = Column(String)                 # file name or URL it came from
    raw_response = Column(String)
    model = Column(String)
    created_at = Column(DateTime, default=utcnow)
    offers = relationship("FlyerOffer", back_populates="flyer", cascade="all, delete-orphan")


class FlyerOffer(Base):
    __tablename__ = "flyer_offers"

    id = Column(Integer, primary_key=True)
    flyer_id = Column(Integer, ForeignKey("flyers.id"), nullable=False)
    raw_name = Column(String)
    canonical_name = Column(String, nullable=False)
    brand = Column(String)
    variant = Column(String)
    category = Column(String)
    quantity = Column(Float)
    unit = Column(String)
    pack_size = Column(Integer, default=1)
    total_price = Column(Float)             # for pack_size items, as advertised
    price_per_unit = Column(Float)
    is_member_price = Column(Integer, default=0)
    deal = Column(String)                   # "3 for 2", "2 for 50,-"
    pre_price = Column(Float)               # the "før" price the flyer claims
    flyer = relationship("Flyer", back_populates="offers")
