# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the Dispatch slim-wheel runtime guard (B1 / B2).

Reproduces the v0.1-alpha *slim wheel* failure mode. The published package ships
the Dispatch CLI command file plus registry/schema **data** under
``tokenpak/orchestration/dispatch/`` (which makes that directory a PEP 420
*namespace package*), but it does NOT ship the runtime engine modules. Before the
fix, invoking a runtime verb (``tokenpak dispatch run …``) raised a raw
``ModuleNotFoundError`` traceback, because the absence check probed the namespace
package directory — which resolves non-``None`` even when the runtime is absent.

The fix sentinels on a *real runtime module*
(:data:`~tokenpak.cli.commands.dispatch_cmd._DISPATCH_RUNTIME_SENTINEL`) and
degrades runtime verbs to a concise, actionable "source/main-only" message via the
existing ``_err`` envelope with a nonzero exit code, instead of crashing. The
message also states the truthful ``[dispatch]`` extra contract (B2).

These tests drive the CLI in-process via the argparse builder + handler functions
(faster, and any leaked ``ModuleNotFoundError`` surfaces as a caught exception).
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys

import pytest

pytest.importorskip("pydantic")

import tokenpak.cli.commands.dispatch_cmd as dc  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tokenpak")
    sub = parser.add_subparsers(dest="command")
    dc.build_dispatch_parser(sub)
    return parser


def _invoke(args):
    """Run ``args.func(args)`` capturing stdout/stderr and any raised exception.

    Returns ``(rc, stdout, stderr, exc)``. Pre-fix, the runtime verbs raised
    ``ModuleNotFoundError`` here; the regression asserts ``exc is None``.
    """
    out, err = io.StringIO(), io.StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    exc = None
    rc = None
    try:
        rc = args.func(args)
    except BaseException as e:  # noqa: BLE001 — the point is to catch the old crash
        exc = e
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return rc, out.getvalue(), err.getvalue(), exc


# argv for every runtime-touching verb (no ``--json`` — tests append as needed).
RUNTIME_VERB_ARGV = [
    ["dispatch", "run", "hello"],
    ["dispatch", "status", "job_x"],
    ["dispatch", "inspect", "job_x"],
    ["dispatch", "decisions"],
    ["dispatch", "approve", "decision_x"],
    ["dispatch", "reject", "decision_x"],
    ["dispatch", "pause", "job_x"],
    ["dispatch", "resume", "job_x"],
    ["dispatch", "cancel", "job_x"],
    ["dispatch", "discard-late", "stationrun_x"],
    ["dispatch", "delivery", "job_x"],
    ["dispatch", "receipt", "job_x"],
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point TOKENPAK_HOME at a tmp dir so the CLI never touches ~/.tpk/."""
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Root-cause: the availability check sentinels on a real runtime module
# ---------------------------------------------------------------------------


def test_runtime_available_false_when_only_namespace_pkg(monkeypatch):
    """The namespace-package directory must NOT be mistaken for a runtime.

    Simulates the slim wheel: ``tokenpak.orchestration.dispatch`` resolves (data
    namespace package) but the runtime sentinel module does not. The old guard
    keyed on the former and was defeated; the fix keys on the latter.
    """
    real_find_spec = importlib.util.find_spec
    # Any real spec object stands in for the "present" namespace package.
    ns_spec = real_find_spec("tokenpak.cli")

    def fake_find_spec(name, *a, **k):
        if name == "tokenpak.orchestration.dispatch":
            return ns_spec  # namespace package "present"
        if name == dc._DISPATCH_RUNTIME_SENTINEL:
            return None  # runtime engine genuinely absent
        return real_find_spec(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    # The OLD (buggy) directory probe is truthy under this condition ...
    assert importlib.util.find_spec("tokenpak.orchestration.dispatch") is not None
    # ... but the fixed check reports the runtime as absent.
    assert dc._dispatch_runtime_available() is False


def test_runtime_available_true_in_source_install():
    """The workbench is a source/editable install, so the runtime IS present."""
    assert dc._dispatch_runtime_available() is True


# ---------------------------------------------------------------------------
# B1: runtime verbs degrade gracefully (no traceback) when runtime is absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("argv", RUNTIME_VERB_ARGV, ids=lambda a: a[1])
def test_runtime_verb_degrades_json(argv, monkeypatch):
    monkeypatch.setattr(dc, "_dispatch_runtime_available", lambda: False)
    parser = _parser()
    args = parser.parse_args(argv + ["--json"])
    rc, out, err, exc = _invoke(args)

    assert exc is None, f"{argv} raised {type(exc).__name__}: {exc}"
    assert rc != 0, f"{argv} returned zero exit on absent runtime"
    payload = json.loads(out)
    assert payload["error"] == "dispatch_runtime_unavailable"


def test_runtime_verb_degrades_human_with_actionable_message(monkeypatch):
    monkeypatch.setattr(dc, "_dispatch_runtime_available", lambda: False)
    parser = _parser()
    args = parser.parse_args(["dispatch", "run", "hello"])
    rc, out, err, exc = _invoke(args)

    assert exc is None
    assert rc != 0
    # Actionable, truthful message (B1 wording + B2 extra contract).
    assert "source/main-only" in err
    assert "[dispatch]" in err
    # No raw traceback markers leaked to the user.
    assert "Traceback" not in err and "ModuleNotFoundError" not in err


def test_namespace_pkg_defeat_end_to_end(monkeypatch):
    """End-to-end crux regression via the real ``find_spec`` gate (not a stub).

    Recreates the exact slim-wheel condition and proves a runtime verb returns the
    graceful envelope instead of raising ``ModuleNotFoundError``.
    """
    real_find_spec = importlib.util.find_spec
    ns_spec = real_find_spec("tokenpak.cli")

    def fake_find_spec(name, *a, **k):
        if name == "tokenpak.orchestration.dispatch":
            return ns_spec
        if name == dc._DISPATCH_RUNTIME_SENTINEL:
            return None
        return real_find_spec(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    parser = _parser()
    args = parser.parse_args(["dispatch", "run", "hello", "--json"])
    rc, out, err, exc = _invoke(args)

    assert exc is None, f"runtime verb raised {type(exc).__name__}: {exc}"
    assert rc != 0
    assert json.loads(out)["error"] == "dispatch_runtime_unavailable"


# ---------------------------------------------------------------------------
# Non-regressions: bare group works without runtime; verbs route when present
# ---------------------------------------------------------------------------


def test_bare_dispatch_group_works_without_runtime(monkeypatch):
    """The bare ``dispatch`` group prints help (rc 0) — it needs no runtime."""
    monkeypatch.setattr(dc, "_dispatch_runtime_available", lambda: False)
    parser = _parser()
    args = parser.parse_args(["dispatch"])
    rc, out, err, exc = _invoke(args)

    assert exc is None
    assert rc == 0


def test_verb_routes_normally_when_runtime_present(home):
    """With the runtime present (workbench), the decorator must pass through.

    ``decisions`` on a fresh (empty) ledger routes to the real handler and must
    NOT short-circuit with the unavailable error.
    """
    parser = _parser()
    args = parser.parse_args(["dispatch", "decisions", "--json"])
    rc, out, err, exc = _invoke(args)

    assert exc is None
    payload = json.loads(out)
    assert payload.get("error") != "dispatch_runtime_unavailable"
