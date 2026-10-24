"""Receipts that come as files instead of photos: a PDF (an online order, a
receipt mailed by the shop), or a CSV or JSON export (from a store app or a
bank). They go through the same queue and the same Gemini reading as
photos; this module only decides whether an upload is one, and checks it.

The type is decided from the contents, not the file name, so a renamed file
can't slip past. Text is stored as UTF-8 whatever it came in."""
import csv
import json
import re

from app.config import MAX_SIZE_MB

# Kinds, as stored: the job's file name ends in one of these
KINDS = ("pdf", "csv", "json")
MAX_TEXT_BYTES = 1024 * 1024  # a receipt export is a few kilobytes
MAX_PDF_PAGES = 20            # a receipt or an order, not a catalogue


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
    stripped = data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    if stripped in (b"{", b"["):
        return "json"
    return None


def decode(data: bytes) -> str:
    """UTF-8, or Windows-1252, which is what Norwegian Excel saves CSV as."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="strict")


def check(kind: str, data: bytes) -> bytes:
    """The bytes to store for a receipt file, or ReceiptFileError."""
    if kind == "pdf":
        if len(data) > MAX_SIZE_MB * 1024 * 1024:
            raise ReceiptFileError(f"PDF-en er for stor. Maks {MAX_SIZE_MB} MB.")
        if b"%%EOF" not in data[-2048:]:
            raise ReceiptFileError("PDF-en ser ut til å være ødelagt. Prøv å lagre den på nytt.")
        # Rough but safe: each page object says /Type /Page (not /Pages)
        if len(re.findall(rb"/Type\s*/Page(?!s)", data)) > MAX_PDF_PAGES:
            raise ReceiptFileError(f"PDF-en har for mange sider. Maks {MAX_PDF_PAGES}.")
        return data

    if len(data) > MAX_TEXT_BYTES:
        raise ReceiptFileError("Filen er for stor. Maks 1 MB for CSV og JSON.")
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
