"""
api/deploy.py
SWARM → beezy-agents deployment bridge.

Add to main FastAPI app:
    from api.deploy import router as deploy_router
    app.include_router(deploy_router)

Env vars required:
    DEPLOY_API_KEY   — shared secret between Paperclip Deployer and this endpoint
"""

import os
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/deploy", tags=["deploy"])

DEPLOY_API_KEY = os.environ.get("DEPLOY_API_KEY", "")


# ─────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────

class SubjectLine(BaseModel):
    variant: str
    text: str
    char_count: int

class DeployRequest(BaseModel):
    # Slot metadata
    slot_date: str
    send_time: str
    content_type: str           # hive_mind | product_feature | promotional | reactivation | hive_club | sleep_audio
    audience_id: str
    audience_label: str
    format: str                 # image | plain_text
    sender_persona: str

    # Copy
    subject_lines: list[SubjectLine]
    preview_text: str
    from_name: str
    body_html: str
    body_plain: str
    cta_url: str
    cta_button_text: Optional[str] = "Shop Now"
    image_concept: Optional[str] = None

    # Optional
    issue_number: Optional[int] = None       # Hive Mind only
    episode_title: Optional[str] = None      # Sleep Audio only
    shopify_page_slug: Optional[str] = None  # pre-computed slug if available

class DeployResponse(BaseModel):
    status: str
    klaviyo_campaign_id: Optional[str] = None
    shopify_page_url: Optional[str] = None
    image_url: Optional[str] = None
    subject_used: Optional[str] = None
    deployed_at: str
    notes: str = ""


# ─────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────

def _check_auth(authorization: Optional[str]):
    if not DEPLOY_API_KEY:
        raise HTTPException(status_code=500, detail="DEPLOY_API_KEY not configured on server")
    if not authorization or authorization != f"Bearer {DEPLOY_API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")


# ─────────────────────────────────────────────
# Route handlers per content type
# ─────────────────────────────────────────────

async def _deploy_hive_mind(req: DeployRequest) -> DeployResponse:
    """Hive Mind: publish Shopify page + create Klaviyo campaign draft."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from workers.klaviyo_campaign import create_hive_mind_campaign
    from workers.shopify_publisher import publish_issue_page

    slug = req.shopify_page_slug or f"hive-mind-issue-{req.issue_number or 'draft'}"
    subject = req.subject_lines[0].text if req.subject_lines else ""

    # 1. Publish Shopify page
    page_url = None
    try:
        page_url = publish_issue_page(
            slug=slug,
            title=f"The Hive Mind — Issue {req.issue_number}",
            body_html=req.body_html,
            issue_number=req.issue_number,
        )
        logger.info(f"[Deploy] Shopify page published: {page_url}")
    except Exception as e:
        logger.error(f"[Deploy] Shopify page failed: {e}")
        # Don't abort — continue with Klaviyo

    # 2. Create Klaviyo campaign draft
    campaign_id = None
    try:
        campaign_id = create_hive_mind_campaign(
            subject=subject,
            preview_text=req.preview_text,
            from_name=req.from_name,
            body_html=req.body_html,
            audience_id=req.audience_id,
            send_date=req.slot_date,
            send_time=req.send_time,
            issue_number=req.issue_number,
        )
        logger.info(f"[Deploy] Klaviyo campaign created: {campaign_id}")
    except Exception as e:
        logger.error(f"[Deploy] Klaviyo campaign failed: {e}")

    return DeployResponse(
        status="success" if campaign_id else "partial",
        klaviyo_campaign_id=campaign_id,
        shopify_page_url=page_url,
        subject_used=subject,
        deployed_at=datetime.now(timezone.utc).isoformat(),
        notes="" if campaign_id else "Klaviyo campaign creation failed — check logs",
    )


async def _deploy_product_campaign(req: DeployRequest) -> DeployResponse:
    """Product / Promo / Reactivation / Hive Club: image + Klaviyo campaign draft."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from workers.beezy_campaign import create_campaign
    from workers.image_gen import generate_image

    subject = req.subject_lines[0].text if req.subject_lines else ""

    # 1. Generate image if needed
    image_url = None
    if req.format == "image" and req.image_concept:
        try:
            image_url = generate_image(prompt=req.image_concept)
            logger.info(f"[Deploy] Image generated: {image_url}")
        except Exception as e:
            logger.error(f"[Deploy] Image generation failed: {e}")

    # 2. Create Klaviyo campaign draft
    campaign_id = None
    try:
        campaign_id = create_campaign(
            subject=subject,
            preview_text=req.preview_text,
            from_name=req.from_name,
            body_html=req.body_html,
            audience_id=req.audience_id,
            send_date=req.slot_date,
            send_time=req.send_time,
            content_type=req.content_type,
            image_url=image_url,
            cta_url=req.cta_url,
            cta_button_text=req.cta_button_text,
        )
        logger.info(f"[Deploy] Klaviyo campaign created: {campaign_id}")
    except Exception as e:
        logger.error(f"[Deploy] Klaviyo campaign failed: {e}")

    return DeployResponse(
        status="success" if campaign_id else "partial",
        klaviyo_campaign_id=campaign_id,
        image_url=image_url,
        subject_used=subject,
        deployed_at=datetime.now(timezone.utc).isoformat(),
        notes="" if campaign_id else "Klaviyo campaign creation failed — check logs",
    )


