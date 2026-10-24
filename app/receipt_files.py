"""Receipts that come as files instead of photos: a PDF (an online order, a
receipt mailed by the shop, a loyalty programme's data export), or a CSV or
JSON export (from a store app, Trumf or a bank). They go through the same
queue as photos; this module decides whether an upload is one, checks it,
and splits it.

A file can hold a whole purchase history, a year of receipts, which is more
than Gemini can read or answer in one go. So it's split into parts of about
PART_BYTES of text or PDF_PART_PAGES pages, each its own job:

- A JSON export with a list of purchases is split between purchases.
- A CSV, a JSON list of lines, or a PDF can't be split on receipt borders
  without knowing the format, so the parts overlap a little and Gemini is
  told to skip a receipt cut off at either end. Each receipt is whole in at
  least one part, and the duplicate check drops the repeats.

The type is decided from the contents, not the file name, so a renamed file
can't slip past. Text is stored as UTF-8 whatever it came in."""
import csv
import io
import json

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from app.config import MAX_SIZE_MB

KINDS = ("pdf", "csv", "json")
MAX_BYTES = MAX_SIZE_MB * 1024 * 1024
PART_BYTES = 30_000   # about 8,000 tokens: Gemini reads it and writes it all back
OVERLAP_ROWS = 40     # more than any one receipt has lines
PDF_PART_PAGES = 4    # parts overlap by one page
MAX_PDF_PAGES = 400
MAX_PARTS = 100       # every part is a Gemini call and counts towards the daily limit


class ReceiptFileError(ValueError):
    """The upload claims to be a receipt file but isn't a usable one. The
    message is shown to the user, so it's Norwegian."""


def kind_of(name: str | None, data: bytes) -> str | None:
    """"pdf", "csv" or "json" for a receipt file, None for anything else
    (which the caller then tries as a photo)."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    suffix = (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    if suffix in ("csv", "json"):
        return suffix
    # No telling extension: JSON is recognisable by its first character
    if data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] in (b"{", b"["):
        return "json"
    return None


def decode(data: bytes) -> str:
    """UTF-8, or Windows-1252, which is what Norwegian Excel saves CSV as."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="strict")


def pdf_pages(data: bytes) -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(data))
        if not reader.pages:
            raise ReceiptFileError("PDF-en har ingen sider.")
        return reader
    except (PdfReadError, ValueError, KeyError, OSError) as e:
        if isinstance(e, ReceiptFileError):
            raise
        raise ReceiptFileError("PDF-en ser ut til å være ødelagt. Prøv å lagre den på nytt.") from None


def check(kind: str, data: bytes) -> bytes:
    """The bytes to store for a receipt file, or ReceiptFileError."""
    if len(data) > MAX_BYTES:
        raise ReceiptFileError(f"Filen er for stor. Maks {MAX_SIZE_MB} MB.")

    if kind == "pdf":
        if len(pdf_pages(data).pages) > MAX_PDF_PAGES:
            raise ReceiptFileError(f"PDF-en har for mange sider. Maks {MAX_PDF_PAGES}.")
        return data

    try:
        text = decode(data)
    except UnicodeDecodeError:
        raise ReceiptFileError("Kunne ikke lese teksten i filen. Lagre den som UTF-8 og prøv igjen.") from None
    if "\x00" in text:
        raise ReceiptFileError("Filen ser ikke ut til å være tekst.")

    if kind == "json":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raise ReceiptFileError("Filen er ikke gyldig JSON.") from None
        if not isinstance(parsed, (dict, list)) or not parsed:
            raise ReceiptFileError("JSON-filen er tom.")
        return text.encode("utf-8")

    rows = [line for line in text.splitlines() if line.strip()]
    if len(rows) < 2:
        raise ReceiptFileError("CSV-filen trenger minst en overskrift og én vare.")
    try:
        csv.Sniffer().sniff("\n".join(rows[:20]), delimiters=",;\t|")
    except csv.Error:
        raise ReceiptFileError("Kunne ikke finne kolonnene i CSV-filen.") from None
    return text.encode("utf-8")


# ---- splitting ----

def windows(rows: list, size, overlap: int) -> list[list]:
    """Runs of rows of about PART_BYTES, each starting `overlap` rows before
    the previous one ended (but always moving on)."""
    parts, start = [], 0
    while start < len(rows):
        end, total = start, 0
        while end < len(rows) and (end == start or total + size(rows[end]) <= PART_BYTES):
            total += size(rows[end])
            end += 1
        parts.append(rows[start:end])
        if end >= len(rows):
            break
        start = max(end - overlap, start + max(1, (end - start) // 2))
    return parts


def purchases(doc) -> tuple[list, str | None, dict]:
    """The list in a JSON export that holds the purchases (or lines): the
    document itself, or its largest list. Returns it, its key, and the rest
    of the document (an account or period, kept with every part)."""
    if isinstance(doc, list):
        return doc, None, {}
    lists = {k: v for k, v in doc.items() if isinstance(v, list)}
    if not lists:
        return [doc], None, {}
    key = max(lists, key=lambda k: len(json.dumps(lists[k], ensure_ascii=False)))
    return lists[key], key, {k: v for k, v in doc.items() if k != key}


def is_receipt(entry) -> bool:
    """A purchase with its own lines, as opposed to a single line."""
    return isinstance(entry, dict) and any(isinstance(v, list) and v for v in entry.values())


def split(kind: str, stored: bytes) -> list[bytes]:
    """The parts to queue, in order; a small file is one part."""
    if kind == "pdf":
        reader = pdf_pages(stored)
        n = len(reader.pages)
        if n <= PDF_PART_PAGES:
            return [stored]
        parts = []
        for start in range(0, n - 1, PDF_PART_PAGES - 1):
            writer = PdfWriter()
            for page in reader.pages[start:start + PDF_PART_PAGES]:
                writer.add_page(page)
            out = io.BytesIO()
            writer.write(out)
            parts.append(out.getvalue())
            if start + PDF_PART_PAGES >= n:
                break
    elif len(stored) <= PART_BYTES:
        return [stored]
    elif kind == "json":
        entries, key, rest = purchases(json.loads(stored))
        size = lambda e: len(json.dumps(e, ensure_ascii=False).encode())  # noqa: E731
        runs = windows(entries, size, 0 if all(is_receipt(e) for e in entries) else OVERLAP_ROWS)
        parts = [json.dumps({**rest, key: run} if key else run, ensure_ascii=False).encode() for run in runs]
    else:
        lines = stored.decode().splitlines()
        header, rows = lines[0], [line for line in lines[1:] if line.strip()]
        parts = ["\n".join([header, *run]).encode() + b"\n"
                 for run in windows(rows, lambda r: len(r.encode()) + 1, OVERLAP_ROWS)]

    if len(parts) > MAX_PARTS:
        raise ReceiptFileError(f"Filen er for stor til å leses på én gang ({len(parts)} deler, maks {MAX_PARTS}). "
                               "Del den opp, for eksempel ett halvår om gangen.")
    return parts
