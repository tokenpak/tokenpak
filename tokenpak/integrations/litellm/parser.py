"""Parse TokenPak from LiteLLM-style request dicts.

Handles four detection patterns:

1. Explicit ``tokenpak`` kwarg — ``completion(tokenpak=pack)``
2. Message content with ``type: tokenpak`` — auto-detection in messages
3. Raw dict payload with a top-level ``"tokenpak"`` key
4. TOKPAK wire-format string in system message
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# Regex to detect TOKPAK wire-format preamble
_TOKPAK_PREAMBLE = re.compile(r"^TOKPAK:\d+", re.MULTILINE)


def parse_tokenpak_request(
    kwargs: Dict[str, Any],
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """Extract a TokenPak from a LiteLLM completion kwargs dict.

    Returns:
        A tuple ``(pack, cleaned_kwargs)`` where ``pack`` is the detected
        TokenPak (or ``None``) and ``cleaned_kwargs`` has the ``tokenpak``
        key removed and is ready for ``litellm.completion(**cleaned_kwargs)``.
    """
    cleaned = dict(kwargs)

    # Pattern 1: explicit tokenpak kwarg
    if "tokenpak" in cleaned:
        pack = cleaned.pop("tokenpak")
        return pack, cleaned

    # Pattern 2: message content auto-detection
    messages: List[Dict] = cleaned.get("messages", [])
    for i, msg in enumerate(messages):
        content = msg.get("content")
        if isinstance(content, dict) and content.get("type") == "tokenpak":
            pack_data = content.get("pack", content)
            # Remove the tokenpak message from the list
            new_messages = [m for j, m in enumerate(messages) if j != i]
            cleaned["messages"] = new_messages
            return pack_data, cleaned

    # Pattern 3: raw dict payload — called from proxy handler
    if "tokenpak" in (cleaned.get("_raw_body") or {}):
        raw = cleaned.pop("_raw_body", {})
        pack = raw["tokenpak"]
        return pack, cleaned

    # Pattern 4: existing system message is TOKPAK wire-format (passthrough)
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, str) and _TOKPAK_PREAMBLE.search(content):
                # Already compiled — pass through as-is
                return None, cleaned

    return None, cleaned


def extract_budget_from_kwargs(kwargs: Dict[str, Any]) -> int:
    """Extract token budget from LiteLLM kwargs (model context window heuristic)."""
    budget = kwargs.get("tokenpak_budget") or kwargs.get("max_tokens") or 8000
    return int(budget)


def extract_compaction_from_kwargs(kwargs: Dict[str, Any]) -> str:
    """Extract compaction strategy from LiteLLM kwargs."""
    strategy = kwargs.get("tokenpak_compaction", "balanced")
    if strategy not in ("none", "balanced", "aggressive"):
        strategy = "balanced"
    return strategy
