# SPDX-License-Identifier: Apache-2.0
"""Canonical mapping from paid features to their minimum license tier.

Deliberately kept as a second, external-facing gating vocabulary
--------------------------------------------------------------

This module is a public import surface for the private `tokenpak-paid`
package, not an internal-only OSS detail. `tokenpak_paid.entitlements`
imports `TIER_FEATURES` and `LicenseTier` directly (see its `_tier_grants`
and `required_tier_for`) to power `gate_command`, the runtime gate wrapped
around every paid CLI command. Deleting or renaming anything here without a
coordinated change on that side would silently disable Pro entitlement
checks for every paid command (`tokenpak_paid` catches the resulting
`ImportError` and fails a check closed, which reads as "not entitled" even
for a paying customer whose license predates a feature).

The in-repo OSS choke point for Pro gating is `tokenpak.licensing`
(`_GATES` / `is_feature_enabled`) — see its module docstring. That table
uses fine-grained coded feature IDs (`C3_code_compression`,
`T9_replay_system`, ...) for the OSS `tokenpak features` CLI surface and
in-tree OSS capability checks; this table uses coarser, human-named IDs
(`compression_advanced`, `team_analytics`, ...) that predate it and are
baked into the external `tokenpak-paid` contract. The two vocabularies are
NOT a 1:1 mapping today and must not be assumed equivalent by name
similarity alone — confirming a full mapping requires product/pricing
context this repo alone cannot verify (which coded OSS features, if any,
a coarse Pro-side label like `team_analytics` or `tokenpak_server` is
meant to cover).

One feature is a confirmed exact match, verified by reading every current
`tokenpak-paid` call site: `"ab_testing"` here is the same capability as
`licensing._GATES["X1_ab_testing"]`. Both are locked at Pro today, and a
regression test (`tests/license/test_gating_tables_no_silent_divergence.py`)
executes both choke points and asserts they keep agreeing. Every other
name in `TIER_FEATURES` is unconfirmed against `_GATES` and should be
treated as its own entry, not silently merged into or replaced by a coded
ID, until that mapping is confirmed with whoever owns the `tokenpak-paid`
command definitions.
"""

from __future__ import annotations

from enum import Enum
from functools import total_ordering


@total_ordering
class LicenseTier(Enum):
    """License tiers ordered from least to most capable."""

    FREE = "free"
    PRO = "pro"

    @classmethod
    def ladder(cls) -> tuple["LicenseTier", ...]:
        """Return license tiers in ascending capability order."""
        return (cls.FREE, cls.PRO)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, LicenseTier):
            return NotImplemented
        return self.ladder().index(self) < self.ladder().index(other)


# External contract: every one of these names is read by name from
# `tokenpak_paid.entitlements` today (via `make_gated_command` call sites
# across `tokenpak_paid/commands/*.py`). Do not rename or remove an entry
# here without confirming no paid command still names it.
TIER_FEATURES: dict[LicenseTier, list[str]] = {
    LicenseTier.FREE: [],
    LicenseTier.PRO: [
        "ab_testing",
        "audit_log",
        "cli",
        "compression_advanced",
        "compression_basic",
        "debug_mode",
        "model_routing_intelligent",
        "model_routing_local",
        "multipak_capture",
        "replay_store",
        "seat_management",
        "sla",
        "team_analytics",
        "tokenpak_server",
    ],
}


def required_tier_for(feature: str) -> str | None:
    """Return the lowest tier that introduces a feature, or ``None``.

    This is the choke point `tokenpak_paid.entitlements.gate_command` calls
    (via its own `required_tier_for` wrapper) to decide what a locked paid
    command should say it requires. See the module docstring for why this
    table is not simply replaced by `tokenpak.licensing.is_feature_enabled`.
    """
    for tier in LicenseTier.ladder():
        if feature in TIER_FEATURES[tier]:
            return tier.value
    return None
