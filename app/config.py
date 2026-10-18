from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

# Everything the app writes (database, uploads, logs) lives under DATA_DIR,
# which is a mounted volume in production.
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", DATA_DIR / "uploads"))
LOG_DIR = Path(os.getenv("LOG_DIR", DATA_DIR / "logs"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'sjekka.db'}")

for directory in (DATA_DIR, UPLOAD_DIR, LOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MAX_SIZE_MB = 10
MAX_DIMENSION = 2000
JPEG_QUALITY = 75
