"""Pacing package — Layer 1 (strategic brain) and Layer 2 (orchestrator).

- `brain.py`    — daily revenue-vs-target check, decides priorities
- `calendar.py` — monthly content plan, data-driven
- `cron.py`     — reads priorities and queues runs for the right Skill at the right time
"""

from . import brain, calendar, cron  # noqa: F401
