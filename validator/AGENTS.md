# Beezy Send Validator — Agent Reference

`workers/validator.py` — runs before every Klaviyo campaign send.
Call: `validate_campaign(conn, slot, copy, cta_url)`.
Return `"pass": false` → campaign is **blocked**. No exceptions.

---

## Rules table (19 rules)

| Rule | Name | Auto-fail? | Logic |
|------|------|-----------|-------|
| R1 | Smart Sending ≥24h | No | No send to same audience today |
| **R2** | **7-day cooldown ≥168h** | **Yes** | No send to same audience within 7 days. NON-NEGOTIABLE. |
| R3 | Theme 5d gap ≥120h | No | Same `content_type` to same audience: ≥5d gap |
| R4 | Active Seal weekly <4 | No | `active_seal`/`active_subscribers`: ≤3 sends in 7 days |
| R5 | Burned audience | No | Reads `agent_state['burned_audiences']`; blocks if key present |
| R6 | Revenue floor ≥$300 | No | `slot.revenue_estimate ≥ 300` |
| R7 | Format kill list | No | Blocked combos: `active_seal+editorial`, `vip+pre_paid_bundle` |
| R8 | Daily cadence ≤3 | No | Max 3 sends today across all audiences |
| R9 | Segment overlap same day | No | Cross-references overlap groups |
| **R10** | **Active flow overlap** | **Yes** | Live Klaviyo flows double-touching audience within 72h |
| R11 | Top-1% benchmark | No | 90d avg RPR ≥ $0.10 AND open rate ≥ 25% (needs ≥3 finalized sends) |
| R12 | Format data-backed | No | Current `content_type` RPR ≥ 70% of best format for this audience (90d) |
| **R13** | **Product accuracy** | **Yes** | Body copy must not reference products absent from the active Shopify catalog. No hallucinated flavor variants. See `_HALLUCINATED_PRODUCTS` list. |
| **R14** | **CTA URL compliance** | **Yes** | When copy promotes a specific product, CTA must point to that product's canonical URL. No generic `/pages/bf-collection` when a product-specific URL is required. |
| **C1** | **Subject personalization syntax** | **Yes** | Must use `{{ first_name }}`, not `{{ person.first_name\|default:'...' }}` |
| **C2** | **CTA URL (customer → direct)** | **Yes** | Customer segments must link to `/pages/bf-collection` or `/discount/CODE` |
| **C3** | **Offer/audience alignment** | **Yes** | HIGH_VALUE_SEGMENTS must not receive discount/BOGO/credit language |
| C4 | Image includes humans | No | Image prompt must include woman/women 50+ |
| **C5** | **Collection URL** | **Yes** | Must be `/pages/bf-collection`, never `/collections/all` |

**Auto-fail rules:** `R2, R10, R13, R14, C1, C2, C3, C5`

Any failure (including warnings) blocks the campaign until the validator matures.

---

## R13 — Product Accuracy (AUTO-FAIL)

**Trigger:** Copy body or subject references a product name NOT in the active Shopify catalog.

**Known hallucinated products (explicit blocklist):**
- Ashwagandha Honey ← documented incident, May 2026 VIP campaign
- Chamomile & Passionflower Honey ← documented incident, May 2026 lapsed_30d
- Lavender Honey, Wildflower Honey, Manuka Honey
- Elderberry Honey, Turmeric Honey, Ginger Honey, Peppermint Honey

**Active catalog (as of May 2026 — verify live before each generation):**

| Category | Products |
|----------|---------|
| Sleep Honey | Cinnamon, Caramel, Graham Cracker, Gingerbread, Chocolate Strawberry, Blood Orange, Apple Pie, Vanilla, Original, Strawberry Cheesecake |
| Premium Honey | Delicious Calm 1500MG, Ultra Strength 3000MG |
| Vegan Gummies | Strawberry, Black Cherry, Mixed Fruit, CBN |
| Bundles | CBN Sleep Bundle, Sleep Essentials Bundle, Gummies Trio Sleep Bundle |
| Topical | Anti-Inflammation & Itching Balm, Lotion |
| Other | Tea, Doggy Treats, Candle Set, Gift Box, Lip Balm 3pk, Oil |
| Subscriptions | Hive Club (monthly/annual), 3-Month Pre-Paid, 12-Month Pre-Paid, Pre-Load Card |

**Implementation:** `_r13_product_accuracy(copy)` in `workers/validator.py`.
Hardcoded blocklist + active catalog. Copywriter must pull live Shopify catalog before writing.

---

## R14 — CTA URL Compliance (AUTO-FAIL)

**Trigger:** Copy promotes a specific product/offer with explicit purchase/join language, but the CTA URL does not match that product's canonical URL.

**Does NOT trigger for:** General "browse our honey" sends where `/pages/bf-collection` is correct.

**Canonical CTA URL table:**

| Offer | Required CTA URL |
|-------|-----------------|
| Hive Club | `https://trybeezybeez.com/pages/membership` |
| 12-Month Pre-Paid | `https://trybeezybeez.com/products/botanical-extract-honey-pps?variant=46208630980857` |
| Pre-Load Card | `https://trybeezybeez.com/products/beezy-beez-pre-load-card?variant=46940893348089` |
| Sleep Honey | `https://trybeezybeez.com/products/honey-sub` |
| Gummies | `https://trybeezybeez.com/products/gummies-bx` |
| CBN Sleep Bundle | `https://trybeezybeez.com/products/cbn-sleep-bundle` |
| Sleep Essentials Bundle | `https://trybeezybeez.com/products/sleep-essentials-bundle` |
| Gummies Trio Bundle | `https://trybeezybeez.com/products/gummies-trio-bundle` |
| Topical Balm & Lotion | `https://trybeezybeez.com/products/topical-island` |

**Detection signals:** Rule triggers when copy contains explicit purchase intent phrases like "join Hive Club", "12-month pre-paid subscription", "cbn sleep bundle", etc. See `_PRODUCT_SIGNALS` in `workers/validator.py`.

**Discount URL exception:** `/discount/CODE?redirect=/pages/bf-collection` is always valid because the discount system handles routing automatically.

---

## Segment classifications

```python
CUSTOMER_SEGMENTS = {
    "lapsed_30d", "lapsed_60d", "lapsed_60_90d", "lapsed_90d", "lapsed_90_180d",
    "lapsed_180d", "lapsed_180d_plus", "winback_180d",
    "vip", "inner_circle", "engaged_customers", "all_customers",
    "active_seal", "active_subscribers", "whales", "high_aov",
    "one_time_buyers", "otb", "cart_abandoners",
}
HIGH_VALUE_SEGMENTS = {"vip", "inner_circle", "whales", "high_aov", "active_seal", "active_subscribers"}
PROSPECT_SEGMENTS   = {"engaged_prospects", "super_engaged"}
```

---

## Adding new rules

1. Implement `_rNN_name(conn, slot, copy, cta_url)` — return `{"rule": "RNN", "name": "...", "pass": bool, "detail": "..."}`.
2. Add to `validate_campaign()` call chain.
3. If auto-fail: add `"RNN"` to `auto_fail_rules` set.
4. Update the rules table in this file and in `CLAUDE.md`.
5. Add a test in `tests/test_validator.py`.
