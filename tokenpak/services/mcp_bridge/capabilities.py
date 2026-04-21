"""Capability negotiation for MCP peers.

Intersects the local component's published TIP capability set
(from ``core/contracts/capabilities.py`` — Phase 3 populates the
authoritative list) with the peer's set. The result is the set of
capabilities both sides agree to use.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class NegotiatedCapabilities:
    """Result of a capability negotiation."""

    local: frozenset[str]
    peer: frozenset[str]
    agreed: frozenset[str]


class CapabilityNegotiator:
    """Negotiate a capability set against an MCP peer.

    ``local_labels`` is the set of TIP capability labels this component
    publishes (e.g. ``{"tip.compression.v1", "tip.preview.local"}``).
    ``peer_labels`` comes from the peer's MCP initialize payload.
    """

    def __init__(self, local_labels: frozenset[str]) -> None:
        self.local = local_labels

    def negotiate(self, peer_labels: frozenset[str]) -> NegotiatedCapabilities:
        agreed = self.local & peer_labels
        return NegotiatedCapabilities(
            local=self.local,
            peer=peer_labels,
            agreed=agreed,
        )

    def requires(
        self,
        required: frozenset[str],
        peer_labels: frozenset[str],
    ) -> None:
        """Raise if the peer is missing any required capability.

        Per docs/protocol/compatibility.md: when a required capability
        is missing, the connection MUST fail with a well-formed TIP
        error rather than silently proceeding.
        """
        missing = required - peer_labels
        if missing:
            from .errors import MCPBridgeError

            raise MCPBridgeError(
                f"peer missing required capabilities: {sorted(missing)}"
            )
