from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

# Everything the app writes (database, uploads, logs) lives under DATA_DIR,
# which is a mounted volume in production.
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", DATA_DIR / "uploads"))
LOG_DIR = Path(os.getenv("LOG_DIR", DATA_DIR / "logs"))
# Receipt files (PDF, CSV, JSON) as uploaded, kept to improve the reading
# (see KEEP_FILES_DAYS); photos are not kept
ORIGINALS_DIR = Path(os.getenv("ORIGINALS_DIR", DATA_DIR / "originals"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'sjekka.db'}")

for directory in (DATA_DIR, UPLOAD_DIR, LOG_DIR, ORIGINALS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Set to 0 to serve the site without processing receipts (browser tests)
RUN_WORKER = os.getenv("RUN_WORKER", "1") != "0"
# How often the worker looks for new flyers on eTilbudsavis; 0 turns it off
FLYER_REFRESH_HOURS = float(os.getenv("FLYER_REFRESH_HOURS", "6"))

UPLOADS_PER_IP_PER_HOUR = int(os.getenv("UPLOADS_PER_IP_PER_HOUR", "10"))
UPLOADS_PER_DAY = int(os.getenv("UPLOADS_PER_DAY", "200"))
# How long uploaded receipt files are kept (the privacy page says 12 months)
KEEP_FILES_DAYS = int(os.getenv("KEEP_FILES_DAYS", "365"))

MAX_SIZE_MB = 10
MAX_DIMENSION = 2000
JPEG_QUALITY = 75
