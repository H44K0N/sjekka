from google import genai
from google.genai import types
from pathlib import Path
from app.config import GEMINI_API_KEY, GEMINI_MODEL
from app.schema import ExtractedFlyer, ExtractedReceipt

client = genai.Client(api_key=GEMINI_API_KEY)

# Shared with the flyer prompt, so offers get the same product names as
# receipts and can be compared with them.
DEFINITIONS = """## DEFINITIONS

**canonical_name**: The generic product name, NO brand, NO size, NO variant.
  - "GILDE KJØTTDEIG 400G" → "Kjøttdeig"
  - "TINE LETTMELK 1L" → "Lettmelk"
  - "LABAN SEIGMENN 260G" → "Seigmenn"
  - "JACOBS TRØFFELSALAMI 80G" → "Trøffelsalami"
  - Always Title Case. Always generic. Never include brand or size.

**brand**: The brand name only, or null if unknown/store brand.
  - "GILDE KJØTTDEIG" → "Gilde"
  - "TINE LETTMELK" → "Tine"
  - "XTRA KJØTTDEIG" → "Xtra"
  - "KJØTTDEIG" (no brand visible) → null

**variant**: Any meaningful differentiator beyond the base product, or null.
  - fat percentage: "3.5%", "0.5% laktosefri"
  - cut/type: "Angus", "Spicy", "Original"
  - Only include if it meaningfully affects the product comparison.

**quantity**: The size/amount of ONE single unit.
  - 260g bag → quantity: 260, unit: "g"
  - 1 litre → quantity: 1000, unit: "ml"
  - Single item with no size → quantity: null, unit: "stk"
  - Multipack sold as one item, "Yoghurt 4x150g" → quantity: 600, unit: "g" (the whole package)

**pack_size**: How many units were purchased on this line item.
  - Bought 1 → pack_size: 1
  - Deal "2 x 49kr" → pack_size: 2
  - Never use pack_size for contents of a single package

"""

PROMPT = f"""
You are a data extraction specialist for Norwegian grocery receipts.
Your only job is to extract structured data from receipt images accurately.

{DEFINITIONS}**total_price**: Exact amount charged on the receipt for this line.


**confidence**: Your confidence in the extracted data.
  - "high": receipt is clear, all fields certain
  - "low": receipt is blurry, truncated, or ambiguous

## RULES

1. Return ONLY valid JSON. No markdown, no explanation, no code fences.
2. SKIP these items entirely — do not include them:
   - Deposit/pant lines ("PANT", "PANT FLASKE" etc.)
   - Bag/carrier charges ("BÆREPOSE", "HANDLEPOSE" etc.)
   - Loyalty points, cashback lines
   - Any line that is not a physical product
3. If the same item appears multiple times at the same price with no deal → return ONCE, pack_size: 1.
4. If multiple units purchased as a deal → return ONCE with correct pack_size and deal description.
5. canonical_name MUST be Title Case and generic — no brands, no sizes.
6. category MUST be exactly one of the allowed values.
7. If quantity AND unit are both unreadable → set both to null. Do NOT guess.
8. If confidence is low on an item, still include it but set confidence: "low".

## ALLOWED CATEGORIES
Meieri, Kjøtt, Fisk, Frukt og grønt, Bakeri, Godteri, Snacks, Drikke, Frys, Hermetikk, Hygiene, Rengjøring, Annet

## FEW-SHOT EXAMPLES

Receipt line: "GILDE KJØTTDEIG 400G  89.00"
→ {{
  "raw_name": "GILDE KJØTTDEIG 400G",
  "canonical_name": "Kjøttdeig",
  "brand": "Gilde",
  "variant": null,
  "category": "Kjøtt",
  "quantity": 400,
  "unit": "g",
  "pack_size": 1,
  "total_price": 89.00,
  "is_member_price": false,
  "deal": null,
  "confidence": "high"
}}

Receipt line: "LABAN SEIGMENN 260G  x3  38.70"
→ {{
  "raw_name": "LABAN SEIGMENN 260G",
  "canonical_name": "Seigmenn",
  "brand": "Laban",
  "variant": null,
  "category": "Godteri",
  "quantity": 260,
  "unit": "g",
  "pack_size": 3,
  "total_price": 38.70,
  "is_member_price": false,
  "deal": "3 x kr 12.90",
  "confidence": "high"
}}

Receipt line: "TINE LETTMELK 0,5% 1L  24.90"
→ {{
  "raw_name": "TINE LETTMELK 0,5% 1L",
  "canonical_name": "Lettmelk",
  "brand": "Tine",
  "variant": "0.5%",
  "category": "Meieri",
  "quantity": 1000,
  "unit": "ml",
  "pack_size": 1,
  "total_price": 24.90,
  "is_member_price": false,
  "deal": null,
  "confidence": "high"
}}

Receipt line: "PANT  2.00"
→ SKIP — do not include

Receipt line: "BÆREPOSE  3.00"
→ SKIP — do not include

## OUTPUT

Follow the response schema. Use store_chain e.g. "Kiwi", "Rema 1000", "Meny"; store_location e.g. "Byporten" or null; region e.g. "Oslo" or null; purchase_date as YYYY-MM-DD or null; total is the receipt total.
"""

