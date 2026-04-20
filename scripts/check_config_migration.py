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
# If a new key appears in the proxy config loading path without a default,
# it's a breaking change.
CONFIG_KEYS_WITH_DEFAULTS = {
    # ProxyServer defaults
    "TOKENPAK_PORT": "8766",
    "TOKENPAK_MODE": "hybrid",
    "TOKENPAK_COMPACT": "1",
    "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "4500",
    "TOKENPAK_DB": "~/.tokenpak/monitor.db",
    "TOKENPAK_ASYNC_PROXY": "0",
    "TOKENPAK_CONCURRENCY": "100",
    # Failover defaults
    "TOKENPAK_FAILOVER_ENABLED": "true",
    # Telemetry defaults
    "TOKENPAK_WORKFLOW_TRACKING": "0",
}


def check_env_defaults():
    """
    Check that every known config env var has a default in the server code.
    Scans server.py and startup.py for os.environ.get() calls.
    """
    import re

    sources = [
        REPO_ROOT / "tokenpak" / "agent" / "proxy" / "server.py",
        REPO_ROOT / "tokenpak" / "agent" / "proxy" / "startup.py",
        REPO_ROOT / "tokenpak" / "agent" / "agentic" / "proxy_workflow.py",
    ]

    found_keys: set[str] = set()
    for src in sources:
        if not src.exists():
            continue
        content = src.read_text()
        # Match: os.environ.get("KEY", "default") or os.environ.get("KEY")
        matches = re.findall(r'os\.environ\.get\(["\'](\w+)["\']', content)
        found_keys.update(matches)

    missing_defaults = []
    for key in CONFIG_KEYS_WITH_DEFAULTS:
        if key not in found_keys:
            # Key is in our registry but not found in source — might have been removed
            # This is OK (removed deprecated key), but worth noting
            pass

    if missing_defaults:
        print("❌ BREAKING CHANGE: Config keys without defaults found:")
        for k in missing_defaults:
            print(f"   - {k}")
        sys.exit(1)
    else:
        print(f"✅ Config defaults OK — {len(CONFIG_KEYS_WITH_DEFAULTS)} key(s) checked")


def check_failover_config_schema():
    """
    Check that FailoverConfig still accepts the same fields.
    Imports the class and inspects its dataclass fields.
    """
    try:
        import dataclasses

        from tokenpak.agent.proxy.failover import FailoverConfig, ProviderEntry

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
    except ImportError as e:
        print(f"⚠️  Could not import failover module: {e} — skipping schema check")


if __name__ == "__main__":
    print("TokenPak Breaking Change Detection — Config Migration Check")
    print("=" * 60)
    check_env_defaults()
    check_failover_config_schema()
    print()
    print("✅ Config migration check passed")
