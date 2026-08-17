# SPDX-License-Identifier: Apache-2.0
"""Session-economics surface tests: dashboard snapshot carries the identical validated
contract values, and the config toggle suppresses only the human layout
section — the snapshot data stays present for explicit JSON consumers.
"""

from __future__ import annotations

import pytest

from tests.session_economics_fixtures import learning_payload
from tokenpak.cli.commands import dashboard as dashboard_mod
from tokenpak.core.contracts.session_economics import SessionEconomics


def test_collect_validates_and_returns_canonical_dict(monkeypatch):
    payload = learning_payload()
    monkeypatch.setattr(dashboard_mod, "_http_post", lambda *a, **k: payload)
    value = dashboard_mod._collect_session_economics(8766)
    assert value == SessionEconomics.from_dict(payload).to_dict()


def test_collect_proxy_down_is_explicit(monkeypatch):
    monkeypatch.setattr(dashboard_mod, "_http_post", lambda *a, **k: None)
    value = dashboard_mod._collect_session_economics(8766)
    assert value == {"unavailable": True, "reason": "proxy not reachable"}


def test_collect_invalid_payload_is_explicit(monkeypatch):
    monkeypatch.setattr(
        dashboard_mod, "_http_post", lambda *a, **k: {"schema_version": "session-economics/1"}
    )
    value = dashboard_mod._collect_session_economics(8766)
    assert value.get("unavailable") is True
    assert "contract validation" in value.get("reason", "")


def test_snapshot_contains_session_economics_and_source(monkeypatch):
    payload = learning_payload()
    monkeypatch.setattr(dashboard_mod, "_http_post", lambda *a, **k: payload)
    monkeypatch.setattr(dashboard_mod, "_http_get", lambda *a, **k: None)
    snapshot = dashboard_mod.collect_dashboard_snapshot("home")
    assert snapshot["session_economics"] == SessionEconomics.from_dict(payload).to_dict()
    assert snapshot["sources"]["session_economics"]["state"] == "available"


def test_values_identical_to_status_and_mcp_for_same_fixture(monkeypatch):
    """Completion criterion: one fixture, three surfaces, identical values."""
    payload = learning_payload()
    canonical = SessionEconomics.from_dict(payload).to_dict()

    # Dashboard
    monkeypatch.setattr(dashboard_mod, "_http_post", lambda *a, **k: payload)
    dash_value = dashboard_mod._collect_session_economics(8766)

    # Status (JSON surface)
    from tokenpak.cli.commands import status as status_mod

    monkeypatch.setattr(
        status_mod,
        "_fetch_session_economics",
        lambda _base: (SessionEconomics.from_dict(payload), ""),
    )
    status_value = status_mod._session_economics_json("http://127.0.0.1:8766")

    # Companion MCP tool
    import json as _json

    from tokenpak.companion.mcp import tools as tools_mod

    monkeypatch.setattr(tools_mod, "_proxy_post", lambda *a, **k: (200, payload))
    state = tools_mod.CompanionState()
    mcp_value = _json.loads(
        tools_mod._handle_session_economics(state, {"session_id": "sess-fixture-1"})
    )

    assert dash_value == canonical
    assert status_value == canonical
    assert mcp_value == canonical


def test_home_layout_section_present_when_enabled(monkeypatch):
    payload = learning_payload()
    monkeypatch.setattr(dashboard_mod, "_http_post", lambda *a, **k: payload)
    monkeypatch.setattr(dashboard_mod, "_http_get", lambda *a, **k: None)
    monkeypatch.setattr(dashboard_mod, "_session_economics_display_enabled", lambda: True)
    snapshot = dashboard_mod.collect_dashboard_snapshot("home")
    names = [s["name"] for s in snapshot["layout"]["sections"]]
    assert "session_economics" in names
    section = next(
        s for s in snapshot["layout"]["sections"] if s["name"] == "session_economics"
    )
    values = {item["label"]: item for item in section["items"]}
    assert "session economics:" in str(values["Trip computer"]["value"])
    assert values["Guard state"]["value"] == "allow"
    assert values["Forecast"]["value"] == "learning"


def test_home_layout_section_suppressed_when_disabled_data_kept(monkeypatch):
    payload = learning_payload()
    monkeypatch.setattr(dashboard_mod, "_http_post", lambda *a, **k: payload)
    monkeypatch.setattr(dashboard_mod, "_http_get", lambda *a, **k: None)
    monkeypatch.setattr(dashboard_mod, "_session_economics_display_enabled", lambda: False)
    snapshot = dashboard_mod.collect_dashboard_snapshot("home")
    names = [s["name"] for s in snapshot["layout"]["sections"]]
    assert "session_economics" not in names  # human display suppressed
    # ...but the explicit data read stays available
    assert snapshot["session_economics"] == SessionEconomics.from_dict(payload).to_dict()


def test_unavailable_layout_item(monkeypatch):
    items = dashboard_mod._session_economics_items(
        {"unavailable": True, "reason": "proxy not reachable"}
    )
    assert len(items) == 1
    assert items[0]["state"] == "unavailable"
    assert items[0]["detail"] == "proxy not reachable"


@pytest.mark.parametrize("layout", ["debug", "fleet"])
def test_other_layouts_unchanged(monkeypatch, layout):
    monkeypatch.setattr(dashboard_mod, "_http_post", lambda *a, **k: None)
    monkeypatch.setattr(dashboard_mod, "_http_get", lambda *a, **k: None)
    snapshot = dashboard_mod.collect_dashboard_snapshot(layout)
    names = [s["name"] for s in snapshot["layout"]["sections"]]
    assert "session_economics" not in names
