#!/usr/bin/env python3
"""Run the TIP-1.0 conformance validator against TokenPak itself.

Reference-implementation gate per Constitution §13.3. TokenPak-the-
package claims conformance for the profiles listed in
``tokenpak.core.contracts.capabilities.SELF_PROFILES``; this script
runs the validator (``tokenpak_tip_validator``) against TokenPak's
self-declared capability set and exits non-zero on any ERROR finding.

What this script validates today:
  1. Every capability label in SELF_CAPABILITIES parses as a valid
     ``tip.*`` or ``ext.*`` label (wire.validate_capability_set).
  2. TokenPak's declared capability set satisfies the requirements of
     every profile in SELF_PROFILES (profiles.validate_profile).
  3. Every tip.* label TokenPak publishes is in the registry catalog
     (implicit in validate_profile).

What it does NOT validate yet (blocked on D1 code consolidation):
  - Real wire-header shapes from live requests
  - Real telemetry rows from live requests
  - Pipeline-stage behavior conformance

Those gates come online as services.execute stages gain real logic
from pipeline extraction (D1). The script is forward-compatible:
when those checks are addable, they land here.

Exit codes:
  0  — TokenPak conforms to every profile in SELF_PROFILES
  1  — at least one profile failed validation
  2  — tooling error (validator not installed, registry not found)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _find_registry_root() -> Path | None:
    """Locate a registry checkout for schema resolution.

    Order: TOKENPAK_REGISTRY_ROOT env var → sibling ``registry/``
    directory (common layout for dev envs that clone registry next
    to tokenpak) → default home locations.
    """
    env = os.environ.get("TOKENPAK_REGISTRY_ROOT")
    if env and (Path(env) / "schemas" / "tip" / "capabilities.schema.json").exists():
        return Path(env)
    here = Path(__file__).resolve().parent.parent.parent
    for candidate in [
        here / "registry",                    # sibling of tokenpak repo
        Path.home() / "registry",              # typical user layout
    ]:
        if (candidate / "schemas" / "tip" / "capabilities.schema.json").exists():
            return candidate
    return None


def main() -> int:
    # Resolve registry
    registry_root = _find_registry_root()
    if registry_root is None:
        print(
            "error: TokenPak registry not found. "
            "Set TOKENPAK_REGISTRY_ROOT or clone tokenpak/registry next to this repo.",
            file=sys.stderr,
        )
        return 2
    os.environ["TOKENPAK_REGISTRY_ROOT"] = str(registry_root)

    # Put the validator package on sys.path (it ships in the registry repo
    # under tokenpak_tip_validator/ — pre-PyPI publish).
    sys.path.insert(0, str(registry_root))

    try:
        from tokenpak_tip_validator import (
            validate_capability_set,
            validate_profile,
        )
    except ImportError as exc:
        print(
            f"error: tokenpak_tip_validator not importable: {exc}. "
            f"Registry root: {registry_root}",
            file=sys.stderr,
        )
        return 2

    from tokenpak.core.contracts.capabilities import (
        SELF_CAPABILITIES,
        SELF_PROFILES,
    )

    all_ok = True
    print(f"TokenPak TIP-1.0 conformance check (registry: {registry_root})")
    print(f"Self-declared capabilities: {sorted(SELF_CAPABILITIES)}")
    print(f"Self-declared profiles: {SELF_PROFILES}")
    print("-" * 60)

    # 1. Capability-label format check.
    cap_result = validate_capability_set(list(SELF_CAPABILITIES))
    print(f"capability-set: {cap_result.summary()}")
    if not cap_result.ok:
        all_ok = False
        for f in cap_result.errors():
            print(f"  ✗ {f.code}: {f.message}")

    # 2. Per-profile compliance.
    for profile in SELF_PROFILES:
        profile_result = validate_profile(
            profile,
            capabilities=list(SELF_CAPABILITIES),
        )
        print(f"profile {profile}: {profile_result.summary()}")
        for f in profile_result.errors():
            print(f"  ✗ {f.code}: {f.message}")
        for f in profile_result.warnings():
            print(f"  ! {f.code}: {f.message}")
        if not profile_result.ok:
            all_ok = False

    print("-" * 60)
    if all_ok:
        print("✅ TokenPak conforms to every declared profile.")
        return 0
    else:
        print("❌ TokenPak does not yet conform to every declared profile.")
        print("   Fix the code OR file a TIP-1.0 amendment — do not silently ignore.")
        print("   (Constitution §13.3 reference-implementation rule.)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
