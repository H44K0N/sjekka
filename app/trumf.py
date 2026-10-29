"""Purchase histories from Trumf (NorgesGruppen's loyalty programme: Kiwi,
Meny, Joker, Spar), as JSON: a list of receipts, each with its date, store,
total and lines (name, quantity or weight, amount).

Everything but the product names is in the file and exact (each receipt's
lines add up to its total), so it's taken as it is, like flyer prices from
Tjek. Gemini only names the lines, as it would on a receipt: one text call
per part for the distinct line texts. That's cheaper than reading the
receipts, misses no lines, and never misreads a price.

    [{"dato": "23.10.2026", "kvitteringsnummer": "196075", "butikknavn": "MENY Sandvika Storsenter",
      "totaltBelop": "518.89", "varelinjer": [
        {"varenavn": "DUSJSÅPE FIKEN&SITRUS 275ML VI", "vareAntallVekt": "1.000", "vareBelop": "29.90"}, ...]}, ...]
"""
import hashlib
import json
import logging
import re
from datetime import datetime

from pydantic import BaseModel

from app.config import GEMINI_MODEL
from app.extractor import DEFINITIONS, client, json_answer, known_names
from app.schema import Category, ExtractedItem, ExtractedReceipt

logger = logging.getLogger("sjekka.worker")

# Marks the jobs read this way (see Job.model), so a history read before
# this parser existed can be told apart and read again
MODEL_PREFIX = "trumf:"
# Lines that are never a product, whatever Gemini says
NOT_A_PRODUCT = re.compile(r"\bPANT\b|BÆREPOSE|GAVEPOSE|GAVEINNPAKKING", re.IGNORECASE)


def receipts_in(doc) -> list[dict] | None:
    """The receipts, if this is a Trumf history (or a part of one): a list
    of receipts, maybe under one key, with Trumf's own field names."""
    if isinstance(doc, dict):
        lists = [v for v in doc.values() if isinstance(v, list)]
        doc = lists[0] if len(lists) == 1 else None
    if not isinstance(doc, list) or not doc:
        return None
    for r in doc:
        if not (isinstance(r, dict) and "dato" in r and "butikknavn" in r and isinstance(r.get("varelinjer"), list)):
            return None
        if any(not (isinstance(line, dict) and "varenavn" in line and "vareBelop" in line) for line in r["varelinjer"]):
            return None
    return doc


class LineName(BaseModel):
    index: int
    canonical_name: str
    brand: str | None = None
    variant: str | None = None
    category: Category
    quantity: float | None = None
    unit: str | None = None
    # False for a bag, a deposit, gift wrapping, a fee: not something bought to compare
    product: bool = True


class LineNames(BaseModel):
    lines: list[LineName]


NAMES_PROMPT = """
Lines from Norwegian grocery receipts follow, one per line as "index | text",
exactly as the till printed them (upper case, cut short). For each, give the
generic product name, brand, variant, category, and the size of one item, as
you would reading a receipt.

{definitions}
**unit**: "g", "ml" or "stk"; quantity in that unit, for one item, from the text
("275ML" → 275 ml, "1KG" → 1000 g, "EGG 12STK" → 12 stk). No size in the text → quantity null.

**product**: false for lines that aren't a product bought: bags, deposits (pant),
gift wrapping, cards and fees.

Return one entry per index, in order; skip nothing.

{lines}
"""


class LineReview(BaseModel):
    index: int
    canonical_name: str
    brand: str | None = None
    variant: str | None = None


class LineReviews(BaseModel):
    lines: list[LineReview]


# Naming a saved line again from scratch gives a slightly different answer
# each time: Chips for Potetgull, Brus for Kullsyredrikk, a new "Brus
# multipack". So a line is reviewed, not named again: its saved name stays
# unless it breaks a definition.
REVIEW_PROMPT = """
Lines from Norwegian grocery receipts follow, one per line as
"index | receipt text | name | brand | variant". The text is exactly as the till
printed it (upper case, cut short); the rest is how the line was named before,
with "-" for none.

Check each saved naming against the definitions below. Keep it, field for
field, unless it breaks one; most are fine and stay exactly as they are.
Change a field only to fix a clear mistake, such as:
  - a word of the text that says what the product is, dropped: "PULVERPADDER
    120G" saved as Godteri with no variant gets variant "Pulverpadder"
  - a brand that isn't written in the text, taken from another line or guessed
    from an abbreviation: "S.M.NAAN BR.GARLIC" saved with brand "Smak" gets none
  - a word in the variant that isn't in the text
Never swap a name for another that would also do (Chips and Potetgull, Brus
and Kullsyredrikk), never make a new name for a pack or size ("Brus
multipack"), and never correct spelling the text shares.

{definitions}
Return one entry per index, in order, with name, brand and variant: unchanged
or fixed.

{lines}
"""