async def _deploy_sleep_audio(req: DeployRequest) -> DeployResponse:
    """Sleep Audio: publish episode page + Klaviyo campaign draft."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from workers.beezy_campaign import create_campaign
    from workers.shopify_publisher import publish_episode_page

    subject = req.subject_lines[0].text if req.subject_lines else ""
    slug = req.shopify_page_slug or f"sleep-{req.episode_title.lower().replace(' ', '-')}" if req.episode_title else "sleep-episode"

    # 1. Publish Shopify episode page
    page_url = None
    try:
        page_url = publish_episode_page(
            slug=slug,
            title=req.episode_title or "New Sleep Story",
            body_html=req.body_html,
        )
        logger.info(f"[Deploy] Episode page published: {page_url}")
    except Exception as e:
        logger.error(f"[Deploy] Episode page failed: {e}")

    # 2. Create Klaviyo campaign draft
    campaign_id = None
    try:
        campaign_id = create_campaign(
            subject=subject,
            preview_text=req.preview_text,
            from_name=req.from_name,
            body_html=req.body_html,
            audience_id=req.audience_id,
            send_date=req.slot_date,
            send_time=req.send_time,
            content_type="sleep_audio",
            cta_url=page_url or req.cta_url,
            cta_button_text=req.cta_button_text,
        )
        logger.info(f"[Deploy] Klaviyo campaign created: {campaign_id}")
    except Exception as e:
        logger.error(f"[Deploy] Klaviyo campaign failed: {e}")

    return DeployResponse(
        status="success" if campaign_id else "partial",
        klaviyo_campaign_id=campaign_id,
        shopify_page_url=page_url,
        subject_used=subject,
        deployed_at=datetime.now(timezone.utc).isoformat(),
        notes="" if campaign_id else "Klaviyo campaign creation failed — check logs",
    )


# ─────────────────────────────────────────────
# Main endpoint
# ─────────────────────────────────────────────

@router.post("/", response_model=DeployResponse)
async def deploy(
    req: DeployRequest,
    authorization: Optional[str] = Header(None),
):
    """
    SWARM → beezy-agents deployment bridge.
    Routes to the correct worker based on content_type.
    All deployments create Klaviyo DRAFT campaigns (never scheduled).
    """
    _check_auth(authorization)

    logger.info(f"[Deploy] Received: {req.content_type} × {req.audience_label} × {req.slot_date}")

    PRODUCT_TYPES = {"product_feature", "promotional", "hive_club", "reactivation"}

    try:
        if req.content_type == "hive_mind":
            return await _deploy_hive_mind(req)
        elif req.content_type in PRODUCT_TYPES:
            return await _deploy_product_campaign(req)
        elif req.content_type == "sleep_audio":
            return await _deploy_sleep_audio(req)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown content_type: {req.content_type}. "
                       f"Valid: hive_mind, product_feature, promotional, hive_club, reactivation, sleep_audio"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Deploy] Unhandled error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health():
    """Sanity check — confirms endpoint is reachable."""
    return {"status": "ok", "endpoint": "beezy-agents deploy bridge"}
