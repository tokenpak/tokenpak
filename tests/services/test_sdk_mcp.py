"""Tests for sdk/mcp capability mapping + client surface.

Transport wiring is pending DECISION-P2-LIB, so these tests focus on
the capability translation and surface shape.
"""

from __future__ import annotations

import pytest

from tokenpak.sdk.mcp.capability_mapping import (
    CapabilityFrame,
    from_frame,
    to_frame,
)


def test_to_frame_accepts_valid_labels():
    frame = to_frame(frozenset({"tip.compression.v1", "ext.acme.audit.v1"}))
    assert isinstance(frame, CapabilityFrame)
    assert "tip.compression.v1" in frame.labels


def test_to_frame_rejects_invalid_label():
    with pytest.raises(ValueError, match="invalid TIP capability label"):
        to_frame(frozenset({"NotATIPLabel"}))


def test_to_frame_rejects_uppercase_prefix():
    with pytest.raises(ValueError):
        to_frame(frozenset({"TIP.compression.v1"}))  # capital TIP


def test_from_frame_filters_unknown_namespaces():
    frame = CapabilityFrame(
        labels=frozenset({"tip.ok", "random-junk", "ext.ns.name"})
    )
    filtered = from_frame(frame)
    assert filtered == frozenset({"tip.ok", "ext.ns.name"})


def test_round_trip_preserves_valid_labels():
    labels = frozenset({"tip.compression.v1", "tip.preview.local"})
    assert from_frame(to_frame(labels)) == labels


def test_client_options_defaults():
    from tokenpak.sdk.mcp.client import ClientOptions
    from tokenpak.services.mcp_bridge import TransportKind

    opts = ClientOptions()
    assert opts.transport_kind is TransportKind.STDIO
    assert opts.endpoint is None
    assert opts.required_capabilities == frozenset()
