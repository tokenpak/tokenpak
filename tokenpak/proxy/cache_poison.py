"""
TokenPak cache-poison scrubbing utilities extracted from the proxy monolith.

Dynamic content (timestamps, UUIDs, heartbeat counters) embedded in prompts
prevents Anthropic prompt-cache hits.  This module strips those patterns from
request bodies before they reach the upstream API.

Provides:
- strip_cache_poisons()  / _strip_cache_poisons() — sanitise request body bytes
- classify_cache_miss_reason() / _classify_cache_miss_reason() — diagnostics
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterator, Optional, Tuple

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
# request_id / x-request-id style keys appearing as literal text in a prompt.
_REQUEST_ID_PATTERN = re.compile(r"\b(?:x-)?request[_-]?id\b", re.IGNORECASE)


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

        def _scrub_content(content: object) -> object:
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


# ---------------------------------------------------------------------------
# Prefix-aware cache-miss diagnosis
# ---------------------------------------------------------------------------
#
# ``classify_cache_miss_reason`` above is a coarse whole-body substring test.
# On byte-preserved Claude Code traffic (where the client owns the
# ``cache_control`` breakpoints and the proxy must not scrub) it over-attributes
# misses to "uuid" whenever the literal strings "uuid"/"request_id" appear
# *anywhere* in the body — including the volatile tail, tool schemas, docs, and
# the vault context TokenPak itself appends after the breakpoint.
#
# ``diagnose_cache_miss`` is the precise replacement: a UUID/timestamp is only
# blamed when it sits inside the *cached prefix* (the region up to and including
# the last ``cache_control`` block) AND the prefix actually changed versus the
# previous request in the same session. It returns derived metadata only —
# hashed values, field labels, offsets — never raw prompt content.


def _hash16(text: str) -> str:
    """Short, stable, non-reversible digest used for change-detection only."""
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:16]


def _block_text(block: Any) -> str:
    """Best-effort text extraction from a content/system/tool block."""
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        txt = block.get("text")
        if isinstance(txt, str):
            return txt
        # tool definitions / non-text blocks: fold their schema to text so a
        # *changing* schema is detectable, but it is not treated as a uuid.
        if "input_schema" in block or block.get("type") == "tool":
            try:
                return json.dumps(block.get("input_schema", ""), sort_keys=True)
            except Exception:
                return ""
    return ""


def _iter_cacheable_segments(data: Dict[str, Any]) -> Iterator[Tuple[str, str, bool]]:
    """Yield ``(label, text, has_cache_control)`` in Anthropic cache order.

    Order is ``tools`` → ``system`` → ``messages`` — the order in which the
    prompt prefix is assembled and cached. ``has_cache_control`` marks a block
    that carries a ``cache_control`` breakpoint.
    """
    for i, tool in enumerate(data.get("tools") or []):
        if isinstance(tool, dict):
            yield (f"tools[{i}]", _block_text(tool), bool(tool.get("cache_control")))

    system = data.get("system")
    if isinstance(system, str):
        yield ("system", system, False)
    elif isinstance(system, list):
        for i, blk in enumerate(system):
            if isinstance(blk, dict):
                yield (f"system[{i}]", _block_text(blk), bool(blk.get("cache_control")))

    for mi, msg in enumerate(data.get("messages") or []):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            yield (f"messages[{mi}]", content, False)
        elif isinstance(content, list):
            for bi, blk in enumerate(content):
                if isinstance(blk, dict):
                    yield (
                        f"messages[{mi}].content[{bi}]",
                        _block_text(blk),
                        bool(blk.get("cache_control")),
                    )


@dataclass
class CacheMissDiagnosis:
    """Result of a prefix-aware cache-miss diagnosis.

    All fields are derived metadata safe to log — no raw prompt content.
    """

    reason: Optional[str] = None  # "uuid" | "timestamp" | "prefix_drift" | None
    location: str = "none"  # "prefix" | "tail" | "none"
    matched_field: str = ""  # block label, e.g. "system[0]"
    value_changed: bool = False  # cached prefix differs from prior request
    breakpoint_index: int = -1  # ordinal of the last cache_control block
    prefix_fingerprint: str = ""  # hash of the cached-prefix text (for next diff)
    prefix_id_hashes: FrozenSet[str] = field(default_factory=frozenset)  # hashed uuids in prefix

    def debug_line(self) -> str:
        """Redacted one-line summary for opt-in forensic logging."""
        return (
            f"cache-miss reason={self.reason or 'none'} loc={self.location} "
            f"field={self.matched_field or '-'} changed={self.value_changed} "
            f"bp={self.breakpoint_index} fp={self.prefix_fingerprint or '-'} "
            f"ids={len(self.prefix_id_hashes)}"
        )


def diagnose_cache_miss(
    body_bytes: Optional[bytes],
    *,
    prior_prefix_fingerprint: Optional[str] = None,
    prior_prefix_id_hashes: Optional[FrozenSet[str]] = None,
) -> CacheMissDiagnosis:
    """Diagnose *why* a prompt-cache miss happened, prefix-aware.

    A UUID/request-id (or timestamp) is only attributed as the cause when it
    lives in the cached prefix — the region up to and including the final
    ``cache_control`` breakpoint — and the prefix changed versus the prior
    request (so a stable id, or one in the volatile tail / tool schema, is not
    blamed). ``prior_*`` carry the previous request's prefix state for the same
    session; pass ``None`` on the first observed request (a cold miss is never
    blamed on a uuid).

    Returns a :class:`CacheMissDiagnosis`. The caller stores
    ``prefix_fingerprint`` / ``prefix_id_hashes`` to feed the next call.
    Read-only: never mutates or re-serialises ``body_bytes``.
    """
    diag = CacheMissDiagnosis()
    if not body_bytes:
        return diag
    try:
        data = json.loads(body_bytes)
    except Exception:
        return diag
    if not isinstance(data, dict):
        return diag

    segments = list(_iter_cacheable_segments(data))
    if not segments:
        return diag

    # Last cache_control block defines the cached-prefix boundary.
    last_bp = -1
    for idx, (_label, _text, has_cc) in enumerate(segments):
        if has_cc:
            last_bp = idx
    diag.breakpoint_index = last_bp
    if last_bp < 0:
        # No breakpoint => caching not requested; a miss here is expected and
        # is not a prefix poison.
        return diag

    prefix_segments = segments[: last_bp + 1]
    tail_segments = segments[last_bp + 1 :]
    prefix_text = "\n".join(t for _l, t, _c in prefix_segments)
    tail_text = "\n".join(t for _l, t, _c in tail_segments)

    diag.prefix_fingerprint = _hash16(prefix_text)
    prefix_uuids = _UUID_PATTERN.findall(prefix_text)
    diag.prefix_id_hashes = frozenset(_hash16(u) for u in prefix_uuids)

    # Did the cached prefix actually change since the prior request?
    if prior_prefix_fingerprint is None:
        diag.value_changed = False  # first observed request — cannot confirm
    else:
        diag.value_changed = diag.prefix_fingerprint != prior_prefix_fingerprint

    # Locate any volatile token, recording where it lives (for forensics).
    def _first_match(label_filter: list[Tuple[str, str, bool]]) -> str:
        for label, text, _cc in label_filter:
            if (
                _UUID_PATTERN.search(text)
                or _TIMESTAMP_PATTERN.search(text)
                or _REQUEST_ID_PATTERN.search(text)
            ):
                return label
        return ""

    prefix_field = _first_match(prefix_segments)
    if prefix_field:
        diag.location = "prefix"
        diag.matched_field = prefix_field
    elif _first_match(tail_segments):
        diag.location = "tail"
        diag.matched_field = _first_match(tail_segments)

    # Attribute a reason ONLY when the prefix changed between requests. A stable
    # prefix that still missed is a cold/TTL/tail event, not a uuid poison.
    if not diag.value_changed:
        return diag

    id_set_changed = (
        prior_prefix_id_hashes is None or diag.prefix_id_hashes != prior_prefix_id_hashes
    )
    if prefix_uuids and id_set_changed:
        diag.reason = "uuid"
    elif _TIMESTAMP_PATTERN.search(prefix_text):
        diag.reason = "timestamp"
    elif _REQUEST_ID_PATTERN.search(prefix_text):
        diag.reason = "uuid"
    else:
        diag.reason = "prefix_drift"
    return diag


# Legacy names used by runtime/proxy.py
_strip_cache_poisons = strip_cache_poisons
_classify_cache_miss_reason = classify_cache_miss_reason
