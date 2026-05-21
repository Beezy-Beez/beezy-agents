"""
Sleep audio page template validator (beezy-sleep-story-page v2.0).

Runs BEFORE pushing a page to Shopify. Catches the v1.1 broken-template
patterns (transcript section, hm-gate, library breadcrumbs) that tanked
conversion 6x vs the locked Bridge v1.0 template.

Used by:
  workers/episode_deployer.py     (pre-produced episodes, Step 1)
  workers/sleep_audio_producer.py (generate-from-scratch path, create_page site)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── FORBIDDEN — any match = fail ────────────────────────────────────────────
# Each entry: (rule_id, compiled_regex, human_description)
_FORBIDDEN: list[tuple[str, re.Pattern[str], str]] = [
    ("transcript_section",
     re.compile(r'<section\b[^>]*\bclass\s*=\s*["\'][^"\']*\bepis-transcript\b', re.IGNORECASE),
     'transcript section: <section class="epis-transcript">'),
    ("transcript_id",
     re.compile(r'\bid\s*=\s*["\']epis-transcript[-"\']', re.IGNORECASE),
     'transcript element id="epis-transcript..."'),
    ("transcript_heading",
     re.compile(r'<h[1-6][^>]*>\s*Transcript\s*</h[1-6]>', re.IGNORECASE),
     'visible "Transcript" heading'),
    ("hm_gate_id",
     re.compile(r'\bid\s*=\s*["\']hm-gate["\']', re.IGNORECASE),
     'Hive Mind subscribe gate: id="hm-gate"'),
    ("hm_gate_class",
     re.compile(r'\bclass\s*=\s*["\'][^"\']*\bhm-gate\b', re.IGNORECASE),
     'Hive Mind subscribe gate: class="hm-gate"'),
    ("epis_crumb_class",
     re.compile(r'\bclass\s*=\s*["\'][^"\']*\bepis-crumb\b', re.IGNORECASE),
     'library breadcrumb: class="epis-crumb"'),
    ("back_to_library",
     re.compile(r'Back to\s+(?:the\s+)?(?:Meditation|Sleep)\s+Library', re.IGNORECASE),
     '"Back to (the) (Meditation|Sleep) Library" breadcrumb text'),
    ("medical_framing",
     re.compile(r'About this meditation', re.IGNORECASE),
     '"About this meditation" medical-framing header'),
]

# ── REQUIRED — all must match = pass ────────────────────────────────────────
# Three regex-based; "three_product_stack" is href-count-based (handled below).
_REQUIRED_REGEX: list[tuple[str, re.Pattern[str], str]] = [
    ("sleep_science_hub_link",
     re.compile(r'/pages/sleep-science-hub', re.IGNORECASE),
     '/pages/sleep-science-hub link (hub CTA)'),
    ("buzzsprout_embed",
     re.compile(r'buzzsprout\.com/\d+/\d+|<iframe[^>]+buzzsprout\.com', re.IGNORECASE),
     'Buzzsprout audio embed (player URL or iframe)'),
    ("hero_image",
     re.compile(r'<img\b[^>]+src\s*=\s*["\'][^"\']+\.(?:jpg|jpeg|png|webp|gif)', re.IGNORECASE),
     'hero <img> with image URL'),
]

_PRODUCT_HREF_RE = re.compile(r'href\s*=\s*["\'][^"\']*/products/[^"\']+', re.IGNORECASE)
_MIN_PRODUCT_LINKS = 3

_HANDLE_MAX = 50
_HANDLE_FORBIDDEN_PREFIX = "episode-"


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    forbidden_violations: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    handle_violations: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Short summary suitable for calendar_executions.notes."""
        parts = []
        if self.forbidden_violations:
            parts.append(f"{len(self.forbidden_violations)}forbidden")
        if self.missing_required:
            parts.append(f"{len(self.missing_required)}missing")
        if self.handle_violations:
            parts.append(f"{len(self.handle_violations)}handle")
        return ",".join(parts) or "ok"


class PageValidationError(Exception):
    """Raised when validate_page returns passed=False."""
    def __init__(self, result: ValidationResult):
        self.result = result
        super().__init__(
            f"Page validation failed: "
            f"{len(result.forbidden_violations)} forbidden, "
            f"{len(result.missing_required)} missing, "
            f"{len(result.handle_violations)} handle issues"
        )


def _check_handle(handle: str) -> list[str]:
    errors = []
    if handle.startswith(_HANDLE_FORBIDDEN_PREFIX):
        errors.append(f"handle {handle!r} starts with forbidden {_HANDLE_FORBIDDEN_PREFIX!r} prefix")
    if len(handle) > _HANDLE_MAX:
        errors.append(f"handle {handle!r} exceeds {_HANDLE_MAX} chars ({len(handle)})")
    return errors


def _check_forbidden(html: str) -> list[str]:
    violations = []
    for rule_id, pattern, description in _FORBIDDEN:
        if pattern.search(html):
            violations.append(f"{rule_id}: {description}")
    return violations


def _check_required(html: str) -> list[str]:
    missing = []
    for rule_id, pattern, description in _REQUIRED_REGEX:
        if not pattern.search(html):
            missing.append(f"{rule_id}: {description}")
    product_links = _PRODUCT_HREF_RE.findall(html)
    if len(product_links) < _MIN_PRODUCT_LINKS:
        missing.append(
            f"three_product_stack: found {len(product_links)} /products/ link(s), "
            f"need ≥{_MIN_PRODUCT_LINKS}"
        )
    return missing


def validate_page(html: str, handle: str) -> ValidationResult:
    """Check a sleep audio page body + handle against beezy-sleep-story-page v2.0.

    Returns ValidationResult with passed=False on any violation.
    Callers raise PageValidationError(result) to abort the publish.
    """
    forbidden = _check_forbidden(html)
    missing = _check_required(html)
    handle_errs = _check_handle(handle)
    return ValidationResult(
        passed=not (forbidden or missing or handle_errs),
        forbidden_violations=forbidden,
        missing_required=missing,
        handle_violations=handle_errs,
    )


def format_failure_slack(title: str, result: ValidationResult) -> tuple[str, str]:
    """Build (title, body) tuple for Slack notify_failure on validation failure."""
    header = f"🛑 Page validation failed: {title}"
    lines = ["Episode page was NOT published. Manual remediation required.", ""]
    if result.forbidden_violations:
        lines.append(f"*Forbidden patterns found ({len(result.forbidden_violations)}):*")
        for v in result.forbidden_violations:
            lines.append(f"  • {v}")
        lines.append("")
    if result.missing_required:
        lines.append(f"*Required elements missing ({len(result.missing_required)}):*")
        for v in result.missing_required:
            lines.append(f"  • {v}")
        lines.append("")
    if result.handle_violations:
        lines.append(f"*Handle issues ({len(result.handle_violations)}):*")
        for v in result.handle_violations:
            lines.append(f"  • {v}")
        lines.append("")
    lines.append("Episode metadata preserved. Rebuild via beezy-sleep-story-page v2.0 template, then re-run the deployer.")
    return header, "\n".join(lines)