def extract_receipt(image_path: Path) -> str:
    """One Gemini call, returning its JSON answer as text. The worker stores
    that raw answer and validates it; retries are its job too."""
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_path.read_bytes(), mime_type="image/jpeg"),
            PROMPT,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedReceipt,
        ),
    )
    return response.text


FLYER_PROMPT = f"""
You are a data extraction specialist for Norwegian grocery flyers (kundeavis).
Extract every advertised product offer from the flyer.

{DEFINITIONS}
**total_price**: The advertised price for pack_size units, as a number.
  - "Kjøttdeig 400 g 39,90" → pack_size: 1, total_price: 39.90
  - "2 for 50,-" → pack_size: 2, total_price: 50.00, deal: "2 for 50"
  - "3 for 2" with a unit price of 20,- → pack_size: 3, total_price: 40.00, deal: "3 for 2"
  - No price you can tell for certain → null

**is_member_price**: true when the price needs a loyalty card or app (Trumf, Coop medlem, Æ, Rema-appen).

## RULES

1. Return ONLY valid JSON, following the response schema.
2. One entry per advertised product. If an offer covers several varieties ("flere varianter"), return it once, generically.
3. Skip non-grocery items (clothes, electronics, kitchenware), recipes and coupons without a product.
4. canonical_name MUST be Title Case and generic: no brands, no sizes. Use the same names a receipt would get.
5. category MUST be exactly one of: Meieri, Kjøtt, Fisk, Frukt og grønt, Bakeri, Godteri, Snacks, Drikke, Frys, Hermetikk, Hygiene, Rengjøring, Annet.
6. Read size from the text ("400 g", "1,5 l", "per kg"). Prices "per kg" → quantity: 1000, unit: "g", pack_size: 1, total_price: the kilo price.
7. store_chain e.g. "Kiwi", "Rema 1000", "Coop Extra". valid_from and valid_to as YYYY-MM-DD if the flyer says when the offers apply, else null.
"""

MIME_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".pdf": "application/pdf"}


def extract_flyer(path: Path) -> str:
    """One Gemini call for a flyer (a PDF or a page image), returning the raw
    JSON answer. Flyers are imported by hand (util/import_flyer.py)."""
    mime = MIME_TYPES.get(path.suffix.lower())
    if mime is None:
        raise ValueError(f"Unsupported flyer file: {path.name} (use PDF, JPEG, PNG or WebP)")
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[types.Part.from_bytes(data=path.read_bytes(), mime_type=mime), FLYER_PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedFlyer,
        ),
    )
    return response.text
