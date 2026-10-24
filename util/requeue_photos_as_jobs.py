import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app import models
from app.config import UPLOAD_DIR

db = SessionLocal()

# Photos, and receipts uploaded as PDF, CSV or JSON files
images = [p for p in UPLOAD_DIR.iterdir() if p.suffix in (".jpg", ".pdf", ".csv", ".json")]
print(f"Found {len(images)} uploads")

for img in images:
    existing = db.query(models.Job).filter(models.Job.filename == img.name).first()
    if not existing:
        job = models.Job(filename=img.name)
        db.add(job)
        print(f"Queued: {img.name}")
    else:
        print(f"Skipped (already exists): {img.name}")

db.commit()
db.close()
print("Done")
