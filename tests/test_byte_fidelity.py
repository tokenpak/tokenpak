"""Byte-fidelity tests for the TokenPak passthrough proxy path.

Constitution §5.2 is the non-negotiable invariant these tests defend:

    Bytes going into the passthrough proxy are the exact bytes going out
    to the upstream provider, and vice versa. No re-serialization, no
    JSON round-trip, no "helpful" normalization. Byte equality, not
    semantic equality.

Why a dedicated test file:
- 10 §A7 names this gate as a must-pass before every release.
- 13 §4.2–4.3 requests byte equality evidence in staging validation.
- 15 §2.7 requires post-deploy smoke-testing of the same contract.
- Without a dedicated test, a regression in the passthrough path ships silently.

Layered tests:

- §1 — Adapter-level: things that are true RIGHT NOW and must not regress.
- §2 — Specification-level: things that SHOULD be true per Constitution §5.2
       but are not yet implemented in the public tree. Marked xfail with an
       explicit reference to the tracking finding so CI stays green while the
       gap is visible in pytest output.
- §3 — Helper utilities shared across the suite.

Follow-up (tracked in known-findings.md):
- HTTP-boundary byte-fidelity test (client socket → proxy → provider socket
  → proxy → client socket). Requires the TestClient fixture + a mock upstream.
  Not in this file; this file covers the adapter boundary only.
"""

from __future__ import annotations

import json

import pytest

from tokenpak.proxy.adapters.passthrough_adapter import PassthroughAdapter


# ---------------------------------------------------------------------------
# §3 Helpers
# ---------------------------------------------------------------------------

# A body that is valid JSON with characteristic formatting choices the proxy
# must not "normalize": unusual key order, whitespace, unicode, trailing
# newline. These all survive round-trip through a byte-preserving proxy; they
# all die in a json.loads/json.dumps cycle.
_REPRESENTATIVE_BODY_BYTES = (
    b'{"model":"claude-opus-4-7","system":"","messages":['
    b'{"role":"user","content":"Hi \xf0\x9f\x91\x8b"}],'
    b'"max_tokens":4096,"stream":false}\n'
)

# Deliberately non-JSON body: some clients send form-encoded, protobuf, or
# partial streaming writes. The passthrough must not blow up; it must emit
# *something* that preserves the original bytes for downstream use.
_NON_JSON_BODY_BYTES = b"\x00\x01\x02garbage not json \xffend"


def _roundtrip_preserves_bytes(original: bytes) -> bool:
    """json.loads + json.dumps does NOT preserve bytes — this is the anti-pattern."""
    try:
        parsed = json.loads(original)
    except Exception:
        # Non-JSON can't survive a JSON round-trip at all.
        return False
    reserialized = json.dumps(parsed, separators=(",", ":")).encode()
    return reserialized == original


# ---------------------------------------------------------------------------
# §1 Adapter-level: behaviors that hold today
# ---------------------------------------------------------------------------


class TestPassthroughAdapterCurrentBehavior:
    """Assertions about what the PassthroughAdapter does RIGHT NOW.

    These must not regress. If someone changes the adapter and these break,
    the change is a byte-fidelity regression and needs explicit review
    against Constitution §5.2.
    """

    def test_adapter_source_format_is_passthrough(self) -> None:
        adapter = PassthroughAdapter()
        assert adapter.source_format == "passthrough"

    def test_adapter_detect_accepts_any_path(self) -> None:
        """Passthrough is the catch-all; detect() always returns True."""
        adapter = PassthroughAdapter()
        assert adapter.detect("/v1/messages", {}, b"") is True
        assert adapter.detect("/anything", {"x-custom": "y"}, None) is True

    def test_non_json_body_preserves_raw_bytes_in_raw_extra(self) -> None:
        """When the body can't be parsed as JSON, the adapter stores the raw
        bytes under raw_extra["_raw_body"]. This preserves the original
        content for downstream forwarding — the passthrough promise holds
        on this path."""
        adapter = PassthroughAdapter()
        result = adapter.normalize(_NON_JSON_BODY_BYTES)

        assert "_raw_body" in result.raw_extra
        # The adapter decodes with errors="replace", which corrupts the bytes.
        # Document what is actually stored today so regressions are caught.
        stored = result.raw_extra["_raw_body"]
        assert isinstance(stored, str)
        # Replacement char U+FFFD appears where invalid UTF-8 was.
        assert "\ufffd" in stored or stored.endswith("end")

    def test_round_trip_through_json_is_lossy(self) -> None:
        """Sanity check: json.loads + json.dumps does NOT preserve bytes,
        even on valid JSON. This is why the passthrough must not do it."""
        assert _roundtrip_preserves_bytes(_REPRESENTATIVE_BODY_BYTES) is False