# Which naming rules a line was named by: the definitions and these prompts.
# Changing any reviews every saved line again (see worker.rename_lines)
NAMING_RULES = hashlib.sha256((DEFINITIONS + NAMES_PROMPT + REVIEW_PROMPT).encode()).hexdigest()[:12]

# Lines named per Gemini call: a short answer is never cut off
NAMES_PER_CALL = 60


def gemini_names(texts: list[str], known=()) -> tuple[dict[int, LineName], str]:
    """One text-only Gemini call naming the given lines. Returns the names
    by index and the raw answer."""
    lines = "\n".join(f"{i} | {t}" for i, t in enumerate(texts))
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[NAMES_PROMPT.format(definitions=DEFINITIONS, lines=lines) + known_names(known)],
        config=json_answer(LineNames),
    )
    raw = response.text
    return {n.index: n for n in LineNames.model_validate_json(raw).lines}, raw


def gemini_review(saved: list[tuple[str, str, str | None, str | None]], known=()) -> tuple[dict[int, LineReview], str]:
    """One text-only Gemini call reviewing saved lines: (text, name, brand,
    variant) each. Returns the reviewed names by index and the raw answer."""
    lines = "\n".join(f"{i} | {text} | {name} | {brand or '-'} | {variant or '-'}"
                      for i, (text, name, brand, variant) in enumerate(saved))
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[REVIEW_PROMPT.format(definitions=DEFINITIONS, lines=lines) + known_names(known)],
        config=json_answer(LineReviews),
    )
    raw = response.text
    return {n.index: n for n in LineReviews.model_validate_json(raw).lines}, raw


def number(text) -> float:
    return float(str(text).replace(",", ".").replace(" ", ""))


def read(doc, namer=None) -> tuple[list[ExtractedReceipt], str]:
    """The receipts in a Trumf history, priced from the file and named by
    `namer`; and the namer's raw answer, to keep on the job."""
    receipts = receipts_in(doc)
    texts = sorted({line["varenavn"].strip() for r in receipts for line in r["varelinjer"]
                    if not NOT_A_PRODUCT.search(line["varenavn"])})
    namer = namer or gemini_names
    by_text, answers = {}, []
    for start in range(0, len(texts), NAMES_PER_CALL):
        chunk = texts[start:start + NAMES_PER_CALL]
        names, answer = namer(chunk)
        by_text.update({t: names.get(i) for i, t in enumerate(chunk)})
        answers.append(json.loads(answer))
    raw = json.dumps(answers, ensure_ascii=False)

    out = []
    for r in receipts:
        items = []
        for line in r["varelinjer"]:
            text = line["varenavn"].strip()
            amount = number(line["vareBelop"])
            name = by_text.get(text)
            if amount <= 0 or NOT_A_PRODUCT.search(text) or name is None or not name.product:
                continue
            amount_or_weight = number(line.get("vareAntallVekt") or 1)
            unit = name.unit if name.unit in ("g", "ml", "stk") else None
            if amount_or_weight != int(amount_or_weight):
                # Weighed at the till: the line is for this many kilos
                quantity, unit, pack = amount_or_weight * 1000, "g", 1
            else:
                quantity, pack = name.quantity, max(int(amount_or_weight), 1)
            items.append(ExtractedItem(
                raw_name=text, canonical_name=name.canonical_name, brand=name.brand, variant=name.variant,
                category=name.category, quantity=quantity, unit=unit, pack_size=pack, total_price=amount,
            ))
        total = number(r["totaltBelop"]) if r.get("totaltBelop") not in (None, "") else None
        lines_sum = round(sum(number(line["vareBelop"]) for line in r["varelinjer"]), 2)
        if total is not None and abs(lines_sum - total) > 0.05:
            logger.warning(f"Trumf receipt {r.get('kvitteringsnummer')}: lines add up to {lines_sum}, total says {total}")
        out.append(ExtractedReceipt(
            store_chain=r["butikknavn"], store_location=r["butikknavn"],
            purchase_date=datetime.strptime(r["dato"], "%d.%m.%Y").date().isoformat(),
            items=items, total=total,
        ))
    return out, raw


def is_history(text: str) -> bool:
    try:
        return receipts_in(json.loads(text)) is not None
    except json.JSONDecodeError:
        return False
