from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

class Receipt(Base):
    __tablename__ = "receipts"

    id = Column(Integer, primary_key=True)
    image_filename = Column(String, nullable=False)
    store_chain = Column(String)        # "Rema 1000"
    store_location = Column(String)     # "Grünerløkka"
    region = Column(String)             # "Oslo"
    purchase_date = Column(Date)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
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
    price = Column(Float)
    price_per_unit = Column(Float)  # always stored per 100g or per 100ml
    is_member_price = Column(Integer, default=0)  # boolean
    receipt = relationship("Receipt", back_populates="items")
    product = relationship("Product", back_populates="items")
