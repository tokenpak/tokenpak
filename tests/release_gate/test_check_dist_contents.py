"""Unit tests for the distribution-contents release gate.

Loaded by path (like the other release_gate tests) so the test does not depend
on a particular ``scripts/`` package layout. The focus is the Dispatch
v0.1-alpha package-data assertion (G4): the gate must FAIL when a declared
Dispatch registry/schema glob ships no file in the wheel or sdist.
"""
import importlib.util
from pathlib import Path

import pytest

_MOD_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "check-dist-contents.py"
)
_spec = importlib.util.spec_from_file_location("check_dist_contents", _MOD_PATH)
cdc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdc)


# A representative set of archive member names mirroring what the real build
# ships on staging: generic package data plus at least one file per declared
# Dispatch registry/schema glob.
SHIPPED_NAMES = {
    "tokenpak/__init__.py",
    "tokenpak/budget_config.yaml",
    "tokenpak/term_cards.json",
    "tokenpak/orchestration/dispatch/registry/worker.builder.default.v1.yaml",
    "tokenpak/orchestration/dispatch/registry/worker.reviewer.default.v1.yaml",
    "tokenpak/orchestration/dispatch/registry/routes/route.code_task.v1.yaml",
    "tokenpak/orchestration/dispatch/registry/overlays/overlay.code_builder.v1.yaml",
    "tokenpak/orchestration/dispatch/schemas/DispatchManifest.json",
}


def _names_dropping_glob(glob: str) -> set[str]:
    """SHIPPED_NAMES minus every member that matches ``glob``."""
    return {n for n in SHIPPED_NAMES if not cdc._matches_glob(n, glob)}


# --- _matches_glob: a single-segment '*' must not cross '/' ----------------


def test_matches_glob_matches_direct_child():
    assert cdc._matches_glob(
        "tokenpak/orchestration/dispatch/registry/worker.builder.default.v1.yaml",
        "tokenpak/orchestration/dispatch/registry/*.yaml",
    )


def test_matches_glob_does_not_cross_directory_boundary():
    # A routes-subdirectory file must NOT satisfy the registry/*.yaml glob,
    # otherwise a missing top-level registry file would go undetected.
    assert not cdc._matches_glob(
        "tokenpak/orchestration/dispatch/registry/routes/route.code_task.v1.yaml",
        "tokenpak/orchestration/dispatch/registry/*.yaml",
    )


def test_matches_glob_extension_must_match():
    assert not cdc._matches_glob(
        "tokenpak/orchestration/dispatch/registry/routes.py",
        "tokenpak/orchestration/dispatch/registry/*.yaml",
    )


# --- _assert_required_dispatch_data: pass when every glob ships -------------


def test_dispatch_data_present_passes_for_wheel_and_sdist():
    # Must not raise for either artifact label when all globs are satisfied.
    cdc._assert_required_dispatch_data(SHIPPED_NAMES, "wheel")
    cdc._assert_required_dispatch_data(SHIPPED_NAMES, "sdist")


def test_dispatch_globs_cover_all_declared_pyproject_entries():
    # Guard against the glob list silently shrinking below the four declared
    # Dispatch package-data entries.
    assert set(cdc.REQUIRED_DISPATCH_DATA_GLOBS) == {
        "tokenpak/orchestration/dispatch/registry/*.yaml",
        "tokenpak/orchestration/dispatch/registry/routes/*.yaml",
        "tokenpak/orchestration/dispatch/registry/overlays/*.yaml",
        "tokenpak/orchestration/dispatch/schemas/*.json",
    }


# --- the core G4 assertion: gate fails when a dispatch glob ships nothing ---


@pytest.mark.parametrize("glob", cdc.REQUIRED_DISPATCH_DATA_GLOBS)
def test_missing_dispatch_glob_fails(glob):
    names = _names_dropping_glob(glob)
    with pytest.raises(AssertionError) as excinfo:
        cdc._assert_required_dispatch_data(names, "wheel")
    msg = str(excinfo.value)
    assert "Dispatch package data" in msg
    assert glob in msg


def test_routes_only_does_not_satisfy_registry_glob():
    # registry/*.yaml ships nothing while routes/*.yaml still does -> must fail
    # on the registry glob specifically (slash-precision regression guard).
    names = _names_dropping_glob("tokenpak/orchestration/dispatch/registry/*.yaml")
    assert any("registry/routes/" in n for n in names)
    with pytest.raises(AssertionError) as excinfo:
        cdc._assert_required_dispatch_data(names, "sdist")
    assert "tokenpak/orchestration/dispatch/registry/*.yaml" in str(excinfo.value)


# --- existing generic-data assertion still behaves -------------------------


def test_required_generic_data_still_enforced():
    cdc._assert_required_data(SHIPPED_NAMES, "wheel")
    with pytest.raises(AssertionError):
        cdc._assert_required_data(SHIPPED_NAMES - {"tokenpak/term_cards.json"}, "wheel")
