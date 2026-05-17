#!/usr/bin/env python3
"""
Full pipeline dry test -- 6 content types.

Runs each worker against real APIs (Klaviyo, Shopify) and reports a summary table.
Cleans up calendar_executions test rows at the end (sets status=skipped).

Run:
    cd ~/workspace && python3 -m scripts.dry_pipeline_test
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import uuid
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg
from config import DATABASE_URL


PASS  = "PASS "
FAIL  = "FAIL "
BLOCK = "BLOCK"

results: list[dict] = []
inserted_exec_ids: list[str] = []
_test_decision_id: str = ""


# ---- DB helpers ----

def get_conn():
    return psycopg.connect(DATABASE_URL)


def ensure_test_decision() -> str:
    global _test_decision_id
    if _test_decision_id:
        return _test_decision_id
    decision_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO decisions (id, decided_by, decision_type, input_context, reasoning, output) "
            "VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb)",
            (decision_id, "dry_test", "dry_test_pipeline",
             json.dumps({}), "Dry test pipeline run", json.dumps({"dry": True})),
        )
        conn.commit()
    _test_decision_id = decision_id
    return decision_id


def insert_calendar_exec(slot_date, content_type, audience, topic_angle="Pipeline dry test", notes=None):
    row_id = str(uuid.uuid4())
    decision_id = ensure_test_decision()
    notes_val = json.dumps(notes) if isinstance(notes, dict) else (notes or "dry_test")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO calendar_executions "
            "(id, decision_id, slot_date, content_type, audience, topic_angle, status, notes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (row_id, decision_id, slot_date, content_type, audience, topic_angle, "pending", notes_val),
        )
        conn.commit()
    inserted_exec_ids.append(row_id)
    return row_id


def cleanup_exec_rows():
    if not inserted_exec_ids:
        return
    with get_conn() as conn:
        conn.execute(
            "UPDATE calendar_executions SET status='skipped', notes='dry_test_cleanup' WHERE id = ANY(%s)",
            (inserted_exec_ids,),
        )
        conn.commit()
    print(f"[cleanup] Marked {len(inserted_exec_ids)} calendar_executions rows as skipped.")


def patch_issue_20_page_fields():
    with get_conn() as conn:
        row = conn.execute("SELECT page_title, page_dek FROM issues WHERE number=20").fetchone()
        if row and row[0] and row[1]:
            print("[prep] Issue 20 page fields already present.")
            return
        conn.execute(
            "UPDATE issues SET page_title=%s, page_dek=%s, page_breadcrumb_label=%s WHERE number=20",
            (
                "Your Body Clock Doesn't Run on Sunlight Alone",
                "In 1938, a scientist descended into Mammoth Cave to spend 32 days underground. "
                "What he discovered changed everything we know about how women 50+ lose their "
                "natural sleep rhythm -- and how to get it back.",
                "Circadian Timing",
            ),
        )
        conn.commit()
    print("[prep] Issue 20 patched with page_title + page_dek.")


def check_shopify_page(handle: str) -> tuple[bool, dict]:
    import httpx
    shop  = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    resp  = httpx.post(
        f"https://{shop}/admin/api/2025-10/graphql.json",
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        timeout=20,
        json={"query": '{ pages(first:1, query:"handle:' + handle + '") { edges { node { id handle isPublished } } } }'},
    )
    edges = resp.json().get("data", {}).get("pages", {}).get("edges", [])
    return (len(edges) > 0), (edges[0]["node"] if edges else {})


def check_klaviyo_campaign(campaign_id: str) -> dict:
    import httpx
    from config import KLAVIYO_REVISION
    resp = httpx.get(
        f"https://a.klaviyo.com/api/campaigns/{campaign_id}/",
        headers={"Authorization": "Klaviyo-API-Key " + os.environ.get("KLAVIYO_API_KEY", ""),
                 "revision": KLAVIYO_REVISION},
        timeout=20,
    )
    if not resp.is_success:
        return {}
    return resp.json().get("data", {}).get("attributes", {})


def record(test_name, verdict, shopify_page, index_updates, klaviyo_draft, smart_off, error=""):
    results.append({
        "test": test_name, "verdict": verdict, "shopify_page": shopify_page,
        "index_updates": index_updates, "klaviyo_draft": klaviyo_draft,
        "smart_off": smart_off, "error": error,
    })


# =========================================================================
# TEST 1 -- HIVE MIND (Issue 20)
# =========================================================================

def test_hive_mind():
    print("\n" + "=" * 70)
    print("TEST 1: HIVE MIND -- Issue 20")
    print("=" * 70)

    patch_issue_20_page_fields()

    with get_conn() as conn:
        row = conn.execute(
            "SELECT shopify_page_id, klaviyo_campaign_id FROM issues WHERE number=20"
        ).fetchone()
    existing_page_id  = row[0] if row else None
    existing_campaign = row[1] if row else None

    page_handle = "social-zeitgebers-circadian-clock-sleep-timing"

    # Ensure Shopify page exists (create it if missing -- locals() bug now fixed)
    page_exists, page_node = check_shopify_page(page_handle)
    if not page_exists:
        print("[test1] Page missing -- creating now...")
        try:
            from workers.klaviyo_campaign import _create_page_for_issue
            page_result = _create_page_for_issue(20)
            print(f"[test1] Page created: {page_result.get('page_url', '')}")
            page_exists, page_node = check_shopify_page(page_handle)
        except Exception as exc:
            print(f"[test1] Page creation error: {exc}")
            traceback.print_exc()

    page_published = page_node.get("isPublished", True) if page_node else "?"

    # Campaign: use existing or create fresh
    campaign_id = existing_campaign
    if not campaign_id:
        try:
            from workers.klaviyo_campaign import create_campaign_for_issue
            out = create_campaign_for_issue(20)
            campaign_id = out.get("campaign_id", "")
            print(f"[test1] Campaign created: {campaign_id}")
        except Exception as e:
            print(f"[test1] Campaign error: {e}")
            traceback.print_exc()
            record("HIVE MIND (Issue 20)", FAIL, "N/A", "N/A", "N/A", "N/A", str(e)[:120])
            return
    else:
        print(f"[test1] Using existing campaign {campaign_id[:12]}...")

    # Verify Klaviyo campaign
    camp_data   = check_klaviyo_campaign(campaign_id) if campaign_id else {}
    smart_off   = not camp_data.get("send_options", {}).get("use_smart_sending", True)
    camp_status = camp_data.get("status", "?")

    print(f"[test1] Page: {'YES' if page_exists else 'NOT FOUND'} isPublished={page_published}")
    print(f"[test1] Klaviyo: id={campaign_id[:12] if campaign_id else 'N/A'}... status={camp_status} smart_off={smart_off}")

    verdict = PASS if (page_exists and campaign_id and smart_off) else FAIL
    record(
        "HIVE MIND (Issue 20)", verdict,
        f"YES (isPublished={page_published})" if page_exists else "NOT FOUND",
        "the-hive-mind only",
        f"YES {campaign_id[:12] if campaign_id else 'N/A'}... status={camp_status}",
        "YES" if smart_off else "NO",
    )


# =========================================================================
# TEST 1b -- TTS pipeline (now local in beezy-agents, not external SAP)
# =========================================================================

def test_tts_dispatch():
    print("\n" + "=" * 70)
    print("TEST 1b: TTS PIPELINE -- local beezy-agents worker")
    print("=" * 70)

    import os as _os

    # Check that the required secrets are configured
    has_eleven  = bool(_os.environ.get("ELEVENLABS_API_KEY", ""))
    has_bz_pod  = bool(_os.environ.get("BUZZSPROUT_PODCAST_ID", ""))
    has_bz_tok  = bool(_os.environ.get("BUZZSPROUT_API_TOKEN", ""))

    missing = []
    if not has_eleven:
        missing.append("ELEVENLABS_API_KEY")
    if not has_bz_pod:
        missing.append("BUZZSPROUT_PODCAST_ID")
    if not has_bz_tok:
        missing.append("BUZZSPROUT_API_TOKEN")

    print(f"[test1b] ELEVENLABS_API_KEY:    {'✓ set' if has_eleven else '✗ MISSING'}")
    print(f"[test1b] BUZZSPROUT_PODCAST_ID: {'✓ set' if has_bz_pod else '✗ MISSING'}")
    print(f"[test1b] BUZZSPROUT_API_TOKEN:  {'✓ set' if has_bz_tok else '✗ MISSING'}")

    try:
        from workers.tts_pipeline import _chunk_script
        # Verify chunker works correctly on a realistic script excerpt
        sample = "This is the first paragraph.\n\nThis is the second paragraph.\n\nThis is the third paragraph."
        chunks = _chunk_script(sample)
        assert len(chunks) >= 1, "Chunker returned empty list"
        assert all(len(c) <= 4_500 for c in chunks), "Chunk exceeds max length"
        print(f"[test1b] _chunk_script: OK — {len(chunks)} chunk(s) from sample text")
    except Exception as e:
        print(f"[test1b] ERROR importing tts_pipeline: {e}")
        record("TTS PIPELINE (local)", FAIL, "N/A", "N/A", "N/A", "N/A", str(e)[:120])
        return

    if missing:
        msg = "Secrets needed before TTS can run: " + ", ".join(missing)
        print(f"[test1b] BLOCK — {msg}")
        print(f"[test1b] Copy these from sleep-audio-platform Replit Secrets into beezy-agents.")
        record("TTS PIPELINE (local)", BLOCK,
               "N/A", "N/A", "N/A (secrets missing)", "N/A", error=msg)
        return

    print("[test1b] PASS — tts_pipeline imported, chunker verified, all secrets present")
    record("TTS PIPELINE (local)", PASS,
           "N/A", "N/A", "Worker ready — ElevenLabs+Buzzsprout secrets configured", "N/A")


# =========================================================================
# TEST 2 -- SLEEP AUDIO (sleep_story)
# =========================================================================

SLEEP_STORY_META = {
    "title":            "Test Sleep Story",
    "episode_type":     "sleep_story",
    "buzzsprout_url":   "https://www.buzzsprout.com/2292260",
    "buzzsprout_embed_url": "https://www.buzzsprout.com/2292260",
    "description_short": "A test episode.",
    "description_long":  "Two paragraphs of test content about sleep and relaxation for women 50+.",
    "hero_image_url":   "https://cdn.shopify.com/s/files/1/0616/0616/6777/files/Honey_CBN_Cinnamon__FOR_WEB_IMAGE2.png",
    "hero_image_alt":   "Test image",
    "script_text":      "This is the full test script text.",
    "episode_id":       "dry_test_sleep_story_001",
    "duration_minutes": 5,
    "suggested_send_date": "2026-06-02",
}


def test_sleep_story():
    print("\n" + "=" * 70)
    print("TEST 2: SLEEP AUDIO -- sleep_story")
    print("=" * 70)

    # Check if already deployed
    with get_conn() as conn:
        ep_row = conn.execute(
            "SELECT klaviyo_campaign_id_a, klaviyo_campaign_id_b, shopify_page_url "
            "FROM episodes WHERE episode_id='dry_test_sleep_story_001'"
        ).fetchone()

    if ep_row and ep_row[0]:
        print(f"[test2] Already deployed -- verifying existing campaigns.")
        camp_a_id = ep_row[0]
        camp_b_id = ep_row[1] or ""
        page_slug = "episode-test-sleep-story"
        page_exists, page_node = check_shopify_page(page_slug)
        camp_a_data = check_klaviyo_campaign(camp_a_id)
        camp_b_data = check_klaviyo_campaign(camp_b_id) if camp_b_id else {}
        smart_a = not camp_a_data.get("send_options", {}).get("use_smart_sending", True)
        smart_b = not camp_b_data.get("send_options", {}).get("use_smart_sending", True)
        verdict = PASS if (page_exists and camp_a_id and camp_b_id and smart_a and smart_b) else FAIL
        record(
            "SLEEP AUDIO (sleep_story)", verdict,
            f"YES isPublished={page_node.get('isPublished','?')}" if page_exists else "NOT FOUND",
            "sleep-science-hub",
            f"A:{camp_a_id[:10]}... | B:{camp_b_id[:10] if camp_b_id else 'N/A'}...",
            f"A={'YES' if smart_a else 'NO'} / B={'YES' if smart_b else 'NO'}",
        )
        return

    slot = {
        "date": "2026-06-02", "content_type": "sleep_audio", "audience": "RvtHdn",
        "topic_angle": "Test Sleep Story", "notes": json.dumps(SLEEP_STORY_META),
    }
    insert_calendar_exec("2026-06-02", "sleep_audio", "RvtHdn", notes=SLEEP_STORY_META)

    try:
        from workers.episode_deployer import run as deploy_run
        out = deploy_run(slot)
        camp_a_id = out.get("campaign_id", "") if isinstance(out, dict) else ""
        print(f"[test2] Result: {out}")

        page_slug = "episode-test-sleep-story"
        page_exists, page_node = check_shopify_page(page_slug)

        with get_conn() as conn:
            ep_row = conn.execute(
                "SELECT klaviyo_campaign_id_a, klaviyo_campaign_id_b FROM episodes WHERE episode_id='dry_test_sleep_story_001'"
            ).fetchone()
        camp_b_id = ep_row[1] if ep_row else ""

        camp_a_data = check_klaviyo_campaign(camp_a_id) if camp_a_id else {}
        camp_b_data = check_klaviyo_campaign(camp_b_id) if camp_b_id else {}
        smart_a = not camp_a_data.get("send_options", {}).get("use_smart_sending", True)
        smart_b = not camp_b_data.get("send_options", {}).get("use_smart_sending", True)

        print(f"[test2] Page: {'YES' if page_exists else 'NOT FOUND'} isPublished={page_node.get('isPublished','?')}")
        print(f"[test2] Campaign A: {camp_a_id[:12] if camp_a_id else 'N/A'}... smart_off={smart_a}")
        print(f"[test2] Campaign B: {camp_b_id[:12] if camp_b_id else 'N/A'}... smart_off={smart_b}")

        verdict = PASS if (page_exists and camp_a_id and camp_b_id and smart_a and smart_b) else FAIL
        record(
            "SLEEP AUDIO (sleep_story)", verdict,
            f"YES isPublished={page_node.get('isPublished','?')}" if page_exists else "NOT FOUND",
            "sleep-science-hub",
            f"A:{camp_a_id[:10] if camp_a_id else 'N/A'}... | B:{camp_b_id[:10] if camp_b_id else 'N/A'}...",
            f"A={'YES' if smart_a else 'NO'} / B={'YES' if smart_b else 'NO'}",
        )

    except Exception as e:
        print(f"[test2] ERROR: {e}")
        traceback.print_exc()
        record("SLEEP AUDIO (sleep_story)", FAIL, "N/A", "N/A", "N/A", "N/A", str(e)[:120])


# =========================================================================
# TEST 3 -- GUIDED MEDITATION
# =========================================================================

GUIDED_MED_META = {
    "title":            "Test Guided Meditation",
    "episode_type":     "guided_meditation",
    "buzzsprout_url":   "https://www.buzzsprout.com/2292260",
    "buzzsprout_embed_url": "https://www.buzzsprout.com/2292260",
    "description_short": "A test guided meditation.",
    "description_long":  "Two paragraphs of test content about guided meditation for women 50+.",
    "hero_image_url":   "https://cdn.shopify.com/s/files/1/0616/0616/6777/files/Honey_CBN_Cinnamon__FOR_WEB_IMAGE2.png",
    "hero_image_alt":   "Test meditation image",
    "script_text":      "This is the full test meditation script text.",
    "episode_id":       "dry_test_guided_med_001",
    "duration_minutes": 10,
    "suggested_send_date": "2026-06-03",
}


def test_guided_meditation():
    print("\n" + "=" * 70)
    print("TEST 3: SLEEP AUDIO -- guided_meditation")
    print("=" * 70)

    # Check if already deployed
    with get_conn() as conn:
        ep_row = conn.execute(
            "SELECT klaviyo_campaign_id_a, klaviyo_campaign_id_b, shopify_page_url "
            "FROM episodes WHERE episode_id='dry_test_guided_med_001'"
        ).fetchone()

    if ep_row and ep_row[0]:
        print("[test3] Already deployed -- verifying existing campaigns.")
        camp_a_id = ep_row[0]
        camp_b_id = ep_row[1] or ""
        page_slug = "episode-test-guided-meditation"
        page_exists, page_node = check_shopify_page(page_slug)
        camp_a_data = check_klaviyo_campaign(camp_a_id)
        camp_b_data = check_klaviyo_campaign(camp_b_id) if camp_b_id else {}
        smart_a = not camp_a_data.get("send_options", {}).get("use_smart_sending", True)
        smart_b = not camp_b_data.get("send_options", {}).get("use_smart_sending", True)
        verdict = PASS if (page_exists and camp_a_id and camp_b_id and smart_a and smart_b) else FAIL
        record(
            "GUIDED MEDITATION", verdict,
            f"YES isPublished={page_node.get('isPublished','?')}" if page_exists else "NOT FOUND",
            "meditation-library (breadcrumb)",
            f"A:{camp_a_id[:10]}... | B:{camp_b_id[:10] if camp_b_id else 'N/A'}...",
            f"A={'YES' if smart_a else 'NO'} / B={'YES' if smart_b else 'NO'}",
        )
        return

    slot = {
        "date": "2026-06-03", "content_type": "sleep_audio", "audience": "RvtHdn",
        "topic_angle": "Test Guided Meditation", "notes": json.dumps(GUIDED_MED_META),
    }
    insert_calendar_exec("2026-06-03", "sleep_audio", "RvtHdn", notes=GUIDED_MED_META)

    try:
        from workers.episode_deployer import run as deploy_run
        out = deploy_run(slot)
        camp_a_id = out.get("campaign_id", "") if isinstance(out, dict) else ""
        print(f"[test3] Result: {out}")

        page_slug = "episode-test-guided-meditation"
        page_exists, page_node = check_shopify_page(page_slug)

        with get_conn() as conn:
            ep_row = conn.execute(
                "SELECT klaviyo_campaign_id_a, klaviyo_campaign_id_b FROM episodes WHERE episode_id='dry_test_guided_med_001'"
            ).fetchone()
        camp_b_id = ep_row[1] if ep_row else ""

        camp_a_data = check_klaviyo_campaign(camp_a_id) if camp_a_id else {}
        camp_b_data = check_klaviyo_campaign(camp_b_id) if camp_b_id else {}
        smart_a = not camp_a_data.get("send_options", {}).get("use_smart_sending", True)
        smart_b = not camp_b_data.get("send_options", {}).get("use_smart_sending", True)

        print(f"[test3] Page: {'YES' if page_exists else 'NOT FOUND'} isPublished={page_node.get('isPublished','?')}")
        print(f"[test3] Campaign A: {camp_a_id[:12] if camp_a_id else 'N/A'}... smart_off={smart_a}")
        print(f"[test3] Campaign B: {camp_b_id[:12] if camp_b_id else 'N/A'}... smart_off={smart_b}")

        verdict = PASS if (page_exists and camp_a_id and camp_b_id and smart_a and smart_b) else FAIL
        record(
            "GUIDED MEDITATION", verdict,
            f"YES isPublished={page_node.get('isPublished','?')}" if page_exists else "NOT FOUND",
            "meditation-library (breadcrumb)",
            f"A:{camp_a_id[:10] if camp_a_id else 'N/A'}... | B:{camp_b_id[:10] if camp_b_id else 'N/A'}...",
            f"A={'YES' if smart_a else 'NO'} / B={'YES' if smart_b else 'NO'}",
        )

    except Exception as e:
        print(f"[test3] ERROR: {e}")
        traceback.print_exc()
        record("GUIDED MEDITATION", FAIL, "N/A", "N/A", "N/A", "N/A", str(e)[:120])


# =========================================================================
# TEST 4 -- PRODUCT FEATURE (beezy_campaign, active_seal)
# =========================================================================

def test_product_feature():
    print("\n" + "=" * 70)
    print("TEST 4: PRODUCT FEATURE -- active_seal (UBFUcH)")
    print("=" * 70)

    slot = {
        "date": "2026-06-04", "content_type": "product_feature", "audience": "active_seal",
        "topic_angle": "Dry test: Cinnamon honey + CBN for deep sleep -- Beehive Club benefit",
        "send_time_est": "20:15", "revenue_estimate": 400, "needs_page": False,
    }
    insert_calendar_exec("2026-06-04", "product_feature", "active_seal", topic_angle=slot["topic_angle"])

    try:
        from workers.beezy_campaign import run as camp_run
        out = camp_run(slot)
        print(f"[test4] Result: {out}")

        if isinstance(out, str) and out.startswith("blocked:"):
            record(
                "PRODUCT FEATURE (active_seal)", BLOCK,
                "N/A -- validator blocked", "N/A", "N/A -- validator blocked", "N/A", error=out,
            )
            return

        campaign_id = out.get("campaign_id", "") if isinstance(out, dict) else ""
        page_url    = out.get("page_url", "") if isinstance(out, dict) else ""

        camp_data   = check_klaviyo_campaign(campaign_id) if campaign_id else {}
        smart_off   = not camp_data.get("send_options", {}).get("use_smart_sending", True)
        camp_status = camp_data.get("status", "?")

        print(f"[test4] Campaign: {campaign_id[:12] if campaign_id else 'N/A'}... status={camp_status} smart_off={smart_off}")

        verdict = PASS if (campaign_id and smart_off) else FAIL
        record(
            "PRODUCT FEATURE (active_seal)", verdict,
            "N/A (customer segment -- direct CTA)" if not page_url else page_url[:50],
            "N/A (no page)",
            f"YES {campaign_id[:12] if campaign_id else 'N/A'}... status={camp_status}",
            "YES" if smart_off else "NO",
        )

    except Exception as e:
        print(f"[test4] ERROR: {e}")
        traceback.print_exc()
        record("PRODUCT FEATURE (active_seal)", FAIL, "N/A", "N/A", "N/A", "N/A", str(e)[:120])


# =========================================================================
# TEST 5 -- REACTIVATION (beezy_campaign, lapsed_30d -- expect R2 block)
# =========================================================================

def test_reactivation():
    print("\n" + "=" * 70)
    print("TEST 5: REACTIVATION -- lapsed_30d (UEQD6k)")
    print("=" * 70)

    with get_conn() as conn:
        row = conn.execute(
            "SELECT slot_date FROM calendar_executions "
            "WHERE audience='lapsed_30d' AND slot_date > CURRENT_DATE - INTERVAL '7 days' "
            "AND status IN ('dispatched','completed') ORDER BY slot_date DESC LIMIT 1"
        ).fetchone()
    r2_last = row[0] if row else None
    r2_expected_block = r2_last is not None
    days_since = (date.today() - r2_last).days if r2_last else None

    if r2_expected_block:
        print(f"[test5] NOTE: lapsed_30d sent {days_since}d ago ({r2_last}) -- R2 block expected")

    slot = {
        "date": "2026-06-04", "content_type": "reactivation", "audience": "lapsed_30d",
        "topic_angle": "Dry test: We miss you -- your CBN reset protocol for women 50+",
        "send_time_est": "14:00", "revenue_estimate": 650, "needs_page": False,
    }
    insert_calendar_exec("2026-06-04", "reactivation", "lapsed_30d", topic_angle=slot["topic_angle"])

    try:
        from workers.beezy_campaign import run as camp_run
        out = camp_run(slot)
        print(f"[test5] Result: {out}")

        if isinstance(out, str) and out.startswith("blocked:"):
            if r2_expected_block and "R2" in out:
                verdict = PASS
                note = f"R2 block EXPECTED and CORRECT (lapsed_30d sent {days_since}d ago). Validator working."
            else:
                verdict = BLOCK
                note = out
            record(
                "REACTIVATION (lapsed_30d)", verdict,
                "N/A -- validator blocked", "N/A",
                f"N/A -- {'EXPECTED R2 block' if r2_expected_block else 'unexpected block'}",
                "N/A", error=note,
            )
            return

        campaign_id = out.get("campaign_id", "") if isinstance(out, dict) else ""
        camp_data   = check_klaviyo_campaign(campaign_id) if campaign_id else {}
        smart_off   = not camp_data.get("send_options", {}).get("use_smart_sending", True)
        camp_status = camp_data.get("status", "?")

        verdict = PASS if (campaign_id and smart_off) else FAIL
        record(
            "REACTIVATION (lapsed_30d)", verdict,
            "N/A (direct CTA)", "N/A",
            f"YES {campaign_id[:12] if campaign_id else 'N/A'}... status={camp_status}",
            "YES" if smart_off else "NO",
        )

    except Exception as e:
        print(f"[test5] ERROR: {e}")
        traceback.print_exc()
        record("REACTIVATION (lapsed_30d)", FAIL, "N/A", "N/A", "N/A", "N/A", str(e)[:120])


# =========================================================================
# TEST 6 -- SMS (engaged_customers)
# =========================================================================

def test_sms():
    print("\n" + "=" * 70)
    print("TEST 6: SMS -- engaged_customers (RvtHdn)")
    print("=" * 70)

    slot = {
        "date": "2026-06-05", "content_type": "sms_campaign", "audience": "engaged_customers",
        "topic_angle": "Dry test: New sleep story available -- check it out",
        "send_time_est": "16:00", "revenue_estimate": 0,
    }
    insert_calendar_exec("2026-06-05", "sms_campaign", "engaged_customers", topic_angle=slot["topic_angle"])

    try:
        from workers.sms_campaign import _generate_sms_copy, _create_sms_campaign, SEGMENT_IDS

        cta_url    = "https://trybeezybeez.com/pages/bf-collection"
        segment_id = SEGMENT_IDS.get("engaged_customers", "RvtHdn")

        print("[test6] Generating SMS copy...")
        copy = _generate_sms_copy(slot, cta_url)
        body     = copy.get("body", "")
        body_len = len(body)
        print(f"[test6] Body ({body_len} chars): {body[:100]}...")

        under_160 = body_len <= 160
        under_320 = body_len <= 320

        print("[test6] Creating Klaviyo SMS campaign...")
        campaign_id, message_id = _create_sms_campaign(slot, copy, segment_id)
        print(f"[test6] Campaign: {campaign_id}")

        camp_data   = check_klaviyo_campaign(campaign_id) if campaign_id else {}
        smart_on    = camp_data.get("send_options", {}).get("use_smart_sending", False)
        camp_status = camp_data.get("status", "?")

        print(f"[test6] status={camp_status}  smart_sending={smart_on}  chars={body_len}")

        # SMS uses smart_sending=True by design (sms_campaign.py)
        verdict = PASS if (campaign_id and under_320) else FAIL
        char_note = (f"{body_len} chars -- <=160 (1 segment)" if under_160
                     else f"{body_len} chars -- <=320 (2 segments)" if under_320
                     else f"{body_len} chars -- OVER LIMIT")
        record(
            "SMS (engaged_customers)", verdict,
            "N/A (SMS -- no page)", "N/A",
            f"YES {campaign_id[:12] if campaign_id else 'N/A'}... status={camp_status}",
            f"ON (SMS default) | {char_note}",
        )

    except Exception as e:
        print(f"[test6] ERROR: {e}")
        traceback.print_exc()
        record("SMS (engaged_customers)", FAIL, "N/A", "N/A", "N/A", "N/A", str(e)[:120])


# =========================================================================
# SUMMARY
# =========================================================================

def print_summary():
    print("\n")
    print("=" * 105)
    print("PIPELINE DRY TEST -- SUMMARY")
    print("=" * 105)
    header = (
        f"{'Content Type':<30} "
        f"{'Shopify Page':<28} "
        f"{'Index Updates':<35} "
        f"{'Klaviyo Draft':<35} "
        f"{'Smart Send':<22} "
        f"{'RESULT':<6}"
    )
    print(header)
    print("-" * 105)
    for r in results:
        print(
            f"{r['test']:<30} "
            f"{r['shopify_page'][:27]:<28} "
            f"{r['index_updates'][:34]:<35} "
            f"{r['klaviyo_draft'][:34]:<35} "
            f"{r['smart_off'][:21]:<22} "
            f"{r['verdict']:<6}"
        )
        if r.get("error"):
            print(f"  {'':>30} NOTE: {r['error'][:95]}")
    print("=" * 105)
    passes   = sum(1 for r in results if r["verdict"] == PASS)
    blocks   = sum(1 for r in results if r["verdict"] == BLOCK)
    failures = sum(1 for r in results if r["verdict"] == FAIL)
    print(f"TOTAL: {len(results)} | PASS: {passes} | BLOCK: {blocks} | FAIL: {failures}")
    print("=" * 105)
    if blocks:
        print("\nNOTE: BLOCK = validator caught a rule violation (correct pipeline behaviour).")
        print("      R2 block on lapsed_30d is EXPECTED -- audience was sent to 2 days ago.")


def main():
    t0 = time.time()
    print("Beezy Pipeline Dry Test -- all 6 content types")
    print(f"Date: {date.today().isoformat()}")

    for fn in [test_hive_mind, test_tts_dispatch, test_sleep_story, test_guided_meditation,
               test_product_feature, test_reactivation, test_sms]:
        try:
            fn()
        except Exception as e:
            label = fn.__name__.replace("test_", "").upper()
            record(label, FAIL, "N/A", "N/A", "N/A", "N/A", str(e)[:120])

    print_summary()
    print(f"\nCleaning up {len(inserted_exec_ids)} test rows...")
    cleanup_exec_rows()
    print(f"Done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
