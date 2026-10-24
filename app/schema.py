from typing import Literal

from pydantic import BaseModel

# Gemini is given this as the response schema, so its output always has this
# shape, and it's validated again before anything is saved.

Category = Literal[
    "Meieri", "Kjøtt", "Fisk", "Frukt og grønt", "Bakeri", "Godteri", "Snacks",
    "Drikke", "Frys", "Hermetikk", "Hygiene", "Rengjøring", "Annet",
]


class ExtractedItem(BaseModel):
    raw_name: str
    canonical_name: str
    brand: str | None = None
    variant: str | None = None
    category: Category
    quantity: float | None = None
    unit: Literal["g", "ml", "stk"] | None = None
    pack_size: int = 1
    total_price: float | None = None
    is_member_price: bool = False
    deal: str | None = None
    confidence: Literal["high", "low"] = "high"


class ExtractedReceipt(BaseModel):
    store_chain: str | None = None
    store_location: str | None = None
    region: str | None = None
    purchase_date: str | None = None  # YYYY-MM-DD
    items: list[ExtractedItem]
    total: float | None = None


class ExtractedOffer(BaseModel):
    raw_name: str
    canonical_name: str
    brand: str | None = None
    variant: str | None = None
    category: Category
    quantity: float | None = None
    unit: Literal["g", "ml", "stk"] | None = None
    pack_size: int = 1
    total_price: float | None = None
    is_member_price: bool = False
    deal: str | None = None


class ExtractedFlyer(BaseModel):
    store_chain: str | None = None
    valid_from: str | None = None  # YYYY-MM-DD
    valid_to: str | None = None
    offers: list[ExtractedOffer]
