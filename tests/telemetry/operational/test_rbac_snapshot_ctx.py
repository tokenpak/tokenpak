"""Snapshot-context guard for RBAC admin bootstrap (snapgen log hygiene).

When ``TOKENPAK_SNAPSHOT_GEN=1`` (set by the release-gate snapshot generators
``gen_api_snapshot.py`` / ``gen_telemetry_schema.py``), the RBAC operational
store must NOT bootstrap a default admin: snapshot generation only introspects
schema and must not mutate the store or emit first-run side effects that would
pollute deterministic snapshot output. With the env unset, the normal first-run
admin bootstrap is preserved.
"""

from __future__ import annotations

import pytest

try:
    from tokenpak.telemetry.operational.rbac_auth import RBACStore
except ImportError:  # flask (telemetry optional extra) not installed in slim CI
    pytest.skip("requires flask (telemetry optional extra)", allow_module_level=True)


def _count_users(store: RBACStore) -> int:
    with store._conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM tp_users").fetchone()[0]


def test_no_bootstrap_under_snapshot_gen(tmp_path, monkeypatch):
    """TOKENPAK_SNAPSHOT_GEN=1 -> no admin created, store left empty."""
    monkeypatch.setenv("TOKENPAK_SNAPSHOT_GEN", "1")
    monkeypatch.delenv("TOKENPAK_ADMIN_BOOTSTRAP", raising=False)
    store = RBACStore(db_path=str(tmp_path / "rbac.db"))
    assert _count_users(store) == 0


def test_bootstrap_preserved_when_env_unset(tmp_path, monkeypatch):
    """Env unset -> first-run bootstrap still creates the default admin."""
    monkeypatch.delenv("TOKENPAK_SNAPSHOT_GEN", raising=False)
    monkeypatch.delenv("TOKENPAK_ADMIN_BOOTSTRAP", raising=False)
    store = RBACStore(db_path=str(tmp_path / "rbac.db"))
    assert _count_users(store) >= 1
