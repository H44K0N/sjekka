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
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
