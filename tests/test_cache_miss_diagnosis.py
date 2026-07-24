# SPDX-License-Identifier: Apache-2.0
"""
tests/test_cache_miss_diagnosis.py

Tests for the prefix-aware cache-miss classifier in
``tokenpak.proxy.cache_poison.diagnose_cache_miss``.

The classifier must blame a UUID/request-id only when the volatile token sits
inside the cached prefix (up to and including the last ``cache_control`` block)
AND the prefix actually changed versus the prior request. Tokens in the
volatile tail, in static tool schemas, or in an unchanged prefix must NOT be
attributed to "uuid". It must also stay read-only on the request body so that
byte-preserved Claude Code semantics are never affected.
"""

from __future__ import annotations

import json

from tokenpak.proxy.cache_poison import (
    CacheMissDiagnosis,
    classify_cache_miss_reason,
    diagnose_cache_miss,
    strip_cache_poisons,
)

U1 = "11111111-2222-3333-4444-555555555555"
U2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _body(prefix_text: str, tail_text: str = "hello", *, breakpoint: bool = True) -> bytes:
    """A Claude-Code-shaped request: cache_control on the system block."""
    sys_block = {"type": "text", "text": prefix_text}
    if breakpoint:
        sys_block["cache_control"] = {"type": "ephemeral"}
    return json.dumps(
        {
            "system": [sys_block],
            "messages": [{"role": "user", "content": [{"type": "text", "text": tail_text}]}],
        }
    ).encode()


def _diag_pair(b1: bytes, b2: bytes) -> CacheMissDiagnosis:
    """Diagnose b2 using b1 as the prior request's prefix state."""
    d1 = diagnose_cache_miss(b1)
    return diagnose_cache_miss(
        b2,
        prior_prefix_fingerprint=d1.prefix_fingerprint,
        prior_prefix_id_hashes=d1.prefix_id_hashes,
    )


# ---------------------------------------------------------------------------
# Core attribution rules
# ---------------------------------------------------------------------------


def test_uuid_in_changed_prefix_is_blamed():
    """UUID in the cached prefix that changes between requests → 'uuid'."""
    d = _diag_pair(_body(f"You are helpful. session {U1}"), _body(f"You are helpful. session {U2}"))
    assert d.reason == "uuid"
    assert d.location == "prefix"
    assert d.value_changed is True


def test_uuid_in_volatile_tail_is_not_blamed():
    """UUID only in the latest user turn (after the breakpoint) → not blamed."""
    d = _diag_pair(
        _body("You are helpful. STATIC", tail_text="turn one"),
        _body("You are helpful. STATIC", tail_text=f"turn id {U2}"),
    )
    assert d.reason is None
    assert d.location == "tail"
    assert d.value_changed is False  # prefix unchanged


def test_uuid_in_static_tool_schema_is_not_blamed():
    """request_id/uuid in a tool schema that never changes → not blamed."""
    tb = json.dumps(
        {
            "tools": [{"type": "tool", "input_schema": {"request_id": f"string {U1}"}}],
            "system": [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": "hi"}],
        }
    ).encode()
    d = _diag_pair(tb, tb)
    assert d.reason is None  # identical prefix → no poison
    assert d.value_changed is False


def test_first_request_never_blames_uuid():
    """A cold first miss (no prior) is never attributed to a uuid."""
    d = diagnose_cache_miss(_body(f"You are helpful. session {U1}"))
    assert d.reason is None
    assert d.value_changed is False
    # but the prefix fingerprint + id hashes are populated for the next diff
    assert d.prefix_fingerprint
    assert len(d.prefix_id_hashes) == 1


def test_no_cache_control_returns_none():
    """No breakpoint anywhere → caching not requested → not a prefix poison."""
    b = _body(f"sys {U1}", breakpoint=False)
    assert diagnose_cache_miss(b).reason is None
    assert diagnose_cache_miss(b).location == "none"


def test_changed_timestamp_in_prefix_is_blamed_as_timestamp():
    d = _diag_pair(
        _body("Current time: 2026-05-27T10:00:00Z. You are helpful."),
        _body("Current time: 2026-05-27T10:05:00Z. You are helpful."),
    )
    assert d.reason == "timestamp"
    assert d.value_changed is True


def test_changed_request_id_literal_in_prefix_is_blamed_as_uuid():
    d = _diag_pair(
        _body("ctx request_id=alpha-001. You are helpful."),
        _body("ctx request_id=alpha-002. You are helpful."),
    )
    assert d.reason == "uuid"


def test_changed_prefix_without_volatile_token_is_prefix_drift():
    """Prefix changed but no uuid/timestamp/request_id → 'prefix_drift', not uuid."""
    d = _diag_pair(_body("You are helpful. mode=alpha"), _body("You are helpful. mode=beta"))
    assert d.reason == "prefix_drift"
    assert d.value_changed is True


def test_stable_prefix_with_uuid_is_not_blamed():
    """Same UUID in the prefix across requests (stable) → not blamed."""
    b = _body(f"You are helpful. build {U1}")
    d = _diag_pair(b, b)
    assert d.reason is None
    assert d.value_changed is False


# ---------------------------------------------------------------------------
# Safety: read-only + redaction
# ---------------------------------------------------------------------------


def test_diagnosis_is_read_only_on_body():
    """Byte-preserved invariant: diagnosis must not mutate the request bytes."""
    b = _body(f"You are helpful. session {U1}")
    snapshot = bytes(b)
    diagnose_cache_miss(b)
    assert b == snapshot


def test_debug_line_leaks_no_raw_uuid():
    """Opt-in forensic line carries only derived metadata, never raw ids."""
    d = _diag_pair(_body(f"sys {U1}"), _body(f"sys {U2}"))
    line = d.debug_line()
    assert U1 not in line and U2 not in line
    assert "reason=uuid" in line


def test_malformed_body_fails_open():
    assert diagnose_cache_miss(b"not json").reason is None
    assert diagnose_cache_miss(b"").reason is None
    assert diagnose_cache_miss(None).reason is None


# ---------------------------------------------------------------------------
# Legacy classifier + scrubber still intact (no regression)
# ---------------------------------------------------------------------------


def test_legacy_classifier_still_works():
    assert classify_cache_miss_reason(b"", False, True, b"") == "schema_tool_change"
    assert (
        classify_cache_miss_reason(b'{"x":"2026-05-27T10:00:00Z"}', True, False, b"")
        == "timestamp_poison"
    )


def test_strip_cache_poisons_still_scrubs():
    body = json.dumps(
        {"messages": [{"role": "user", "content": f"id {U1} at 2026-05-27T10:00:00Z"}]}
    ).encode()
    out = strip_cache_poisons(body)
    assert U1 not in out.decode()
    assert b"[id]" in out and b"[time]" in out
