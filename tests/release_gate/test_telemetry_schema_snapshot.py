"""Deterministic coverage for the user-facing SQLite schema snapshot."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "release_gate" / "gen_telemetry_schema.py"
_SNAPSHOT = _REPO_ROOT / "tokenpak" / "_snapshots" / "telemetry-schema.json"
_SPEC = importlib.util.spec_from_file_location("gen_telemetry_schema_under_test", _SCRIPT)
assert _SPEC and _SPEC.loader
schema_snapshot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(schema_snapshot)


def _fingerprint(snapshot: dict) -> list[dict]:
    return [{"path": store["path"], "ddl": store["ddl"]} for store in snapshot["stores"]]


def _store(snapshot: dict, path: str) -> dict:
    return next(store for store in snapshot["stores"] if store["path"] == path)


def _object_sql(store: dict, *, object_type: str, name: str) -> str:
    objects = store["ddl"]["objects"]
    return next(obj["sql"] for obj in objects if obj["type"] == object_type and obj["name"] == name)


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return schema_snapshot.build_snapshot()


@pytest.mark.timeout(90)
def test_materializes_every_store_without_ambient_home_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    poisoned_home = tmp_path / "home"
    (poisoned_home / ".tpk").mkdir(parents=True)
    (poisoned_home / ".tokenpak").mkdir()
    for relative_path in (
        ".tpk/telemetry.db",
        ".tpk/monitor.db",
        ".tokenpak/spend_guard.db",
    ):
        (poisoned_home / relative_path).write_text("not sqlite")
    monkeypatch.setenv("HOME", str(poisoned_home))

    isolated = schema_snapshot.build_snapshot()
    committed = json.loads(_SNAPSHOT.read_text())
    assert _fingerprint(isolated) == _fingerprint(committed)
    assert [store["path"] for store in isolated["stores"]] == [
        "~/.tpk/telemetry.db",
        "~/.tokenpak/spend_guard.db",
        "~/.tpk/monitor.db",
    ]
    assert all(store["exists"] is True for store in isolated["stores"])
    assert all(store["ddl"]["objects"] for store in isolated["stores"])


def test_monitor_snapshot_covers_runtime_schema_owners(snapshot: dict) -> None:
    monitor = _store(snapshot, "~/.tpk/monitor.db")
    object_names = {obj["name"] for obj in monitor["ddl"]["objects"]}
    assert {"requests", "provider_events", "idx_provider_events_ts"} <= object_names

    requests_sql = _object_sql(monitor, object_type="table", name="requests")
    assert "timestamp TEXT NOT NULL" in requests_sql
    assert "stop_reason TEXT DEFAULT ''" in requests_sql

    provider_events_sql = _object_sql(monitor, object_type="table", name="provider_events")
    assert "event TEXT NOT NULL" in provider_events_sql
    assert "reason TEXT DEFAULT ''" in provider_events_sql

    provider_events_index_sql = _object_sql(
        monitor, object_type="index", name="idx_provider_events_ts"
    )
    assert "ON provider_events(timestamp)" in provider_events_index_sql


def test_shared_spend_guard_store_contains_both_owned_schemas(snapshot: dict) -> None:
    spend_guard = _store(snapshot, "~/.tokenpak/spend_guard.db")
    object_names = {obj["name"] for obj in spend_guard["ddl"]["objects"]}
    assert {
        "pending_requests",
        "spend_guard_audit",
        "context_approvals",
        "expired_approval_uses",
    } <= object_names
    pending_sql = _object_sql(spend_guard, object_type="table", name="pending_requests")
    assert "projected_input_tokens INTEGER" in pending_sql
    assert "block_response BLOB" in pending_sql


def test_build_snapshot_is_ddl_deterministic(snapshot: dict) -> None:
    assert _fingerprint(schema_snapshot.build_snapshot()) == _fingerprint(snapshot)


def test_check_rejects_a_changed_monitor_column(
    snapshot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = json.loads(json.dumps(snapshot))
    monitor = _store(stale, "~/.tpk/monitor.db")
    requests = next(
        obj
        for obj in monitor["ddl"]["objects"]
        if obj["type"] == "table" and obj["name"] == "requests"
    )
    requests["sql"] = requests["sql"].replace(",\n                stop_reason TEXT DEFAULT ''", "")
    out = tmp_path / "telemetry-schema.json"
    out.write_text(json.dumps(stale))
    monkeypatch.setattr(schema_snapshot, "build_snapshot", lambda: snapshot)
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT), "--check", "--out", str(out)])
    assert schema_snapshot.main() == 1

    out.write_text(json.dumps(snapshot))
    assert schema_snapshot.main() == 0
