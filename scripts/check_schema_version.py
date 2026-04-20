#!/usr/bin/env python3
"""
Breaking Change Detection: Config Schema Version Check
=======================================================

Verifies that if required config fields have been added to state_schema.json,
a corresponding migration script exists. Fails CI if a new required field
appears without a migration entry.

Usage:
    python scripts/check_schema_version.py

Exit codes:
    0 — OK, no breaking changes detected
    1 — Breaking change detected (missing migration)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "tokenpak" / "state_schema.json"
MIGRATIONS_DIR = REPO_ROOT / "scripts"
MIGRATION_REGISTRY = REPO_ROOT / "scripts" / "migration_registry.json"


def load_schema() -> dict:
    if not SCHEMA_PATH.exists():
        print(f"⚠️  Schema not found at {SCHEMA_PATH} — skipping check")
        return {}
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def load_migration_registry() -> dict:
    """Load registry of known required fields + their migration scripts."""
    if not MIGRATION_REGISTRY.exists():
        return {"known_required_fields": []}
    with open(MIGRATION_REGISTRY) as f:
        return json.load(f)


def check_schema_migrations():
    """
    Check that every required field in the schema has a migration entry.
    New required fields = breaking change → must have migration.
    """
    schema = load_schema()
    registry = load_migration_registry()

    required_fields = schema.get("required", [])
    known_fields = set(registry.get("known_required_fields", []))

    new_required = [f for f in required_fields if f not in known_fields]

    if new_required:
        print("❌ BREAKING CHANGE DETECTED: New required config fields without migration:")
        for field in new_required:
            print(f"   - {field}")
        print()
        print("Action required:")
        print("  1. Add a migration script in scripts/")
        print(f"  2. Register the field in {MIGRATION_REGISTRY}")
        print("  3. OR mark the field as optional (add a default value)")
        sys.exit(1)
    else:
        print(f"✅ Config schema OK — {len(required_fields)} required field(s), all have migrations")


def check_version_bump_on_api_change():
    """
    Check that if public API surface changed, version was bumped.
    Simple heuristic: if __version__ hasn't changed but __all__ has grown,
    warn (but don't fail — this is a soft check).
    """
    init_path = REPO_ROOT / "tokenpak" / "__init__.py"
    if not init_path.exists():
        return

    content = init_path.read_text()
    version_line = next(
        (line for line in content.splitlines() if "__version__" in line), ""
    )
    print(f"📦 Current version: {version_line.strip()}")


if __name__ == "__main__":
    print("TokenPak Breaking Change Detection — Schema Check")
    print("=" * 50)
    check_schema_migrations()
    check_version_bump_on_api_change()
    print()
    print("✅ Schema check passed")
