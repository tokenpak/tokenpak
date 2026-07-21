"""Static fail-closed wiring checks for the release-check Make targets."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
CORE_MARKERS = "not integration and not chaos and not slow and not needs_fast_host"


def _target_prerequisites(name: str) -> set[str]:
    text = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}:\s+([^#\n]+)", text, re.MULTILINE)
    assert match, f"target {name!r} is missing or has no prerequisites"
    return set(match.group(1).split())


def _target_block(name: str) -> str:
    text = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(
        rf"^{re.escape(name)}:.*?(?=^[A-Za-z0-9_.-]+:|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"target {name!r} is missing"
    return match.group(0)


def test_audit_maps_every_std09_automated_component():
    assert _target_prerequisites("audit") == {
        "ci-lint",
        "audit-mypy",
        "docs-check",
        "forbidden-phrases-check",
        "telemetry-audit",
    }


def test_release_check_maps_every_local_umbrella_gate():
    assert _target_prerequisites("release-check") == {
        "release-check-baseline",
        "test",
        "test-quick",
        "lint-imports",
        "fresh-install-demo",
        "bench",
        "byte-fidelity-check",
        "audit",
        "release-docs-pattern-check",
    }


def test_release_check_does_not_activate_deferred_formatter_ratchet():
    assert "check" not in _target_prerequisites("release-check")
    assert "format-check" not in _target_prerequisites("release-check")


def test_release_core_partition_exactly_matches_blocking_ci():
    text = MAKEFILE.read_text(encoding="utf-8")
    assert f"RELEASE_CORE_MARKERS := {CORE_MARKERS}" in text
    block = _target_block("test-release-core")
    assert '$(PYTEST) tests/ -m "$(RELEASE_CORE_MARKERS)" -q --tb=short' in block
    assert "does not satisfy complete A1" in block


def test_a3_python_defaults_to_the_release_environment_but_can_be_pinned_separately():
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "A3_PYTHON ?= $(VENV_BIN)/python3" in text
    assert "$(A3_PYTHON) scripts/release_audit.py mypy" in _target_block("audit-mypy")


def test_complete_suite_is_a_fail_closed_umbrella_prerequisite():
    text = MAKEFILE.read_text(encoding="utf-8")
    test_block = _target_block("test")
    release_block = _target_block("release-check")
    assert "$(PYTEST) tests/ -q --tb=short" in test_block
    assert "test" in _target_prerequisites("release-check")
    assert "A1-A7" in release_block


def test_composites_do_not_mask_prerequisite_failure():
    for target in ("audit", "release-check"):
        block = _target_block(target)
        assert "|| true" not in block
        assert "continue-on-error" not in block
        assert not any(line.startswith("\t-") for line in block.splitlines())
