from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/home/haakon/sjekka/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MAX_SIZE_MB = 10
MAX_DIMENSION = 2000
JPEG_QUALITY = 75
