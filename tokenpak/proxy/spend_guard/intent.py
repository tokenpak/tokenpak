# SPDX-License-Identifier: Apache-2.0
"""Yes/No intent parser for spend guard approval flow.

Used only when a session has a pending block. Looks at the LAST user-text
segment of the new request body and matches it against curated positive /
negative token sets. Failing closed: anything that isn't a clean match
returns ``AMBIGUOUS`` and the user is re-prompted.
"""

from __future__ import annotations

import json
import re
import string
from enum import Enum


class Intent(Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    AMBIGUOUS = "ambiguous"


# Single source of truth for intent vocab — extend here, not in five places.
_POSITIVE = frozenset({
    "yes", "y", "yeah", "yep", "yup", "yea",
    "ok", "okay", "k",
    "go", "go ahead", "go for it", "send it",
    "proceed", "continue", "approve", "approved",
    "sure", "fine", "alright", "all right",
    "do it", "run it", "ship it", "let's go", "lets go",
    "confirm", "confirmed",
})

_NEGATIVE = frozenset({
    "no", "n", "nope", "nah",
    "stop", "halt", "kill", "kill it", "cancel",
    "quit", "exit", "abort", "block",
    "deny", "denied", "reject", "rejected",
    "don't", "dont", "do not",
    "nevermind", "never mind",
    "skip",
})


def _last_user_text(body: bytes) -> str:
    """Best-effort extract the trailing user-text segment.

    For an Anthropic ``/v1/messages`` body, that's the last user message's
    content — which for the approval flow is what the operator just typed.
    For other shapes, returns the whole decoded body so trivial
    one-word inputs still work.
    """
    try:
        body_text = body.decode("utf-8", errors="replace")
        body_json = json.loads(body_text)
    except Exception:
        return body.decode("utf-8", errors="replace") if body else ""

    msgs = body_json.get("messages") or []
    if isinstance(msgs, list):
        # Walk from the end for the last user-role message
        for msg in reversed(msgs):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Pick first text block
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        return str(blk.get("text") or "")
            break
    return ""


def _normalize(s: str) -> str:
    """Lowercase, strip whitespace + trailing punctuation."""
    if not s:
        return ""
    s = s.strip().lower()
    # Strip trailing punctuation (?, !, ., ',', ';', ':')
    s = s.rstrip(string.punctuation + " \t\n\r")
    # Collapse internal whitespace
    s = re.sub(r"\s+", " ", s)
    return s


def parse_intent(body: bytes) -> Intent:
    """Classify the trailing user-text as positive/negative/ambiguous.

    Returns AMBIGUOUS unless the *entire* normalized last-user-text exactly
    matches a positive or negative vocab entry. This keeps "I'll go ahead
    and write..." from being misread as a positive intent.
    """
    text = _last_user_text(body)
    norm = _normalize(text)
    if not norm:
        return Intent.AMBIGUOUS
    # Strict whole-string match: prevents agent text containing "yes" from
    # accidentally approving.
    if norm in _POSITIVE:
        return Intent.POSITIVE
    if norm in _NEGATIVE:
        return Intent.NEGATIVE
    return Intent.AMBIGUOUS


__all__ = ["Intent", "parse_intent"]
