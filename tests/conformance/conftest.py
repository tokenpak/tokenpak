"""Shared fixtures for the TIP self-conformance suite (SC-06).

Three responsibilities:

1. ``conformance_observer`` — install/uninstall the ConformanceObserver
   around each test; captured events are returned as a dict keyed by
   kind.
2. ``scrub_cc_env_if_scenario_requires`` — read ``test_env_preconditions``
   from a scenario fixture and unset the listed env vars for the
   duration of a test. Scoped per scenario; the surrounding shell's
   env is restored on teardown.
3. ``companion_tmp_home`` — temp HOME directory for companion Layer-B
   tests so ``~/.tokenpak/companion/capsules/`` + ``journal.db`` reads
   hit an isolated tree.

All fixtures restore state on teardown even if the test fails.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping

import pytest


def _discover_registry_root() -> "Path | None":
    """Locate a usable schema root for TIP-1.0 validation.

    Resolution order:
    1. ``TOKENPAK_REGISTRY_ROOT`` env var (operator override).
    2. Sibling ``../registry`` relative to the tokenpak repo root —
       the layout the SC-08 GitHub Actions workflow checks out.
    3. ``$HOME/registry`` — common dev-box layout.
    4. Vendored ``tokenpak/_tip_schemas/`` shipped in the wheel
       (SC-07). Makes the conformance suite runnable standalone in
       any installed environment; the installed validator reads
       schemas from this path when the higher-priority roots are
       absent.
    5. None — pytest proceeds and any test that depends on
       registry-only schemas degrades per SC-07's schema-unavailable
       WARN convention (module/test-level skipif gates in
       test_layer_b_companion.py + test_layer_c_smoke.py honor it).

    The chosen root must contain ``schemas/tip/capabilities.schema.json``
    to be considered valid.
    """
    candidates: list[Path] = []
    env_root = os.environ.get("TOKENPAK_REGISTRY_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    tokenpak_repo_root = Path(__file__).resolve().parents[2]
    candidates.append(tokenpak_repo_root.parent / "registry")
    candidates.append(Path.home() / "registry")
    # SC-07 vendored tree — always last so registry checkouts win
    # (the registry has the latest _SCHEMA_PATHS map; the vendored
    # tree only has schemas, not validator source).
    try:
        from importlib.resources import files as _files

        vendored = _files("tokenpak").joinpath("_tip_schemas")
        vendored_path = Path(str(vendored))
        candidates.append(vendored_path)
    except Exception:
        pass
    for root in candidates:
        if (root / "schemas" / "tip" / "capabilities.schema.json").is_file():
            return root
    return None


def installed_validator_knows_schema(schema_name: str) -> bool:
    """Return True if the installed ``tokenpak_tip_validator`` knows the schema.

    Guards Layer-B + Layer-C assertions that validate against schemas
    added in SC-01 but not yet included in the pinned PyPI validator
    (``tokenpak-tip-validator==0.1.0`` predates ``companion-journal-row``).
    Running against a registry-editable install (SC-08 CI path) sees
    the full _SCHEMA_PATHS map and this returns True for every schema.

    Tests use this via ``pytest.mark.skipif(not
    installed_validator_knows_schema('...'))`` so standalone dev-env
    runs against a plain pip install gracefully skip the affected
    assertions instead of failing — mirrors the SC-07 runner's WARN
    convention on the pytest side.
    """
    try:
        from tokenpak_tip_validator.schema import _SCHEMA_PATHS
        return schema_name in _SCHEMA_PATHS
    except Exception:
        return False


def pytest_configure(config):
    # Make the registry validator importable for round-trip schema
    # validation. The validator is pinned in pyproject.toml [dev]
    # extras; installing from the registry source gets the latest
    # _SCHEMA_PATHS map (including SC-01 companion-journal-row).
    root = _discover_registry_root()
    if root is not None:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        os.environ.setdefault("TOKENPAK_REGISTRY_ROOT", str(root))


_CC_ENV_VARS = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
)


@pytest.fixture
def conformance_observer() -> Iterator[Dict[str, list]]:
    """Install a capturing ConformanceObserver for the duration of the test.

    Yields a dict with four keys — ``telemetry``, ``headers``,
    ``journal``, ``capabilities`` — each a list of captured events.

    Observer is uninstalled in teardown whether or not the test
    passed, and any stale observer is restored.
    """
    from tokenpak.services.diagnostics import conformance as _conformance

    captured: Dict[str, list] = {
        "telemetry": [],
        "headers": [],
        "journal": [],
        "capabilities": [],
    }

    class _Obs:
        def on_telemetry_row(self, row: Mapping[str, Any]) -> None:
            captured["telemetry"].append(dict(row))

        def on_response_headers(
            self, headers: Mapping[str, str], direction: str
        ) -> None:
            captured["headers"].append((direction, dict(headers)))

        def on_companion_journal_row(self, row: Mapping[str, Any]) -> None:
            captured["journal"].append(dict(row))

        def on_capability_published(
            self, profile: str, caps: Any
        ) -> None:
            captured["capabilities"].append((profile, list(caps)))

    uninstall = _conformance.install(_Obs())
    try:
        yield captured
    finally:
        uninstall()


@pytest.fixture
def scrub_cc_env() -> Iterator[None]:
    """Unset CLAUDECODE + CLAUDE_CODE_* env vars for a test.

    Scoped: only the listed vars are unset; the surrounding shell's
    other env is untouched. Restored in teardown.

    Used by scenarios that declare ``test_env_preconditions.must_unset``
    including any of _CC_ENV_VARS. Tests apply via fixture request or
    the ``apply_scenario_env`` helper below.
    """
    saved = {k: os.environ.pop(k, None) for k in _CC_ENV_VARS}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def apply_scenario_env(scenario: Mapping[str, Any]) -> Dict[str, "str | None"]:
    """Honor a scenario's ``test_env_preconditions.must_unset``.

    Unsets every env var listed; returns a saved-env dict callers
    pass back to :func:`restore_scenario_env` in teardown. Safe to
    invoke even when the scenario declares no preconditions — no-op.

    Kept as a plain function (not a fixture) so both Layer A (proxy
    scenarios) and Layer B (companion scenarios) can share the logic
    without pytest fixture composition gymnastics.
    """
    preconds = scenario.get("test_env_preconditions") or {}
    to_unset = list(preconds.get("must_unset") or [])
    saved: Dict[str, "str | None"] = {}
    for k in to_unset:
        saved[k] = os.environ.pop(k, None)
    return saved


def restore_scenario_env(saved: Mapping[str, "str | None"]) -> None:
    """Restore env vars captured by :func:`apply_scenario_env`."""
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def apply_scenario_env_override(env: Mapping[str, str]) -> Dict[str, "str | None"]:
    """Apply a scenario's ``env`` block (companion fixtures).

    Sets every listed var to its value and returns prior values for
    restoration.
    """
    saved: Dict[str, "str | None"] = {}
    for k, v in (env or {}).items():
        saved[k] = os.environ.get(k)
        os.environ[k] = str(v)
    return saved


@pytest.fixture
def companion_tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME at an isolated temp dir for companion Layer-B tests.

    The companion module computes ``~/.tokenpak/companion/`` lazily
    from ``Path.home()`` at call time via the module-level
    ``_COMPANION_DIR`` constant, so we have to redirect it directly
    after setting HOME. Both steps are done here.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    companion_dir = tmp_path / ".tokenpak" / "companion"
    companion_dir.mkdir(parents=True, exist_ok=True)
    (companion_dir / "capsules").mkdir(parents=True, exist_ok=True)

    # Repoint module-level constants in pre_send so _load_active_capsule
    # and _journal_write_savings hit our tmp tree.
    from tokenpak.companion.hooks import pre_send as _ps

    monkeypatch.setattr(_ps, "_COMPANION_DIR", companion_dir, raising=False)
    monkeypatch.setattr(
        _ps,
        "_JOURNAL_DB",
        companion_dir / "journal.db",
        raising=False,
    )
    return tmp_path


# ---- Fixture-data loaders ----------------------------------------------------


_TESTS_ROOT = Path(__file__).resolve().parent


def load_proxy_scenario(name: str) -> Dict[str, Any]:
    path = _TESTS_ROOT / "scenarios" / name
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_companion_scenarios() -> Dict[str, Any]:
    path = _TESTS_ROOT / "companion" / "scenarios.json"
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def proxy_scenario_names() -> list[str]:
    return sorted(p.name for p in (_TESTS_ROOT / "scenarios").glob("*.json"))
