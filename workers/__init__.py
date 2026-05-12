"""Workers package.

Workers do not contain content logic. They invoke existing Beezy Skills via the
Anthropic API. The generic invoker lives in `skill_runner.py`; per-Skill system
prompts live in `workers/prompts/<skill>.md`.
"""

from . import skill_runner  # noqa: F401
