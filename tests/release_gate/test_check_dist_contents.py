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
    "tokenpak/companion/GUIDE.md",
    "tokenpak/companion/hooks/pre_send.sh",
    "tokenpak/companion/hooks/session_start_name.sh",
    "tokenpak/companion/codex/hooks_session_start.sh",
    "tokenpak/companion/codex/hooks_pre_send.sh",
    "tokenpak/companion/codex/hooks_pre_tool_use.sh",
    "tokenpak/companion/codex/hooks_post_tool_use.sh",
    "tokenpak/companion/codex/hooks_stop.sh",
    "tokenpak/companion/codex/skills/example-skill/SKILL.md",
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


# --- companion runtime data: hook scripts + skills must ship ----------------


def test_matches_glob_supports_wildcard_directory_segment():
    assert cdc._matches_glob(
        "tokenpak/companion/codex/skills/example-skill/SKILL.md",
        "tokenpak/companion/codex/skills/*/SKILL.md",
    )
    # The wildcard segment is exactly one directory level.
    assert not cdc._matches_glob(
        "tokenpak/companion/codex/skills/nested/deeper/SKILL.md",
        "tokenpak/companion/codex/skills/*/SKILL.md",
    )


def test_companion_data_present_passes_for_wheel_and_sdist():
    cdc._assert_required_companion_data(SHIPPED_NAMES, "wheel")
    cdc._assert_required_companion_data(SHIPPED_NAMES, "sdist")


@pytest.mark.parametrize(
    "hook_script",
    [
        "tokenpak/companion/codex/hooks_session_start.sh",
        "tokenpak/companion/codex/hooks_pre_send.sh",
        "tokenpak/companion/codex/hooks_pre_tool_use.sh",
        "tokenpak/companion/codex/hooks_post_tool_use.sh",
        "tokenpak/companion/codex/hooks_stop.sh",
    ],
)
def test_missing_codex_hook_script_fails(hook_script):
    # Each of the five Codex hook entrypoints is individually required: a
    # wheel missing any one of them makes that hook exit 127 on clean
    # installs, so the gate must name the exact missing file.
    with pytest.raises(AssertionError) as excinfo:
        cdc._assert_required_companion_data(SHIPPED_NAMES - {hook_script}, "wheel")
    assert hook_script in str(excinfo.value)


@pytest.mark.parametrize("glob", cdc.REQUIRED_COMPANION_DATA_GLOBS)
def test_missing_companion_glob_fails(glob):
    # Dropping every file a declared companion glob matches must fail the
    # gate. For the codex/*.sh glob the exact-file check reports the missing
    # hook scripts by name; for the others the glob-liveness check names the
    # dead glob.
    names = {n for n in SHIPPED_NAMES if not cdc._matches_glob(n, glob)}
    with pytest.raises(AssertionError) as excinfo:
        cdc._assert_required_companion_data(names, "sdist")
    msg = str(excinfo.value)
    assert glob in msg or "companion runtime files" in msg


def test_companion_required_files_cover_all_five_hooks():
    hooks = {
        n
        for n in cdc.REQUIRED_COMPANION_FILES
        if n.startswith("tokenpak/companion/codex/hooks_")
    }
    assert hooks == {
        "tokenpak/companion/codex/hooks_session_start.sh",
        "tokenpak/companion/codex/hooks_pre_send.sh",
        "tokenpak/companion/codex/hooks_pre_tool_use.sh",
        "tokenpak/companion/codex/hooks_post_tool_use.sh",
        "tokenpak/companion/codex/hooks_stop.sh",
    }


# --- existing generic-data assertion still behaves -------------------------


def test_required_generic_data_still_enforced():
    cdc._assert_required_data(SHIPPED_NAMES, "wheel")
    with pytest.raises(AssertionError):
        cdc._assert_required_data(SHIPPED_NAMES - {"tokenpak/term_cards.json"}, "wheel")
