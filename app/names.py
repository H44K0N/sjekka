"""One name per product and one spelling per brand.

A product is its canonical name: every receipt line and offer with the same
name is compared as one. The name is generic ("Pizza"), and the brand and
variant are fields of their own, so "the cheapest pizza right now" can be
answered across brands. Gemini doesn't always keep to that: before it was
given the names already in use, the same pizza came back as "Grandiosa",
"Pizza grandiosa" and "Pizza", and Tine as "TINE" and "Tine". Each such
product had a third of its prices.

So names are tidied as they're saved, and the ones saved before are tidied
at startup (repair_product_names):

- PRODUCTS renames names that were a brand, a plural or another spelling of
  a product that has a name already. Reviewed by hand from what's in use.
- Names and brands that differ only in spaces, case or punctuation
  ("Spiss paprika", "Spisspaprika") are given the spelling most used.
- BRANDS gives a brand one spelling ("Q-Meieriene" is "Q").

Nothing is deleted; only names change. A list or link with an old name
still finds the product (see resolve)."""
import logging
import re
from collections import Counter

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models

logger = logging.getLogger("sjekka.worker")

# Old name -> (name, brand, variant). A brand or variant of None keeps what
# the line has; one given replaces it, for names that were the brand.
PRODUCTS = {
    # The brand as the name
    "Grandiosa": ("Pizza", "Grandiosa", None),
    "Pizza grandiosa": ("Pizza", "Grandiosa", None),
    "Ristorante pizza": ("Pizza", "Dr. Oetker", "Ristorante"),
    "Pizza ristorante": ("Pizza", "Dr. Oetker", "Ristorante"),
    "Coca-cola": ("Brus", "Coca-Cola", None),
    "Cola": ("Brus", "Coca-Cola", None),
    "Pepsi max": ("Brus", "Pepsi Max", None),
    "Pepsi": ("Brus", "Pepsi Max", None),
    "Solo super": ("Brus", "Solo", "Super"),
    "Red bull": ("Energidrikk", "Red Bull", None),
    "Nugatti": ("Sjokoladepålegg", "Nugatti", None),
    "Fisherman's friend": ("Halspastiller", "Fisherman's Friend", None),
    "Norvegia": ("Gulost", "Tine", "Norvegia"),
    "Norvegia ost": ("Gulost", "Tine", "Norvegia"),
    "Laban seigmenn": ("Seigmenn", "Laban", None),
    "Litago melk": ("Sjokolademelk", "Litago", None),
    "Maldon salt": ("Salt", "Maldon", None),
    "Møllers tran": ("Tran", "Møllers", None),
    "Omo pluss color": ("Vaskemiddel", "Omo", "Pluss Color"),
    "Apetina": ("Fetaost", "Apetina", None),
    "Biola yoghurt": ("Yoghurt", "Tine", "Biola"),
    # A kind of the product, as a variant
    "Frokostegg": ("Egg", None, "Frokostegg"),
    "Gårdsegg": ("Egg", None, "Gårdsegg"),
    "Steinovnspizza": ("Pizza", None, "Steinovn"),
    "Pan pizza": ("Pizza", None, "Pan"),
    "Firkantpizza": ("Pizza", None, "Firkant"),
    "Iste fersken": ("Iste", None, "Fersken"),
    "Polarbrød havre": ("Polarbrød", None, "Havre"),
    "Grovt & godt brød": ("Grovbrød", None, "Grovt & Godt"),
    "Grovt & godt grovbrød": ("Grovbrød", None, "Grovt & Godt"),
    # Another word or spelling for the same thing
    "Feta": ("Fetaost", None, None),
    "White cheese": ("Fetaost", None, None),
    "Kyllingbryst": ("Kyllingfilet", None, None),
    "Kylling kjøttdeig": ("Kyllingkjøttdeig", None, None),
    "Energydrikk": ("Energidrikk", None, None),
    "Taco spice mix": ("Tacokrydder", None, None),
    "Taco kryddermix": ("Tacokrydder", None, None),
    "Tortillas": ("Tortilla", None, None),
    "Tortilla wraps": ("Tortilla", None, None),
    "Wraps": ("Tortilla", None, None),
    "Sweet chili sauce": ("Sweet chili saus", None, None),
    "Béarnaisesaus": ("Bearnaisesaus", None, None),
    "Græsk flatbrød": ("Gresk flatbrød", None, None),
    "Firekorn": ("4-korn", None, None),
    "Avocado nett": ("Avokado", None, None),
    "Håndsape": ("Håndsåpe", None, None),
    "Halslinser": ("Halspastiller", None, None),
    "Tomat": ("Tomater", None, None),
    "Tomat cherry": ("Cherrytomater", None, None),
    "Gulrøtter": ("Gulrot", None, None),
    "Gulrot snack": ("Gulrotsnack", None, None),
    "Snackgulrot": ("Gulrotsnack", None, None),
    "Snackgulrøtter": ("Gulrotsnack", None, None),
    "Spiss paprika": ("Spisspaprika", None, None),
    "Ruccula salat": ("Ruccula", None, None),
    "Salater": ("Salat", None, None),
    "Brød skåret": ("Brød", None, None),
    "Grovt brød": ("Grovbrød", None, None),
    "Bakevarer": ("Bakervarer", None, None),
    "Ferdigretter": ("Ferdigrett", None, None),
    "Frossenmat": ("Frossen matrett", None, None),
    "Protein shake": ("Proteinshake", None, None),
    "Snackspizza": ("Snackpizza", None, None),
    "Sjokomelk": ("Sjokolademelk", None, None),
    "Vafler/pannekaker": ("Vaffel-/pannekakemiks", None, None),
    "Pannekake / vaffelmiks": ("Vaffel-/pannekakemiks", None, None),
    "Pannekaker/vafler miks": ("Vaffel-/pannekakemiks", None, None),
    "Valnøttkjerner / pistasj": ("Valnøttkjerner/pistasjnøtter", None, None),
    "Toalettpapir/tørkepapir": ("Toalett- og tørkepapir", None, None),
    "Toalettpapir/tørkerull": ("Toalett- og tørkepapir", None, None),
    "Veggmalig": ("Veggmaling", None, None),
    # One or many of the same
    "Søtpoteter": ("Søtpotet", None, None),
    "Grillpølser": ("Grillpølse", None, None),
    "Kjøttpølser": ("Kjøttpølse", None, None),
    "Wienerpølser": ("Wienerpølse", None, None),
    "Burgere": ("Burger", None, None),
    "Muffin": ("Muffins", None, None),
    "Donut": ("Donuts", None, None),
    "Lunsjkaker": ("Lunsjkake", None, None),
    "Svinekotelett": ("Svinekoteletter", None, None),
    "Koteletter av svin": ("Svinekoteletter", None, None),
    "Koteletter": ("Svinekoteletter", None, None),
    "Sjokolader": ("Sjokolade", None, None),
    "Sjokoladebarer": ("Sjokoladebar", None, None),
    "Småsjokolader": ("Småsjokolade", None, None),
    "Godteripose": ("Godteposer", None, None),
    "Godteriposer": ("Godteposer", None, None),
    "Smågodterier": ("Smågodt", None, None),
    "Smågodt i løsvekt": ("Smågodt", None, None),
    "Batteri": ("Batterier", None, None),
    "Mikrofiberkluter": ("Mikrofiberklut", None, None),
    "Tørkeruller": ("Tørkerull", None, None),
}


