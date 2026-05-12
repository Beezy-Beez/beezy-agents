"""Central config: load .env and expose typed accessors."""

import os
from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default)


DATABASE_URL = _optional("DATABASE_URL")
ANTHROPIC_API_KEY = _optional("ANTHROPIC_API_KEY")
SLACK_WEBHOOK_URL = _optional("SLACK_WEBHOOK_URL")
SLACK_SIGNING_SECRET = _optional("SLACK_SIGNING_SECRET")

KLAVIYO_API_KEY = _optional("KLAVIYO_API_KEY")
SHOPIFY_SHOP_DOMAIN = _optional("SHOPIFY_SHOP_DOMAIN")
SHOPIFY_ACCESS_TOKEN = _optional("SHOPIFY_ACCESS_TOKEN")
GSC_CREDENTIALS_JSON = _optional("GSC_CREDENTIALS_JSON")

OVERSEER_MODEL = _optional("OVERSEER_MODEL", "claude-opus-4-7")
AGENT_MODEL = _optional("AGENT_MODEL", "claude-sonnet-4-6")
