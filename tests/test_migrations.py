import sqlite3

from sqlalchemy import create_engine, inspect

from app.migrations import migrate

OLD_JOBS = """CREATE TABLE jobs (id INTEGER PRIMARY KEY, filename VARCHAR NOT NULL, status VARCHAR,
    attempts INTEGER, error VARCHAR, created_at DATETIME, updated_at DATETIME)"""


def test_adds_missing_columns_and_keeps_data(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(OLD_JOBS)
    con.execute("INSERT INTO jobs (filename, status) VALUES ('a.jpg', 'done')")
    con.commit()
    con.close()

    engine = create_engine(f"sqlite:///{path}")
    assert migrate(engine) == ["jobs.raw_response", "jobs.model"]
    assert {"raw_response", "model"} <= {c["name"] for c in inspect(engine).get_columns("jobs")}
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT filename, status, raw_response FROM jobs").all() == [("a.jpg", "done", None)]

    assert migrate(engine) == []  # running again changes nothing
    engine.dispose()


def test_new_database_needs_nothing(db):
    from app.database import engine
    assert migrate(engine) == []
