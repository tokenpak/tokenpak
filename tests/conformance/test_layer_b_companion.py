"""Layer B — companion pre_send hook + journal observer validation.

For each companion scenario in scenarios.json:
- set up tmp HOME with the optional capsule fixture
- apply the scenario's env overrides
- invoke tokenpak.companion.hooks.pre_send.run(payload)
- assert captured journal rows match expected_observer_events and
  that none match expected_absent_events
- schema-validate every captured companion_journal_row against the
  registry schema
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from tokenpak_tip_validator import validate_against

from tokenpak.companion.hooks import pre_send

from .conftest import (
    apply_scenario_env_override,
    installed_validator_knows_schema,
    load_companion_scenarios,
    restore_scenario_env,
)


# Layer B validates captured rows against ``companion-journal-row``
# (schema added in SC-01). The pinned PyPI validator 0.1.0 predates
# that schema, so a standalone dev run against a plain pip install
# skips this module gracefully. The SC-08 CI path installs the
# validator editable from a registry checkout, which always carries
# the full _SCHEMA_PATHS map — there the skip never fires.
pytestmark = [
    pytest.mark.conformance,
    pytest.mark.skipif(
        not installed_validator_knows_schema("companion-journal-row"),
        reason=(
            "installed tokenpak-tip-validator predates companion-journal-row "
            "schema (SC-01). Install validator from a registry checkout to "
            "run: pip install -e ./registry. See "
            "tokenpak/_tip_schemas/README.md sync checklist."
        ),
    ),
]


_FIXTURES_DIR = Path(__file__).resolve().parent / "companion"


def _companion_scenario_ids() -> list[str]:
    return list(load_companion_scenarios()["scenarios"].keys())


def _event_matches(event: Mapping[str, Any], pattern: Mapping[str, Any]) -> bool:
    """Does an observer event satisfy a matcher pattern from the fixture?

    Pattern keys:
    - ``kind``                — required; 'companion_journal_row' etc.
                                 (We treat every captured journal row as
                                 kind='companion_journal_row').
    - ``entry_type``          — exact match on row.entry_type.
    - ``source_startswith``   — prefix match on row.source.
    """
    if pattern["kind"] != "companion_journal_row":
        return False
    if "entry_type" in pattern and event.get("entry_type") != pattern["entry_type"]:
        return False
    if "source_startswith" in pattern:
        src = event.get("source") or ""
        if not src.startswith(pattern["source_startswith"]):
            return False
    return True


@pytest.mark.parametrize("scenario_id", _companion_scenario_ids())
def test_companion_scenario_events(
    scenario_id, conformance_observer, companion_tmp_home
):
    scenarios = load_companion_scenarios()["scenarios"]
    sc = scenarios[scenario_id]

    # Stage the capsule (if this scenario has one) under the tmp HOME.
    if sc.get("capsule_file"):
        src = _FIXTURES_DIR / sc["capsule_file"]
        dst = companion_tmp_home / ".tokenpak" / "companion" / "capsules" / "active.md"
        dst.write_text(src.read_text())

    # Load the prompt payload.
    prompt_path = _FIXTURES_DIR / sc["payload"]["prompt_file"]
    prompt_text = prompt_path.read_text()
    payload = {
        "session_id": sc["payload"]["session_id"],
        "prompt": prompt_text,
        "transcript_path": sc["payload"].get("transcript_path") or "",
    }

    saved = apply_scenario_env_override(sc.get("env", {}))
    try:
        # Drive the pre_send hook. It must not raise; exit code 0/2 is
        # acceptable (0 = normal completion, 2 = budget block — not
        # exercised by these scenarios but documented for completeness).
        exit_code = pre_send.run(payload)
        assert exit_code in (0, 2), f"pre_send exit={exit_code} for {scenario_id}"
    finally:
        restore_scenario_env(saved)

    captured = conformance_observer["journal"]

    # Every captured row must be schema-valid.
    for row in captured:
        res = validate_against("companion-journal-row", row)
        assert res.ok, (
            f"{scenario_id}: row failed companion-journal-row schema: "
            f"{[(f.code, f.message) for f in res.errors()]} — row={row}"
        )

    # Every expected event has at least one match in the captured set.
    for pattern in sc.get("expected_observer_events", []):
        hits = [e for e in captured if _event_matches(e, pattern)]
        assert hits, (
            f"{scenario_id}: expected event {pattern!r} not captured; "
            f"got {[(e.get('entry_type'), e.get('source')) for e in captured]}"
        )

    # Every absent-event pattern must have zero matches.
    for pattern in sc.get("expected_absent_events", []):
        hits = [e for e in captured if _event_matches(e, pattern)]
        assert not hits, (
            f"{scenario_id}: event {pattern!r} was NOT expected but was "
            f"captured: {hits}"
        )


def test_companion_disabled_emits_zero_events(
    conformance_observer, companion_tmp_home
):
    """Explicit coverage for the early-return path.

    TOKENPAK_COMPANION_ENABLED=0 short-circuits pre_send.run before any
    journal write. Any observer event here is a regression.
    """
    scenarios = load_companion_scenarios()["scenarios"]
    sc = scenarios["companion_disabled"]
    prompt_path = _FIXTURES_DIR / sc["payload"]["prompt_file"]
    payload = {
        "session_id": sc["payload"]["session_id"],
        "prompt": prompt_path.read_text(),
    }

    saved = apply_scenario_env_override(sc.get("env", {}))
    try:
        pre_send.run(payload)
    finally:
        restore_scenario_env(saved)

    assert conformance_observer["journal"] == []
