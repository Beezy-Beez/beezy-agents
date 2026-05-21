from contextlib import contextmanager
import psycopg
import config

_WRONG_DB_HOST = "ep-royal-cell-aq3d2wj0-pooler.c-8.us-east-1.aws.neon.tech"


@contextmanager
def get_conn():
    if not config.NEON_DATABASE_URL:
        raise RuntimeError("NEON_DATABASE_URL is not set.")
    if _WRONG_DB_HOST in config.NEON_DATABASE_URL:
        raise RuntimeError(
            f"NEON_DATABASE_URL points at {_WRONG_DB_HOST}, which has an empty 'neondb' "
            "and is NOT the beezy-agents database. Update the Replit secret to the "
            "real Neon endpoint before any worker runs."
        )
    conn = psycopg.connect(config.NEON_DATABASE_URL, keepalives=1, keepalives_idle=10, keepalives_interval=5, keepalives_count=3)
    try:
        yield conn
    finally:
        conn.close()


def raw_connect():
    if not config.NEON_DATABASE_URL:
        raise RuntimeError("NEON_DATABASE_URL is not set.")
    return psycopg.connect(config.NEON_DATABASE_URL)
