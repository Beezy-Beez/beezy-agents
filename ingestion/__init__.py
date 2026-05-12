"""Performance ingestion package — Layer 6.

Pulls Klaviyo + Shopify data into the `performance` table every 4h. The pacing
brain reads from `performance`; without this layer, the brain is flying blind.

- `klaviyo.py` — campaigns, flows, opens, clicks, conversions, revenue
- `shopify.py` — orders, attributed revenue
- `sync.py`   — orchestrator; dedupes, writes `performance` + `ingestion_runs` rows
"""

from . import klaviyo, shopify, sync  # noqa: F401
