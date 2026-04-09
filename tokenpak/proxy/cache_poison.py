"""
TokenPak cache-poison scrubbing utilities extracted from the proxy monolith.

Dynamic content (timestamps, UUIDs, heartbeat counters) embedded in prompts
prevents Anthropic prompt-cache hits.  This module strips those patterns from
request bodies before they reach the upstream API.

Provides:
- strip_cache_poisons()  / _strip_cache_poisons() — sanitise request body bytes
- classify_cache_miss_reason() / _classify_cache_miss_reason() — diagnostics
"""

import json
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b"
)
_HEARTBEAT_COUNTER = re.compile(r"Heartbeat\s*#?\s*\d+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def strip_cache_poisons(body_bytes: bytes) -> bytes:
    """
    Strip dynamic content that breaks prompt-cache hits:

    - ISO timestamps embedded in prompts (e.g. "Current time: 2026-03-09T17:00:00Z")
    - UUIDs embedded in prompts (e.g. "request_id: a1b2c3d4-…")
    - Heartbeat counters (e.g. "Heartbeat #1287")

    Only strips from message content strings, not from metadata fields.
    Fails open — returns original body bytes on any error.
    """
    try:
        data = json.loads(body_bytes)
        changed = False

        def _scrub(text: str) -> str:
            nonlocal changed
            original = text
            text = _UUID_PATTERN.sub("[id]", text)
            text = _TIMESTAMP_PATTERN.sub("[time]", text)
            text = _HEARTBEAT_COUNTER.sub("Heartbeat", text)
            if text != original:
                changed = True
            return text

        def _scrub_content(content):
            if isinstance(content, str):
                return _scrub(content)
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part["text"] = _scrub(part["text"])
            return content

        # Scrub message content
        for msg in data.get("messages", []):
            if isinstance(msg, dict):
                msg["content"] = _scrub_content(msg.get("content", ""))

        # Scrub system prompt (text parts only, not cache_control blocks)
        system = data.get("system")
        if isinstance(system, str):
            data["system"] = _scrub(system)
        elif isinstance(system, list):
            for part in system:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = _scrub(part["text"])

        if changed:
            return json.dumps(data, ensure_ascii=False).encode("utf-8")
        return body_bytes
    except Exception:
        return body_bytes  # fail-open


def classify_cache_miss_reason(
    raw_body: Optional[bytes],
    cache_poison_scrubbed: bool,
    tools_schema_changed: bool,
    final_body: Optional[bytes],
) -> str:
    """Best-effort classifier for cache-miss root cause."""
    if tools_schema_changed:
        return "schema_tool_change"

    raw_text = ""
    if raw_body:
        try:
            raw_text = raw_body.decode("utf-8", errors="ignore")
        except Exception:
            raw_text = ""

    if cache_poison_scrubbed:
        if _TIMESTAMP_PATTERN.search(raw_text):
            return "timestamp_poison"
        if _UUID_PATTERN.search(raw_text) or re.search(
            r"\brequest[_-]?id\b", raw_text, re.IGNORECASE
        ):
            return "uuid_request_id_poison"
        return "timestamp_poison"

    if raw_body and final_body and raw_body != final_body:
        return "retrieval_order_drift_or_unknown"

    return "retrieval_order_drift_or_unknown"


# Legacy names used by runtime/proxy.py
_strip_cache_poisons = strip_cache_poisons
_classify_cache_miss_reason = classify_cache_miss_reason
