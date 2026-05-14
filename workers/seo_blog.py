import json, os
import anthropic, httpx

MODEL = "claude-sonnet-4-6"

SYSTEM = (
    "You are an expert SEO copywriter for Beezy Beez Honey (trybeezybeez.com), "
    "a DTC botanical extract honey brand targeting women 50+ seeking better sleep. "
    "Rules: open with a specific person/stat/scenario. Keyword in H1, first para, "
    "2-3 subheadings. 900-1100 words. Prose with H2/H3 only, no bullet lists. "
    "One product reference in final CTA only. "
    'Output ONLY valid JSON, no markdown. Schema: '
    '{"title":"SEO H1 title","slug":"url-slug","meta_description":"max 155 chars",'
    '"html_body":"<h2>...</h2><p>...</p>","word_count":950}'
)

def _generate(topic, audience):
    key = os.environ.get("BEEZY_ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("BEEZY_ANTHROPIC_API_KEY not set.")
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=MODEL, max_tokens=4096, system=SYSTEM,
        messages=[{"role": "user", "content": "Topic: " + topic + "\nAudience: " + audience + "\nBrand: Beezy Beez Honey"}],
    )
    raw = msg.content[0].text.strip()
    s, e = raw.find("{"), raw.rfind("}")
    return json.loads(raw[s:e+1] if s != -1 else raw)

def _publish(post):
    shop  = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    url   = "https://" + shop + "/admin/api/2025-10/graphql.json"
    hdrs  = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    r = httpx.post(url, headers=hdrs, timeout=30,
                   json={"query": "{ blogs(first:5){edges{node{id title}}} }"})
    blogs = r.json()["data"]["blogs"]["edges"]
    if not blogs:
        raise RuntimeError("No Shopify blog found.")
    blog_id = blogs[0]["node"]["id"]
    r2 = httpx.post(url, headers=hdrs, timeout=30, json={
        "query": (
            "mutation articleCreate($article: ArticleCreateInput!) {"
            "  articleCreate(article: $article) {"
            "    article { id handle onlineStoreUrl }"
            "    userErrors { field message }"
            "  }}"
        ),
        "variables": {"article": {
            "blogId": blog_id, "title": post["title"],
            "handle": post["slug"], "body": post["html_body"],
            "summary": post.get("meta_description", ""), "isPublished": True,
        }},
    })
    data   = r2.json()["data"]["articleCreate"]
    errors = data.get("userErrors", [])
    if errors:
        raise RuntimeError("Shopify articleCreate errors: " + str(errors))
    article = data.get("article", {})
    return article.get("onlineStoreUrl") or ("https://" + shop + "/blogs/news/" + post["slug"])

def run(slot):
    from lib.slack import post_draft
    topic    = slot.get("topic_angle", "sleep and wellness")
    audience = slot.get("audience", "women 50+")
    print("[seo_blog] Generating: " + topic)
    post = _generate(topic, audience)
    wc = str(post.get("word_count", "?"))
    print("[seo_blog]   " + wc + "w: " + post["title"])
    page_url = _publish(post)
    print("[seo_blog]   Published: " + page_url)
    post_draft(
        title="SEO Blog Published: " + post["title"],
        summary_lines=[
            "Title:    " + post["title"],
            "Words:    " + wc,
            "Date:     " + slot.get("date", "?"),
        ],
        body="Live: " + page_url + "\n\nMeta: " + post.get("meta_description", ""),
    )
    return {"url": page_url, "title": post["title"]}