def key(text: str) -> str:
    """A spelling without spaces, case or punctuation: "Dr. Greve" and
    "Dr.Greve" are one brand."""
    return re.sub(r"[\W_]+", "", text.lower())


RENAMED = {key(old): new for old, new in PRODUCTS.items()}


# key(brand as written) -> the brand
BRANDS = {key(old): new for old, new in {
    "Q-Meieriene": "Q", "Q Fersk": "Q",
    "Synnøve Finden": "Synnøve",
    "Rørosmeieriet": "Røros",
    "Hennig-Olsen Is": "Hennig-Olsen",
    "Sørlands": "Sørlandschips",
    "St. Maria": "Santa Maria", "St.Mar": "Santa Maria",
    "OEP": "Old El Paso",
    "Firs": "First Price", "First Pric": "First Price",
    "Aunt Mabel": "Aunt Mabel's", "Aunt Mabels": "Aunt Mabel's",
    "Dave & John's": "Dave & Jon's",
    "Stryhns Dansk": "Stryhns",
    "Honningc.": "Honningcentralen",
    "wwAsk Gård": "Ask Gård",
    "King Oskar": "King Oscar",
    "Fo": "Folkets",
    "Stabb": "Stabburet",
    "Peppes Pizza": "Peppes",
    "Purina Gourmet": "Gourmet", "Gourmet Purina": "Gourmet",
    "Arla Apetina": "Apetina",
    "Kronenbourg 1664": "Kronenbourg",
    "Lotus Biscoff": "Lotus",
    "Masalamagic": "Masala Magic",
    "Northpeak": "North Peak",
    "Ben & Jerry": "Ben & Jerry's",
    "G&F": "Grønn & Frisk", "Grønn&Frisk": "Grønn & Frisk",
    "Fersk&Ferdig": "Fersk & Ferdig",
    "Dr Oetker": "Dr. Oetker",
    "Meny": "Meny",
    "Tine": "Tine",
}.items()}


