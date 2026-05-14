"""Database access layer.

DB helpers used by `pacing/` (brain, calendar, cron) and `ingestion/` (klaviyo,
shopify, sync) will live in this package. Only the connection helper is
implemented at scaffold time.
"""

from .connection import get_conn  # noqa: F401
