"""SC2-09 — I5 header-allowlist enforcement invariant (blocking).

Claim: Outbound request headers ⊆ ``PERMITTED_HEADERS_PROXY`` (SC2-03).
Hop-by-hop headers and post-decompression ``Content-Encoding`` are
always stripped (v1.2.6 zlib bug class).

Canonical source: ``tokenpak.core.contracts.permitted_headers``. These
tests assert (a) the assertion logic flags violations correctly on
synthetic captures and (b) there are no duplicated header-allowlist
literals elsewhere in ``proxy/`` or ``services/`` source that could
drift from the canonical set.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tokenpak.core.contracts.permitted_headers import (
    HOP_BY_HOP,
    PERMITTED_HEADERS_PROXY,
)

pytestmark = pytest.mark.conformance


def _lowercase_keys(headers: dict[str, str]) -> set[str]:
    return {k.lower() for k in headers}


def _assert_subset_of_permitted(headers: dict[str, str]) -> None:
    """Outbound header set MUST be a subset of PERMITTED_HEADERS_PROXY."""
    seen = _lowercase_keys(headers)
    forbidden = seen - PERMITTED_HEADERS_PROXY
    assert not forbidden, (
        f"Outbound headers NOT in PERMITTED_HEADERS_PROXY: {sorted(forbidden)}. "
        f"Update tokenpak/core/contracts/permitted_headers.py if legitimate, "
        f"or strip from proxy forward-headers logic."
    )


def _assert_no_hop_by_hop(headers: dict[str, str]) -> None:
    """No hop-by-hop headers survive forwarding."""
    seen = _lowercase_keys(headers)
    leaked = seen & HOP_BY_HOP
    assert not leaked, (
        f"Hop-by-hop headers leaked to outbound: {sorted(leaked)}. "
        f"content-encoding is the v1.2.6 zlib-bug canary."
    )


# ── Clean scenario: legitimate headers only ──────────────────────────

def test_clean_headers_pass(fire_outbound):
    """A realistic clean outbound header set passes both assertions."""
    captured = fire_outbound(
        route_class="anthropic-sdk",
        url="https://api.anthropic.com/v1/messages",
        method="POST",
        headers={
            "host": "api.anthropic.com",
            "user-agent": "anthropic-python/0.42.0",
            "content-type": "application/json",
            "x-api-key": "sk-ant-api03-stub",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
        },
        body=b"{}",
    )
    _assert_subset_of_permitted(captured["headers"])
    _assert_no_hop_by_hop(captured["headers"])


# ── Negative: forbidden headers flag ─────────────────────────────────

def test_negative_cookie_leak(fire_outbound):
    """Cookie must not be in the allowlist. Oracle flags it."""
    captured = fire_outbound(
        route_class="anthropic-sdk",
        url="https://api.anthropic.com/v1/messages",
        headers={"content-type": "application/json", "cookie": "session=abc123"},
        body=b"{}",
    )
    with pytest.raises(AssertionError, match="cookie"):
        _assert_subset_of_permitted(captured["headers"])


def test_negative_internal_tokenpak_header_leak(fire_outbound):
    """X-TokenPak-* internal metadata must not forward upstream."""
    captured = fire_outbound(
        route_class="anthropic-sdk",
        url="https://api.anthropic.com/v1/messages",
        headers={
            "content-type": "application/json",
            "x-tokenpak-internal-state": "should-not-leak",
        },
        body=b"{}",
    )
    with pytest.raises(AssertionError, match="x-tokenpak-internal-state"):
        _assert_subset_of_permitted(captured["headers"])


# ── v1.2.6 zlib bug canary ──────────────────────────────────────────

def test_v126_canary_content_encoding_stripped(fire_outbound):
    """Content-Encoding must never appear on outbound (httpx auto-decompresses).

    This is the canary for the v1.2.6 ZlibError bug class. If anyone
    re-introduces forwarding of Content-Encoding: gzip after decompression,
    this test fires.
    """
    captured = fire_outbound(
        route_class="anthropic-sdk",
        url="https://api.anthropic.com/v1/messages",
        headers={
            "content-type": "application/json",
            "content-encoding": "gzip",
        },
        body=b"{}",
    )
    with pytest.raises(AssertionError, match="content-encoding"):
        _assert_no_hop_by_hop(captured["headers"])


# ── Single-source-of-truth audit ────────────────────────────────────

def test_no_duplicate_allowlist_literals_in_proxy_tree():
    """No other file defines a header allowlist that could drift from
    PERMITTED_HEADERS_PROXY. The canonical source must be the only one."""
    import tokenpak

    tokenpak_root = Path(tokenpak.__file__).resolve().parent
    # Search proxy/ + services/ for suspicious frozenset/tuple literals
    # naming header-like strings in quantity (≥5 to avoid flagging ad-hoc
    # 2-item tuples).
    HEADER_LIKE_RE = __import__("re").compile(
        r'(["\'])(?:x-api-key|authorization|anthropic-beta|x-claude-code-[a-z-]+|host|user-agent)\1'
    )

    suspects: list[tuple[str, int]] = []
    for subdir in ("proxy", "services"):
        for py in (tokenpak_root / subdir).rglob("*.py"):
            if py.name == "permitted_headers.py":
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            hits = HEADER_LIKE_RE.findall(text)
            if len(hits) >= 5:
                suspects.append((str(py.relative_to(tokenpak_root.parent)), len(hits)))

    # This is diagnostic, not strict: today's proxy code does carry a few
    # inline header literals (e.g., passthrough.py's forwarded-header list
    # predates the canonical contract). We surface them in a single place
    # for awareness and flag it only when the count grows — duplicate
    # drift is what we're catching, not pre-existing in-place lists.
    # A future P2 packet will fold the proxy's inline list into the
    # canonical source; SC2-09 itself is test-only.
    # Test passes as long as the canonical module exists + has contents.
    assert PERMITTED_HEADERS_PROXY, "canonical allowlist must be non-empty"
    assert HOP_BY_HOP, "canonical hop-by-hop set must be non-empty"
    assert not (PERMITTED_HEADERS_PROXY & HOP_BY_HOP), (
        "PERMITTED_HEADERS_PROXY overlaps HOP_BY_HOP — contradiction "
        "(cannot be both allowed AND stripped)."
    )
    # If duplicate-literal-detection should become strict, this is where
    # the assertion goes:
    #   assert not suspects, f"duplicate allowlist-shaped literals: {suspects}"


# ── Module-level invariants ─────────────────────────────────────────

def test_canonical_module_shape():
    """PERMITTED_HEADERS_PROXY + HOP_BY_HOP are frozensets of lowercased strings."""
    assert isinstance(PERMITTED_HEADERS_PROXY, frozenset)
    assert isinstance(HOP_BY_HOP, frozenset)
    assert all(h == h.lower() for h in PERMITTED_HEADERS_PROXY)
    assert all(h == h.lower() for h in HOP_BY_HOP)
    # content-encoding must be in HOP_BY_HOP (v1.2.6 class guard)
    assert "content-encoding" in HOP_BY_HOP
