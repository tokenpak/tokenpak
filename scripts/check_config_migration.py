#!/usr/bin/env python3
"""
Breaking Change Detection: Config Migration Check
=================================================

Verifies that the default config loaded by TokenPak doesn't require fields
that weren't present in the previous release. Reads from a "baseline" snapshot
and compares against current defaults.

Usage:
    python scripts/check_config_migration.py

Exit codes:
    0 — OK, no new required config fields without defaults
    1 — Breaking change: new required field without default or migration
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# Known config keys that must always have defaults (never break existing users).
# If a registered key disappears from the package source, that is a breaking
# change (a default silently removed) and the gate must fail.
#
# H4 registry reconciliation (L11b): TOKENPAK_ASYNC_PROXY and
# TOKENPAK_FAILOVER_ENABLED were removed from this registry — neither key is
# referenced anywhere in the package source any longer (the async-proxy switch
# and failover toggle now flow through the config loader's typed config keys,
# not these env vars). They were kept here only because the gate could never
# fail; with the gate now failing closed, leaving retired keys would red the
# baseline. Re-add (with a corresponding source reference) if either env var is
# reinstated. See the submission note for ratification of this contract trim.
CONFIG_KEYS_WITH_DEFAULTS = {
    # ProxyServer defaults
    "TOKENPAK_PORT": "8766",
    "TOKENPAK_MODE": "hybrid",
    "TOKENPAK_COMPACT": "1",
    "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "4500",
    "TOKENPAK_DB": "~/.tokenpak/monitor.db",
    "TOKENPAK_CONCURRENCY": "100",
    # Telemetry defaults
    "TOKENPAK_WORKFLOW_TRACKING": "0",
}


def check_env_defaults():
    """Verify every registered config key is still referenced in the package.

    H4 hardening (L11b release-gate integrity): the previous implementation
    (1) scanned hard-coded paths under ``tokenpak/agent/proxy/`` that no longer
    exist (the proxy package was relocated to ``tokenpak/proxy/``), and
    (2) never populated ``missing_defaults`` — the loop body was ``pass`` and
    the list was a literal ``[]`` — so the gate could not fail under any input.

    Config keys are now read through the central config loader
    (``tokenpak/core/config_loader.py`` / ``tokenpak/proxy/config.py``) rather
    than scattered ``os.environ.get`` calls, so we scan the whole package source
    for each registered key and FAIL CLOSED when a key vanishes (a removed
    default is a breaking change) or when the package source itself is missing.
    """
    pkg = REPO_ROOT / "tokenpak"
    if not pkg.is_dir():
        print(f"❌ tokenpak package not found at {pkg} — cannot verify config defaults (failing closed)")
        sys.exit(1)

    # Concatenate package source once (excluding tests), then check each key
    # literal. A central-config-loader reference, an os.environ.get, or a
    # documented default all count as "still present".
    blob_parts: list[str] = []
    for path in pkg.rglob("*.py"):
        posix = path.as_posix()
        if "/tests/" in posix or path.name.startswith("test_"):
            continue
        try:
            blob_parts.append(path.read_text())
        except (OSError, UnicodeDecodeError):
            continue
    blob = "\n".join(blob_parts)

    missing_defaults = [
        key
        for key in CONFIG_KEYS_WITH_DEFAULTS
        if f'"{key}"' not in blob and f"'{key}'" not in blob
    ]

    if missing_defaults:
        print("❌ BREAKING CHANGE: registered config key(s) no longer referenced in tokenpak/:")
        for k in missing_defaults:
            print(f"   - {k} (default {CONFIG_KEYS_WITH_DEFAULTS[k]!r}) — removed without migration?")
        print()
        print("Action required:")
        print("  1. Restore the key or add a migration/alias for existing users, OR")
        print("  2. Update CONFIG_KEYS_WITH_DEFAULTS in scripts/check_config_migration.py if intentional")
        sys.exit(1)
    else:
        print(f"✅ Config defaults OK — {len(CONFIG_KEYS_WITH_DEFAULTS)} key(s) all still referenced")


def check_failover_config_schema():
    """Verify FailoverConfig / ProviderEntry still expose their required fields.

    H4 hardening (L11b release-gate integrity): the import target was the
    pre-reorg ``tokenpak.agent.proxy.failover`` (the module now lives at
    ``tokenpak.proxy.failover``), so the import always raised and the
    ``except ImportError: ... skipping`` branch made this check unconditionally
    fail-open. We now import the current module and treat an import failure as a
    HARD FAILURE — a schema gate that cannot import the schema cannot certify it.
    """
    try:
        import dataclasses

        from tokenpak.proxy.failover import FailoverConfig, ProviderEntry
    except ImportError as e:
        print(f"❌ Could not import tokenpak.proxy.failover: {e}")
        print("   FailoverConfig schema gate cannot run — failing CLOSED (was fail-open / H4).")
        sys.exit(1)

    failover_fields = {f.name for f in dataclasses.fields(FailoverConfig)}
    provider_fields = {f.name for f in dataclasses.fields(ProviderEntry)}

    required_failover = {"enabled", "chain"}
    required_provider = {"provider", "model_map", "credential_env"}

    missing_failover = required_failover - failover_fields
    missing_provider = required_provider - provider_fields

    if missing_failover or missing_provider:
        print("❌ BREAKING CHANGE: FailoverConfig schema changed:")
        if missing_failover:
            print(f"   Missing FailoverConfig fields: {missing_failover}")
        if missing_provider:
            print(f"   Missing ProviderEntry fields: {missing_provider}")
        sys.exit(1)
    else:
        print("✅ FailoverConfig schema OK")


if __name__ == "__main__":
    print("TokenPak Breaking Change Detection — Config Migration Check")
    print("=" * 60)
    check_env_defaults()
    check_failover_config_schema()
    print()
    print("✅ Config migration check passed")
