"""Writes every job with Gemini's raw answer as JSON Lines, oldest first, for
reworking categorisation or comparing models outside the app.

    python util/export_extractions.py > extractions.jsonl

On the server, from /opt/sjekka (the app's DATA_DIR is used):

    sudo docker compose exec -T app python util/export_extractions.py > extractions.jsonl

One line per job: filename, status, model, attempts, created_at, error, and
`answer` (the raw answer parsed as JSON, or the raw text if it isn't JSON).
Jobs from before raw answers were stored have no answer.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app import models  # noqa: E402


def rows(db):
    for job in db.query(models.Job).order_by(models.Job.created_at, models.Job.id):
        answer = job.raw_response
        if answer is not None:
            try:
                answer = json.loads(answer)
            except ValueError:
                pass  # keep invalid answers as text; they're interesting too
        yield {
            "filename": job.filename,
            "status": job.status,
            "model": job.model,
            "attempts": job.attempts,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "error": job.error,
            "answer": answer,
        }


def main(out=sys.stdout):
    db = SessionLocal()
    try:
        for row in rows(db):
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    finally:
        db.close()


if __name__ == "__main__":
    main()
