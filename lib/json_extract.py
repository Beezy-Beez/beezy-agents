"""Lenient JSON extraction for LLM responses.

LLMs that emit long string fields (2,000-word html_body, multi-paragraph
email copy) frequently break strict JSON with raw newlines or stray quotes.
`loads_lenient` recovers a dict where `json.loads` would raise.

Escalation, cheapest first:
  1. strip markdown fences, extract the outermost {...}
  2. json.loads (strict)
  3. json.loads(strict=False)         — tolerates control chars in strings
  4. escape raw newlines/tabs inside string literals, retry

Raises ValueError (never lets a JSONDecodeError escape) so callers can
turn a bad model response into a clean, non-fatal failure.
"""
from __future__ import annotations

import json
import re
from typing import Any


def _extract_object(raw: str) -> str:
    raw = raw.strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                raw = part
                break
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]
    return raw


def _escape_ctrl_in_strings(s: str) -> str:
    """Escape literal newlines/tabs/CRs that appear inside JSON string values."""
    out, in_str, esc = [], False, False
    for ch in s:
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\":
            out.append(ch)
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            out.append(ch)
            continue
        if in_str and ch == "\n":
            out.append("\\n")
        elif in_str and ch == "\r":
            out.append("\\r")
        elif in_str and ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return "".join(out)


def loads_lenient(raw: str) -> dict[str, Any]:
    obj = _extract_object(raw)
    try:
        result = json.loads(obj)
    except json.JSONDecodeError:
        try:
            result = json.loads(obj, strict=False)
        except json.JSONDecodeError:
            try:
                result = json.loads(_escape_ctrl_in_strings(obj), strict=False)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Could not parse JSON from model response ({e}). "
                    f"First 200 chars: {obj[:200]!r}"
                ) from e
    if not isinstance(result, dict):
        raise ValueError(
            f"Model response parsed to {type(result).__name__}, expected object: {str(result)[:200]!r}"
        )
    return result
