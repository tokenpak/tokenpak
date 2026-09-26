# SPDX-License-Identifier: Apache-2.0
"""tokenpak.agent.license — live external contract, NOT a dead shim.

Unlike its sibling ``agent.cli.commands.{serve,metrics}``, this package is
not internal debris: ``tokenpak_paid.entitlements`` imports
``TIER_FEATURES`` and ``LicenseTier`` directly from ``.validator`` (see
that module's docstring) to gate real paid CLI commands for real Pro
customers. No code in *this* repository imports from here — the
in-repo choke point for Pro gating is ``tokenpak.licensing``
(``is_feature_enabled``) — but that is not the same thing as "unused";
do not attach a deprecation warning or removal plan to this package
without first confirming with whoever owns the `tokenpak-paid` command
definitions that nothing still needs it.
"""

from .validator import TIER_FEATURES, LicenseTier, required_tier_for

__all__ = ["LicenseTier", "TIER_FEATURES", "required_tier_for"]