def tidy_text(text: str | None) -> str | None:
    text = " ".join((text or "").split())
    return text or None


def normalize_name(name: str) -> str:
    """'KJØTTDEIG ' and 'kjøttdeig' become 'Kjøttdeig', so they group as one product."""
    name = " ".join(name.split())
    return name[:1].upper() + name[1:].lower()


class Names:
    """The names and brands in use, to give a new line the spelling the
    product already has. Made once per receipt, flyer or request."""

    def __init__(self, db: Session):
        names, brands = Counter(), Counter()
        for model in (models.ReceiptItem, models.FlyerOffer):
            for name, n in db.query(model.canonical_name, func.count()).group_by(model.canonical_name):
                if name:
                    names[RENAMED.get(key(name), (name,))[0]] += n
            for brand, n in db.query(model.brand, func.count()).group_by(model.brand):
                if tidy_text(brand):
                    brands[tidy_text(brand)] += n
        # The spelling most used wins; a name PRODUCTS renames to always does
        targets = {new for new, _, _ in PRODUCTS.values()}
        self.names = {}
        for name, _ in names.most_common():
            if key(name) not in self.names or name in targets:
                self.names[key(name)] = name
        self.brands = {key(brand): brand for brand in BRANDS.values()}
        for brand, _ in brands.most_common():
            self.brands.setdefault(key(brand), brand)

    def tidy(self, name: str, brand: str | None = None, variant: str | None = None) -> tuple[str, str | None, str | None]:
        """The name, brand and variant to save a line as."""
        name = normalize_name(name)
        name, new_brand, new_variant = RENAMED.get(key(name), (name, None, None))
        name = self.names.get(key(name), name)
        brand = new_brand or tidy_text(brand)
        if brand:
            brand = BRANDS.get(key(brand), brand)
            brand = self.brands.get(key(brand), brand)
        return name, brand, new_variant or variant

    def resolve(self, name: str) -> str:
        """The product an old or loosely written name is now: what a saved
        list or a link with "Grandiosa" finds."""
        return self.tidy(name)[0]


def text_key(text: str | None) -> str:
    """A receipt line's text as a lookup key: "Banane  kg" and "BANANE KG" are one."""
    return " ".join((text or "").split()).casefold()


class SavedLines:
    """How each receipt text has been named before, to name it the same way
    again: the same line on next week's receipt gets the same card, without
    asking Gemini, whose wording varies from call to call. Made once per
    receipt or history."""

    def __init__(self, db: Session):
        seen: dict[str, Counter] = {}
        rows = (db.query(models.ReceiptItem.raw_name, models.ReceiptItem.canonical_name, models.ReceiptItem.brand,
                         models.ReceiptItem.variant, models.ReceiptItem.category, models.ReceiptItem.quantity,
                         models.ReceiptItem.unit, models.ReceiptItem.named_by)
                .filter(models.ReceiptItem.raw_name.isnot(None), models.ReceiptItem.canonical_name.isnot(None))
                .order_by(models.ReceiptItem.id))
        for text, *naming in rows:
            seen.setdefault(text_key(text), Counter())[tuple(naming)] += 1
        # The naming most lines of a text have; on a tie, the first saved
        self.lines = {k: c.most_common(1)[0][0] for k, c in seen.items()}

    def get(self, text: str | None) -> tuple | None:
        """(name, brand, variant, category, quantity, unit, named_by), or None for a new text."""
        return self.lines.get(text_key(text))


def repair_product_names(db: Session) -> int:
    """Gives lines and offers saved before tidying their product's name and
    brand. Safe to run again."""
    names = Names(db)
    fixed = 0
    for model in (models.ReceiptItem, models.FlyerOffer):
        for row in db.query(model).filter(model.canonical_name.isnot(None)):
            tidied = names.tidy(row.canonical_name, row.brand, row.variant)
            if tidied != (row.canonical_name, row.brand, row.variant):
                row.canonical_name, row.brand, row.variant = tidied
                fixed += 1
    db.commit()
    if fixed:
        logger.info(f"Gave {fixed} lines and offers their product's name")
    return fixed
