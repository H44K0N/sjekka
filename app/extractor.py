from google import genai
from google.genai import types
import json
import time
import random
from pathlib import Path
from app.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

PROMPT = """
You are extracting data from a Norwegian grocery receipt.
Return ONLY a JSON object, no markdown, no explanation.

{
  "store_chain": "e.g. Kiwi, Rema 1000, Meny",
  "store_location": "e.g. Byporten, Grünerløkka or null",
  "region": "e.g. Oslo or null",
  "purchase_date": "YYYY-MM-DD or null",
  "items": [
    {
      "raw_name": "exactly as printed on receipt",
      "canonical_name": "simple norwegian product name e.g. seigmenn, marsipanpølser",
      "category": "one of: Meieri, Kjøtt, Fisk, Frukt og grønt, Bakeri, Godteri, Snacks, Drikke, Frys, Hermetikk, Hygiene, Rengjøring, Annet",
      "quantity": 260,
      "unit": "g, ml, stk, l, kg",
      "price": 12.90,
      "price_per_unit": 4.96,
      "is_member_price": false,
      "deal": null
    }
  ],
  "total": 91.20
}

Rules:
- If the same item appears multiple times at the same price, return it ONCE only.
- If duplicates exist due to a deal (e.g. 2 for 1, 3 for 2, bundle discount), return ONE entry and describe the deal in the "deal" field e.g. "3 for 2" or "2 for 1".
- If no deal, set "deal" to null.
- For price_per_unit: calculate per 100g or per 100ml if unit is g or ml, otherwise null.
- If you cannot read a value clearly, use null.
"""

def extract_receipt(image_path: Path) -> dict:
    with open(image_path, "rb") as f:
        image_data = f.read()

    max_retries = 5
    base_delay = 2

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Part.from_bytes(data=image_data, mime_type="image/jpeg"),
                    PROMPT
                ]
            )

            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]

            return json.loads(text)

        except Exception as e:
            if attempt == max_retries - 1:
                raise

            if "503" not in str(e) and "UNAVAILABLE" not in str(e):
                raise

            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            print(f"503 error, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(delay)