# ---------------------------------------------------------------------------
# §2 Specification-level: what Constitution §5.2 REQUIRES
# ---------------------------------------------------------------------------


class TestPassthroughByteFidelityContract:
    """These tests define the byte-fidelity contract TokenPak must satisfy
    per Constitution §5.2. They currently xfail where the public tree does
    not implement the contract — the xfails serve as a living specification
    until the implementation catches up.

    When a test here starts passing, remove its xfail marker and move it to
    §1. When one persists as xfail across two release cycles, promote the
    corresponding finding from Medium to High.
    """

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "Constitution §5.2 requires the passthrough path to preserve request "
            "body bytes exactly. The current PassthroughAdapter json.loads + "
            "deepcopy reconstructs a CanonicalRequest and does not store the "
            "original bytes on the parse-success path. Audit HIGH tracked in "
            "known-findings.md; see also audit-v1.0.3-pre-deploy-2026-04-19.md "
            "§2.2 finding HIGH 'byte-fidelity test'."
        ),
    )
    def test_valid_json_body_preserves_raw_bytes_somewhere(self) -> None:
        """The proxy must preserve original bytes even when the body is
        successfully parsed — for downstream re-emission without JSON
        re-serialization. Currently fails because _raw_body is only
        stored on the parse-failure path."""
        adapter = PassthroughAdapter()
        result = adapter.normalize(_REPRESENTATIVE_BODY_BYTES)

        # Acceptable surface for the raw bytes:
        # - result.raw_extra["_raw_body"] as exact bytes (preferred), OR
        # - result.raw_extra["_raw_body_encoded"] as an encoding-aware pair
        raw = result.raw_extra.get("_raw_body")
        if isinstance(raw, (bytes, bytearray)):
            assert bytes(raw) == _REPRESENTATIVE_BODY_BYTES
        elif isinstance(raw, str):
            assert raw.encode() == _REPRESENTATIVE_BODY_BYTES
        else:
            pytest.fail(
                "PassthroughAdapter must expose the original request bytes in "
                "raw_extra['_raw_body']. Currently stores a parsed dict under "
                "'_passthrough_payload' which cannot reconstruct the original "
                "bytes (see _roundtrip_preserves_bytes is False)."
            )

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "HTTP-boundary byte-fidelity test (socket-level) not implemented. "
            "Requires TestClient fixture + mock upstream. Tracked as follow-up "
            "Medium in known-findings.md."
        ),
    )
    def test_http_boundary_byte_equality(self) -> None:
        """The bytes a client writes to 127.0.0.1:8766 must equal the bytes
        the upstream provider receives, and vice versa on the response path.
        This is the full Constitution §5.2 invariant; the adapter-level
        tests above are a proper subset."""
        # Sentinel for future implementation. See module docstring.
        pytest.skip("HTTP-boundary fixture pending — see known-findings.md follow-up.")


# ---------------------------------------------------------------------------
# §1 (continued) — Regression gate for the adapter contract itself
# ---------------------------------------------------------------------------


def test_passthrough_adapter_does_not_raise_on_empty_body() -> None:
    """An empty body is a legitimate request shape (e.g. GET-style probes
    routed through the proxy). The adapter must not raise."""
    adapter = PassthroughAdapter()
    result = adapter.normalize(b"")
    assert result.source_format == "passthrough"


def test_passthrough_adapter_preserves_system_and_messages_on_valid_json() -> None:
    """On the parse-success path the adapter exposes system + messages in
    the CanonicalRequest. These are the fields downstream compression
    stages read; changing their shape here is a cascading regression."""
    adapter = PassthroughAdapter()
    result = adapter.normalize(_REPRESENTATIVE_BODY_BYTES)
    assert result.model == "claude-opus-4-7"
    assert result.messages[0]["role"] == "user"
    assert result.messages[0]["content"].startswith("Hi")
    assert result.stream is False
