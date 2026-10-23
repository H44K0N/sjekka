import os
import tempfile

# app.config reads these at import time, so set them before anything imports app.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="sjekka-test-")
os.environ.setdefault("GEMINI_API_KEY", "test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main, models  # noqa: E402
from app.database import SessionLocal  # noqa: E402


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(db):
    # Not used as a context manager, so the startup event (the Gemini worker)
    # doesn't run.
    main.recent_uploads.clear()
    for table in reversed(models.Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    return TestClient(main.app)
