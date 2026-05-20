"""Central config — reads from environment variables (Replit Secrets)."""
import os

KLAVIYO_REVISION      = "2025-10-15"
DATABASE_URL          = os.environ.get("POSTGRES_URL", "")
KLAVIYO_API_KEY       = os.environ.get("KLAVIYO_API_KEY", "")
KLAVIYO_FROM_EMAIL    = os.environ.get("KLAVIYO_FROM_EMAIL", "help@trybeezybeez.com")
ANTHROPIC_API_KEY     = os.environ.get("BEEZY_ANTHROPIC_API_KEY", "")
SHOPIFY_SHOP_DOMAIN   = os.environ.get("SHOPIFY_SHOP_DOMAIN", "trybeezybeez.myshopify.com")
SHOPIFY_ACCESS_TOKEN  = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
HIGGSFIELD_KEY        = os.environ.get("HIGGSFIELD_KEY", "")
HIGGSFIELD_SECRET     = os.environ.get("HIGGSFIELD_SECRET", "")
SLACK_BOT_TOKEN       = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_WEBHOOK_URL     = os.environ.get("SLACK_WEBHOOK_URL", "")
REPLIT_DOMAIN         = os.environ.get("REPLIT_DOMAIN", "beezy-agents-ingestion.replit.app")
