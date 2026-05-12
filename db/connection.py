"""psycopg connection / pool helpers."""

from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool

import config

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set. Add it to .env.")
        _pool = ConnectionPool(conninfo=config.DATABASE_URL, min_size=1, max_size=8, open=True)
    return _pool


@contextmanager
def get_conn():
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def raw_connect() -> psycopg.Connection:
    """Single-shot connection — used by the migrations runner."""
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env.")
    return psycopg.connect(config.DATABASE_URL)
