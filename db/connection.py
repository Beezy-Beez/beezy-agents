import os
from contextlib import contextmanager
import psycopg
import config


@contextmanager
def get_conn():
    pg_host = os.environ.get("PGHOST")
    pg_database = os.environ.get("PGDATABASE")
    pg_user = os.environ.get("PGUSER")
    pg_password = os.environ.get("PGPASSWORD")
    pg_port = os.environ.get("PGPORT")
    if all([pg_host, pg_database, pg_user, pg_password, pg_port]):
        database_url = f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_database}?sslmode=require"
    else:
        database_url = config.DATABASE_URL
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set.")
    conn = psycopg.connect(database_url, keepalives=1, keepalives_idle=10, keepalives_interval=5, keepalives_count=3)
    try:
        yield conn
    finally:
        conn.close()


def raw_connect():
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg.connect(config.DATABASE_URL)
