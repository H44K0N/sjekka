import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app import models
from app.config import UPLOAD_DIR

db = SessionLocal()

images = list(UPLOAD_DIR.glob('*.jpg'))
print(f"Found {len(images)} images")

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
