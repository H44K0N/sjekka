import sys
sys.path.insert(0, '/home/haakon/sjekka')

from app.database import SessionLocal
from app import models
import os
from pathlib import Path

UPLOAD_DIR = Path('/home/haakon/sjekka/uploads')

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
