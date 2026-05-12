"""Applies schema.sql to the configured database.

Usage:
    python -m db.migrations
"""

from pathlib import Path

from .connection import raw_connect

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"


def apply_schema() -> None:
    sql = SCHEMA_PATH.read_text()
    with raw_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    print(f"Applied {SCHEMA_PATH.name} successfully.")


if __name__ == "__main__":
    apply_schema()
