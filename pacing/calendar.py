"""Monthly content calendar generator.

Once per month: produce a data-driven content plan covering the upcoming period
(Hive Mind issues, SEO blog cadence, sleep audio drops, Klaviyo campaigns, SMS,
flow tuning experiments). The calendar becomes input for `pacing/brain.py`'s
daily prioritization and ultimately for `pacing/cron.py`'s queueing.

Reads:
  - active `goals`
  - last ~90 days of `performance` (which channels are converting)
  - `strategies` (current per-component strategy)

Writes:
  - a calendar artifact (stored as a `decisions` row of type 'calendar_plan'
    or in a future dedicated table) capturing planned slots per day with the
    intended worker/skill and topic angle.
"""


def generate(month_start) -> dict:
    """Generate a monthly content calendar starting at month_start (date)."""
    raise NotImplementedError(
        "Pull goals + 90d performance + active strategies. Call Opus to produce "
        "an ordered slot plan per day for the upcoming month. Return a structured "
        "calendar object; persist to the decisions table."
    )


def run_monthly():
    """Monthly entrypoint — triggered on the 1st of each month."""
    raise NotImplementedError(
        "Compute next month_start, call generate(), persist result, post a Slack "
        "digest of the proposed calendar for review."
    )


if __name__ == "__main__":
    run_monthly()
