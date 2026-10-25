"""Adds columns that create_all can't: it creates missing tables but never
changes existing ones, such as the production database.

Each entry is applied once, when the column is missing. Only add columns
here; anything bigger needs a real migration tool."""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

COLUMNS = [
    # (table, column, SQL type) in the order they were introduced
    ("jobs", "raw_response", "TEXT"),
    ("jobs", "model", "VARCHAR"),
    ("jobs", "batch", "VARCHAR"),
    ("receipts", "total", "FLOAT"),
]


def migrate(engine: Engine) -> list[str]:
    """Adds any missing columns and returns what it added."""
    added = []
    with engine.begin() as conn:
        existing = {}
        tables = set(inspect(conn).get_table_names())
        for table, column, sql_type in COLUMNS:
            if table not in tables:  # create_all makes it, with every column
                continue
            if table not in existing:
                existing[table] = {c["name"] for c in inspect(conn).get_columns(table)}
            if column not in existing[table]:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}'))
                existing[table].add(column)
                added.append(f"{table}.{column}")
    return added
