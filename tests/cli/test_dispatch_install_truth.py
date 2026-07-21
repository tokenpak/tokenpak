# SPDX-License-Identifier: Apache-2.0
"""Base-install dependency-truth tests for the Dispatch CLI (install-truth residual).

The prior slim-wheel guard covers the *runtime-absent* case: the released wheel
ships the CLI + registry data but not the runtime engine, so a runtime verb
degrades to a "source/main-only" notice.

This module covers the remaining blind spot: the runtime engine IS present (a
source/main install) but the optional ``[dispatch]`` dependencies (``pydantic`` /
``jsonschema``) are NOT installed. Those deps are kept out of the slim core, so a
base install can reach this state.

Before the fix, ``_dispatch_runtime_available()`` — which ``find_spec``s the
sentinel submodule and thereby imports the pydantic-native package ``__init__`` —
raised ``ModuleNotFoundError`` internally, was caught, and reported the runtime as
absent. The verb then told a tester who *already had a source install* to "run
Dispatch from a source/main install": a false path. The fix distinguishes the two
states and points a deps-missing tester at ``pip install 'tokenpak[dispatch]'``.
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tokenpak")
    sub = parser.add_subparsers(dest="command")
    dc.build_dispatch_parser(sub)
    return parser


def _invoke(args):
    out, err = io.StringIO(), io.StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    exc = None
    rc = None
    try:
        rc = args.func(args)
    except BaseException as e:  # noqa: BLE001 — the point is to catch a raw crash
        exc = e
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return rc, out.getvalue(), err.getvalue(), exc


# Every runtime/deps-gated verb (includes the new discovery verbs).
GATED_VERB_ARGV = [
    ["dispatch", "run", "hello"],
    ["dispatch", "status", "job_x"],
    ["dispatch", "inspect", "job_x"],
    ["dispatch", "decisions"],
    ["dispatch", "routes"],
    ["dispatch", "workers"],
]


# ---------------------------------------------------------------------------
# Crux: the deps gap is detected WITHOUT importing pydantic, and is
# distinguishable from a genuinely-absent runtime.
# ---------------------------------------------------------------------------


def test_missing_deps_detected_side_effect_free(monkeypatch):
    """``_missing_dispatch_deps`` reports pydantic/jsonschema absent via find_spec.

    Simulated by stubbing ``find_spec`` for those top-level names — proving the
    probe never imports them (importing pydantic would defeat the whole point of a
    side-effect-free check).
    """
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *a, **k):
        if name in ("pydantic", "jsonschema"):
            return None
        return real_find_spec(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    assert dc._missing_dispatch_deps() == ["pydantic", "jsonschema"]


def test_runtime_source_present_without_importing_deps(monkeypatch):
    """The runtime file is detected on disk even when the deps can't be imported.

    This is the disambiguation crux: 'runtime present but deps missing' must NOT
    collapse to 'runtime absent'. The source-presence probe locates the runtime
    module file via the (pydantic-free) ``tokenpak.orchestration`` package path.
    """
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *a, **k):
        if name in ("pydantic", "jsonschema"):
            return None
        return real_find_spec(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    # Workbench is a source install: the runtime file ships here.
    assert dc._dispatch_runtime_source_present() is True


# ---------------------------------------------------------------------------
# Behavior: verbs report the deps gap (not the false source-only path).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("argv", GATED_VERB_ARGV, ids=lambda a: a[1])
def test_verb_reports_deps_gap_json(argv, monkeypatch):
    monkeypatch.setattr(dc, "_dispatch_runtime_source_present", lambda: True)
    monkeypatch.setattr(dc, "_missing_dispatch_deps", lambda: ["pydantic", "jsonschema"])
    parser = _parser()
    args = parser.parse_args(argv + ["--json"])
    rc, out, err, exc = _invoke(args)

    assert exc is None, f"{argv} raised {type(exc).__name__}: {exc}"
    assert rc != 0, f"{argv} returned zero exit on missing deps"
    assert json.loads(out)["error"] == "dispatch_dependencies_missing"


def test_deps_gap_message_is_actionable_and_not_false_source_path(monkeypatch):
    monkeypatch.setattr(dc, "_dispatch_runtime_source_present", lambda: True)
    monkeypatch.setattr(dc, "_missing_dispatch_deps", lambda: ["pydantic", "jsonschema"])
    parser = _parser()
    args = parser.parse_args(["dispatch", "run", "hello"])
    rc, out, err, exc = _invoke(args)

    assert exc is None
    assert rc != 0
    # Truthful remedy: install the extra.
    assert "pip install" in err and "tokenpak[dispatch]" in err
    assert "pydantic" in err and "jsonschema" in err
    # Must NOT point a source-install tester at the false "use a source install"
    # remedy — that wording belongs to the genuinely runtime-absent case.
    assert "source/main-only" not in err
    # No raw traceback leaks.
    assert "Traceback" not in err and "ModuleNotFoundError" not in err


def test_deps_gap_end_to_end_via_real_gate(monkeypatch):
    """End-to-end through the real gate (only ``find_spec`` stubbed for deps).

    Proves the un-stubbed gate classifies a deps-missing source install as
    ``dispatch_dependencies_missing`` — not ``dispatch_runtime_unavailable``.
    """
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *a, **k):
        if name in ("pydantic", "jsonschema"):
            return None
        return real_find_spec(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    parser = _parser()
    args = parser.parse_args(["dispatch", "run", "hello", "--json"])
    rc, out, err, exc = _invoke(args)

    assert exc is None, f"runtime verb raised {type(exc).__name__}: {exc}"
    assert rc != 0
    assert json.loads(out)["error"] == "dispatch_dependencies_missing"


# ---------------------------------------------------------------------------
# The two states stay distinct: genuinely-absent runtime keeps its own message.
# ---------------------------------------------------------------------------


def test_runtime_absent_still_reports_source_only(monkeypatch):
    """When the runtime file is absent (slim wheel), the source-only notice wins.

    The deps gate must not swallow the runtime-absent case: source-present is
    checked FIRST, so a genuinely-absent runtime still yields the runtime-absent
    message even if deps also happen to be missing.
    """
    monkeypatch.setattr(dc, "_dispatch_runtime_source_present", lambda: False)
    monkeypatch.setattr(dc, "_missing_dispatch_deps", lambda: ["pydantic"])
    parser = _parser()
    args = parser.parse_args(["dispatch", "run", "hello", "--json"])
    rc, out, err, exc = _invoke(args)

    assert exc is None
    assert rc != 0
    assert json.loads(out)["error"] == "dispatch_runtime_unavailable"
