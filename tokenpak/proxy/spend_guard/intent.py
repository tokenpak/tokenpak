# SPDX-License-Identifier: Apache-2.0
"""Yes/No intent parser for spend guard approval flow.

Used only when a session has a pending block. Looks at the newest input
item of the new request body and, when it is user text, matches it against
curated positive / negative token sets. Failing closed: anything that isn't
a clean match returns ``AMBIGUOUS`` and the user is re-prompted.
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
_POSITIVE = frozenset(
    {
        "yes",
        "y",
        "yeah",
        "yep",
        "yup",
        "yea",
        "ok",
        "okay",
        "k",
        "go",
        "go ahead",
        "go for it",
        "send it",
        "proceed",
        "continue",
        "approve",
        "approved",
        "sure",
        "fine",
        "alright",
        "all right",
        "do it",
        "run it",
        "ship it",
        "let's go",
        "lets go",
        "confirm",
        "confirmed",
    }
)

_NEGATIVE = frozenset(
    {
        "no",
        "n",
        "nope",
        "nah",
        "stop",
        "halt",
        "kill",
        "kill it",
        "cancel",
        "quit",
        "exit",
        "abort",
        "block",
        "deny",
        "denied",
        "reject",
        "rejected",
        "don't",
        "dont",
        "do not",
        "nevermind",
        "never mind",
        "skip",
    }
)


def _is_client_context(text: str) -> bool:
    """A block the client adds around the operator's text, not typed by them."""
    stripped = text.strip()
    return stripped.startswith("<system-reminder>") and stripped.endswith("</system-reminder>")


def _is_tool_result(block: object) -> bool:
    return isinstance(block, dict) and str(block.get("type") or "").endswith("tool_result")


def _typed_after_tool_result(content: list) -> str:
    """The operator's text typed after the last tool result in ``content``.

    Claude Code appends a reply typed after a 402 to the user message that
    already carries the tool result (``[tool_result, text]``), and appends
    each later reply to the same message. The reply is the last text block
    with no tool result after it. Text inside a tool result is tool output
    and is never read, and ``<system-reminder>`` blocks the client adds
    around the typed text are skipped.
    """
    for block in reversed(content):
        if not isinstance(block, dict):
            continue
        if _is_tool_result(block):
            break
        if block.get("type") == "text":
            text = str(block.get("text") or "")
            if not _is_client_context(text):
                return text
    return ""


def _last_user_text(body: bytes) -> str:
    """Best-effort extract the operator's reply: the newest input item.

    A reply answers a held request only when it is the newest item the
    client sent. An agent's tool loop keeps re-sending the operator's
    earlier ``yes`` as the last user-role message while tool calls and
    results pile up after it; reading it there would approve every later
    held request in the turn. So when the newest item is anything other
    than user text (assistant output, a tool call, a tool result with
    nothing typed after it, reasoning), there is no reply and this
    returns ``""``.

    For an Anthropic ``/v1/messages`` or Chat Completions body, that's the
    last message (see :func:`_typed_after_tool_result` when it carries tool
    results). For an OpenAI Responses body, the last ``input`` item. For
    other shapes, returns the whole decoded body so trivial one-word inputs
    still work.
    """
    try:
        body_text = body.decode("utf-8", errors="replace")
        body_json = json.loads(body_text)
    except Exception:
        return body.decode("utf-8", errors="replace") if body else ""

    if not isinstance(body_json, dict):
        return ""

    msgs = body_json.get("messages")
    if isinstance(msgs, list) and msgs:
        msg = msgs[-1]
        if not isinstance(msg, dict) or msg.get("role") != "user":
            return ""
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            if any(_is_tool_result(b) for b in content):
                return _typed_after_tool_result(content)
            # Pick first text block
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    return str(blk.get("text") or "")
        return ""

    # OpenAI Responses API request.  A Codex request carries the full
    # interaction history in ``input``; the newest item must be the user
    # message itself, not reasoning or tool items that followed it.
    inputs = body_json.get("input")
    if isinstance(inputs, str):
        return inputs
    if isinstance(inputs, list) and inputs:
        item = inputs[-1]
        if not isinstance(item, dict) or item.get("role") != "user":
            return ""
        content = item.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # The approval is the trailing user-text segment.  Accept
            # both canonical Responses ``input_text`` blocks and the
            # compatible ``text`` shape used by some clients.
            for block in reversed(content):
                if not isinstance(block, dict):
                    continue
                if block.get("type") in ("input_text", "text"):
                    return str(block.get("text") or "")
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
    """Classify the newest user-text item as positive/negative/ambiguous.

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
