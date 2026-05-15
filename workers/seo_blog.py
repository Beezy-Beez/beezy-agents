"""
SEO blog worker — generates and publishes 2 000-word articles to Shopify.

Entry points:
  run(slot)        — single article from a caller-supplied slot dict
  run_pending()    — batch: pulls all pending rows from seo_topics table,
                     generates + publishes each, marks published/error in DB

Required env vars: ANTHROPIC_API_KEY (or BEEZY_ANTHROPIC_API_KEY),
                   SHOPIFY_SHOP_DOMAIN, SHOPIFY_ACCESS_TOKEN
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import anthropic
import httpx

from lib.dryrun import is_dry_run
from lib.json_extract import loads_lenient

MODEL = "claude-sonnet-4-6"

SYSTEM = (
    "You are an expert SEO copywriter for Beezy Beez Honey (trybeezybeez.com), "
    "a DTC botanical-extract honey brand targeting women 50+ seeking better sleep. "
    "Products contain CBN and CBD in raw honey — pure, food-grade, no pills. "
    "Brand voice: warm, science-backed, empowering (not medicinal or clinical). "
    "Article rules: open with a specific real person, stat, or scenario (no generics). "
    "Keyword in H1, first paragraph, and 2-3 subheadings. "
    "Exactly 1 900-2 100 words. Prose only — H2/H3 headings, NO bullet lists. "
    "Weave in the CBN/CBD honey sleep-wellness angle naturally throughout. "
    "One product reference in the final CTA paragraph only. "
    "Output ONLY valid JSON (no markdown fences). Schema: "
    '{"title":"SEO H1 title","slug":"url-slug","meta_description":"max 155 chars",'
    '"html_body":"<h2>...</h2><p>...</p>...","word_count":2000}'
)


def _api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("BEEZY_ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    return key


def _generate(topic: str, audience: str) -> dict:
    client = anthropic.Anthropic(api_key=_api_key())
    msg = client.messages.create(
        model=MODEL,
        max_tokens=6000,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Topic: {topic}\n"
                f"Audience: {audience}\n"
                "Brand: Beezy Beez Honey — CBN/CBD honey for sleep"
            ),
        }],
    )
    # loads_lenient recovers from the raw newlines/quotes a 2,000-word
    # html_body routinely injects, and raises a clear ValueError (never a
    # cryptic JSONDecodeError) so the orchestrator marks a clean failure.
    return loads_lenient(msg.content[0].text)


def _shopify_headers() -> dict:
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("SHOPIFY_ACCESS_TOKEN is not set.")
    return {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}


def _publish(post: dict) -> tuple[str, str]:
    """Publish article to Shopify. Returns (url, article_gid)."""
    if is_dry_run():
        slug = post.get("slug", "dry-run-article")
        print(f"[seo_blog/DRY RUN] would publish article: {post.get('title', '?')}")
        return f"https://trybeezybeez.com/blogs/news/{slug}", "dry-article"
    shop = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    if not shop:
        raise RuntimeError("SHOPIFY_SHOP_DOMAIN is not set.")
    url = f"https://{shop}/admin/api/2025-10/graphql.json"
    hdrs = _shopify_headers()

    # Find first blog
    r = httpx.post(url, headers=hdrs, timeout=30,
                   json={"query": "{ blogs(first:5){edges{node{id title}}} }"})
    r.raise_for_status()
    blogs = r.json()["data"]["blogs"]["edges"]
    if not blogs:
        raise RuntimeError("No Shopify blog found.")
    blog_id = blogs[0]["node"]["id"]

    # Create article
    r2 = httpx.post(url, headers=hdrs, timeout=30, json={
        "query": (
            "mutation articleCreate($article: ArticleCreateInput!) {"
            "  articleCreate(article: $article) {"
            "    article { id handle onlineStoreUrl }"
            "    userErrors { field message }"
            "  }}"
        ),
        "variables": {"article": {
            "blogId":      blog_id,
            "title":       post["title"],
            "handle":      post["slug"],
            "body":        post["html_body"],
            "summary":     post.get("meta_description", ""),
            "isPublished": True,
        }},
    })
    r2.raise_for_status()
    data   = r2.json()["data"]["articleCreate"]
    errors = data.get("userErrors", [])
    if errors:
        raise RuntimeError(f"Shopify articleCreate errors: {errors}")
    article = data.get("article", {})
    page_url = article.get("onlineStoreUrl") or f"https://{shop}/blogs/news/{post['slug']}"
    article_id = article.get("id", "")
    return page_url, article_id


def run(slot: dict) -> dict:
    """Generate and publish one SEO article. `slot` must contain `topic_angle`."""
    from lib.slack import post_draft

    topic    = slot.get("topic_angle", "sleep and wellness")
    audience = slot.get("audience", "women 50+")
    print(f"[seo_blog] Generating: {topic}")
    post = _generate(topic, audience)
    wc = str(post.get("word_count", "?"))
    print(f"[seo_blog]   {wc}w: {post['title']}")
    page_url, _ = _publish(post)
    print(f"[seo_blog]   Published: {page_url}")
    post_draft(
        title=f"SEO Blog Published: {post['title']}",
        summary_lines=[
            f"Title:    {post['title']}",
            f"Words:    {wc}",
            f"Date:     {slot.get('date', '?')}",
        ],
        body=f"Live: {page_url}\n\nMeta: {post.get('meta_description', '')}",
    )
    return {"url": page_url, "title": post["title"]}


def run_pending() -> str:
    """
    Pull all pending rows from seo_topics, generate + publish each article,
    and update DB status to 'published' or 'error'.

    Returns a short summary string.
    """
    from db.connection import get_conn
    from lib.slack import post_draft

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, keyword FROM seo_topics WHERE status = 'pending' ORDER BY created_at ASC LIMIT 5"
        ).fetchall()

    if not rows:
        print("[seo_blog] No pending topics.")
        return "no_pending"

    published, failed = 0, 0
    for (topic_id, keyword) in rows:
        print(f"[seo_blog] Processing: {keyword}")
        try:
            post = _generate(keyword, "women 50+")
            page_url, article_id = _publish(post)
            now = datetime.now(timezone.utc)
            with get_conn() as conn:
                conn.execute(
                    """UPDATE seo_topics
                       SET status = 'published',
                           published_url = %s,
                           shopify_article_id = %s,
                           published_at = %s
                       WHERE id = %s""",
                    (page_url, article_id, now, topic_id),
                )
                conn.commit()
            print(f"[seo_blog]   Published: {page_url}")
            post_draft(
                title=f"SEO Blog Published: {post['title']}",
                summary_lines=[
                    f"Keyword:  {keyword}",
                    f"Title:    {post['title']}",
                    f"Words:    {post.get('word_count', '?')}",
                ],
                body=f"Live: {page_url}\n\nMeta: {post.get('meta_description', '')}",
            )
            published += 1
        except Exception as exc:
            err = str(exc)[:400]
            print(f"[seo_blog]   ERROR: {err}")
            with get_conn() as conn:
                conn.execute(
                    "UPDATE seo_topics SET status = 'error', error_detail = %s WHERE id = %s",
                    (err, topic_id),
                )
                conn.commit()
            failed += 1

    summary = f"seo_blog: {published} published, {failed} failed out of {len(rows)} pending"
    print(f"[seo_blog] {summary}")
    return summary
