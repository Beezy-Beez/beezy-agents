from contextlib import contextmanager
import psycopg
import config


@contextmanager
def get_conn():
    if not config.DATABASE_URL:
        raise RuntimeError("POSTGRES_URL is not set.")
    conn = psycopg.connect(config.DATABASE_URL, keepalives=1, keepalives_idle=10, keepalives_interval=5, keepalives_count=3)
    try:
        yield conn
    finally:
        conn.close()


def raw_connect():
    if not config.DATABASE_URL:
        raise RuntimeError("POSTGRES_URL is not set.")
    return psycopg.connect(config.DATABASE_URL)
