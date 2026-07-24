# SPDX-License-Identifier: Apache-2.0
"""Read-only discovery verbs: ``tokenpak dispatch routes`` / ``dispatch workers``.

Testers need to find legal route and worker ids without reading source. These
verbs enumerate the registries (packaged defaults + user overrides for routes;
packaged workers + discoverable overlays) and are strictly read-only — they must
never create the Run Ledger.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pydantic")

import tokenpak.cli.commands.dispatch_cmd as dc  # noqa: E402

_PACKAGED_ROUTES = (
    Path(dc.__file__).resolve().parent.parent.parent
    / "orchestration"
    / "dispatch"
    / "registry"
    / "routes"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tokenpak")
    sub = parser.add_subparsers(dest="command")
    dc.build_dispatch_parser(sub)
    return parser


def _invoke(argv):
    parser = _parser()
    args = parser.parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    exc = None
    rc = None
    try:
        rc = args.func(args)
    except BaseException as e:  # noqa: BLE001
        exc = e
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return rc, out.getvalue(), err.getvalue(), exc


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


def test_routes_json_exposes_ids_names_and_stations(home):
    rc, out, err, exc = _invoke(["dispatch", "routes", "--json"])
    assert exc is None, err
    assert rc == 0
    payload = json.loads(out)
    assert payload["count"] >= 1
    ids = {r["id"] for r in payload["routes"]}
    # Packaged defaults are discoverable.
    assert "route.code_task.v1" in ids
    by_id = {r["id"]: r for r in payload["routes"]}
    code = by_id["route.code_task.v1"]
    assert code["name"] == "Code Task"
    assert "code_task" in code["intents"]
    station_ids = {s["id"] for s in code["stations"]}
    assert {"build", "review"} <= station_ids
    # The user override directory is surfaced so testers know where to drop routes.
    assert payload["user_routes_dir"].endswith("dispatch/routes")


def test_routes_human_lists_ids_and_override_dir(home):
    rc, out, err, exc = _invoke(["dispatch", "routes"])
    assert exc is None, err
    assert rc == 0
    assert "route.code_task.v1" in out
    assert "User route overrides" in out


def test_routes_discovers_user_override(home):
    """A route file dropped into the user routes dir is enumerated alongside
    packaged defaults (merged registry — packaged + user)."""
    user_routes = home / "dispatch" / "routes"
    user_routes.mkdir(parents=True)
    seed = (_PACKAGED_ROUTES / "route.quick_answer.v1.yaml").read_text()
    override = seed.replace("route.quick_answer.v1", "route.user_probe.v1").replace(
        "name: Quick Answer", "name: User Probe"
    )
    (user_routes / "route.user_probe.v1.yaml").write_text(override)

    rc, out, err, exc = _invoke(["dispatch", "routes", "--json"])
    assert exc is None, err
    ids = {r["id"] for r in json.loads(out)["routes"]}
    assert "route.user_probe.v1" in ids  # user override discovered
    assert "route.code_task.v1" in ids  # packaged defaults still present


# ---------------------------------------------------------------------------
# workers
# ---------------------------------------------------------------------------


def test_workers_json_exposes_ids_roles_capabilities(home):
    rc, out, err, exc = _invoke(["dispatch", "workers", "--json"])
    assert exc is None, err
    assert rc == 0
    payload = json.loads(out)
    assert payload["count"] >= 1
    by_id = {w["id"]: w for w in payload["workers"]}
    assert "worker.builder.default.v1" in by_id
    builder = by_id["worker.builder.default.v1"]
    assert "builder" in builder["roles"]
    assert "code_drafting" in builder["capabilities"]
    # Overlays + the user overlay directory are surfaced.
    assert "overlay.code_builder.v1" in payload["overlays"]
    assert payload["user_overlay_dir"].endswith("dispatch/overlays")


def test_workers_human_lists_ids_and_capabilities(home):
    rc, out, err, exc = _invoke(["dispatch", "workers"])
    assert exc is None, err
    assert rc == 0
    assert "worker.builder.default.v1" in out
    assert "capabilities" in out


# ---------------------------------------------------------------------------
# read-only + gate consistency
# ---------------------------------------------------------------------------


def test_discovery_is_read_only_no_ledger(home):
    for verb in ("routes", "workers"):
        rc, out, err, exc = _invoke(["dispatch", verb, "--json"])
        assert exc is None, err
        assert rc == 0
    # Neither verb may open/create the Run Ledger.
    assert list(home.rglob("runs.db")) == []


@pytest.mark.parametrize("verb", ["routes", "workers"])
def test_discovery_degrades_when_runtime_absent(verb, monkeypatch):
    """Discovery verbs stay behind the runtime gate: a slim wheel (no runtime
    file) yields the same source/main-only notice as every other verb."""
    monkeypatch.setattr(dc, "_dispatch_runtime_source_present", lambda: False)
    rc, out, err, exc = _invoke(["dispatch", verb, "--json"])
    assert exc is None, err
    assert rc != 0
    assert json.loads(out)["error"] == "dispatch_runtime_unavailable"
