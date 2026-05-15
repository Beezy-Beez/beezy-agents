"""On-demand dry run of the morning pipeline.

Runs the exact chain Boris sees each morning — pacing brain → orchestrator →
morning brief — with REAL copy generation and REAL validator checks, but:

  • NO Klaviyo template/campaign/discount/list creation
  • NO Shopify page / image / blog writes
  • NO calendar_executions rows written (no phantom rows)
  • NO campaigns queued for auto-schedule
  • Slack payloads PRINTED to stdout (not posted), unless --post-slack

Use this to verify a fix before the next 8am run instead of waiting.

Usage:
    python -m scripts.dry_run_pipeline                 # print everything
    python -m scripts.dry_run_pipeline --post-slack    # also post to Slack ([DRY RUN] banner)
    python -m scripts.dry_run_pipeline --strict        # respect approval gate + already-ran dedupe
    python -m scripts.dry_run_pipeline --only orchestrator   # one stage only

Stages: pacing | orchestrator | brief  (default: all three, in order)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _banner(title: str) -> None:
    print("\n" + "█" * 70)
    print("█  " + title)
    print("█" * 70)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.dry_run_pipeline")
    parser.add_argument("--post-slack", action="store_true",
                        help="actually post Slack messages (prefixed [DRY RUN]) instead of printing")
    parser.add_argument("--strict", action="store_true",
                        help="respect the orchestrator approval gate and already-ran dedupe "
                             "(default: bypass both so every slot is exercised)")
    parser.add_argument("--only", choices=["pacing", "orchestrator", "brief"],
                        help="run a single stage only")
    args = parser.parse_args(argv[1:])

    # Engage the global dry-run switch BEFORE importing pipeline modules.
    os.environ["BEEZY_DRY_RUN"] = "1"
    if args.post_slack:
        os.environ["BEEZY_DRY_RUN_POST_SLACK"] = "1"

    print("🧪 DRY RUN — no Klaviyo/Shopify writes, no phantom rows, no scheduling.")
    print("   Slack:", "POSTED with [DRY RUN] banner" if args.post_slack else "PRINTED below")
    print("   Gates:", "STRICT (real approval/dedupe)" if args.strict
          else "BYPASSED (every slot exercised)")

    stages = [args.only] if args.only else ["pacing", "orchestrator", "brief"]

    if "pacing" in stages:
        _banner("STAGE 1/3 — Pacing brain (7:30am digest)")
        try:
            from pacing.cron import run_daily as pacing_run
            pacing_run(dry_run=True)  # computes + dedup-writes pacing_state, prints payload
            print("[dry-run] pacing brain OK")
        except Exception as e:
            import traceback
            print(f"[dry-run] pacing brain RAISED: {e}")
            traceback.print_exc()

    if "orchestrator" in stages:
        _banner("STAGE 2/3 — Orchestrator (8:00am dispatch)")
        try:
            import pacing.orchestrator as orch
            if not args.strict:
                orch._is_approved = lambda conn: True          # type: ignore[assignment]
                orch._already_ran = lambda c, d, s: False       # type: ignore[assignment]
            orch.run_daily()
            print("[dry-run] orchestrator OK")
        except Exception as e:
            import traceback
            print(f"[dry-run] orchestrator RAISED: {e}")
            traceback.print_exc()

    if "brief" in stages:
        _banner("STAGE 3/3 — Morning brief (8:05am digest)")
        try:
            from workers.morning_brief import run_morning_brief
            run_morning_brief()
            print("[dry-run] morning brief OK")
        except Exception as e:
            import traceback
            print(f"[dry-run] morning brief RAISED: {e}")
            traceback.print_exc()

    _banner("DRY RUN COMPLETE — nothing was sent, written, or scheduled.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
