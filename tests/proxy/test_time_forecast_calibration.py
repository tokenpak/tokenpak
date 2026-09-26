# SPDX-License-Identifier: Apache-2.0
"""Time-remaining-band engine unit tests: honest states, gate compliance, fail-open.

``build_calibrated_time_forecast`` is a pure lookup against
``_PUBLISHED_TIME_PRIOR`` — it never fits a model live (see the module
docstring). These tests exercise its branching by calling it (and its one
caller, ``session_forecast._time_calibrated_or_fallback``) with a real,
explicit ``published_prior`` argument — the minimal dependency-injection seam
both functions expose for exactly this purpose — rather than reaching into
``time_forecast_calibration`` module state via ``monkeypatch.setattr`` (the
#633/#634 real-path testing standard). ``monkeypatch.setenv``/``delenv`` are
still used where they exercise a real, documented external interface (an env
var, a config file on a real, isolated ``TOKENPAK_HOME``) rather than an
internal implementation detail.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tokenpak.core.contracts.session_economics import (
    DriftState,
    TimeForecastStatus,
    TimeForecastStreamMode,
)
from tokenpak.proxy import time_forecast_calibration as tf_cal

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _evidence(
    *,
    full_confidence: bool,
    history_n: int = 40,
    observed_coverage_50: float = 52.0,
    observed_coverage_90: float = 92.0,
    drift_state: DriftState = DriftState.STABLE,
    band50: dict[int, tuple[float, float]] | None = None,
    band90: dict[int, float] | None = None,
) -> tf_cal.PublishedTimeCellEvidence:
    return tf_cal.PublishedTimeCellEvidence(
        prior_version="time-prior-2026-09-01",
        band50_y_by_k=band50 if band50 is not None else {1: (-0.2, 0.6), 4: (-0.1, 0.9)},
        band90_hi_y_by_k=band90 if band90 is not None else {1: 1.2, 4: 1.8},
        coverage_method="walk-forward-split-conformal",
        observed_coverage_50=observed_coverage_50,
        observed_coverage_90=observed_coverage_90,
        history_n=history_n,
        drift_state=drift_state,
        full_confidence=full_confidence,
    )


def _build(
    *,
    turn_index: int = 4,
    elapsed_ms: float = 60_000.0,
    model: str = "model-a",
    effort: str = "high",
    stream_mode: TimeForecastStreamMode = TimeForecastStreamMode.STREAMING,
    session_id: str = "active",
    published_prior: dict[tuple[str, str, str], tf_cal.PublishedTimeCellEvidence] | None = None,
):
    return tf_cal.build_calibrated_time_forecast(
        monitor_db_path="ignored",
        now=NOW,
        session_id=session_id,
        model=model,
        effort=effort,
        stream_mode=stream_mode,
        turn_index=turn_index,
        elapsed_ms=elapsed_ms,
        published_prior=published_prior,
    )


# ---------------------------------------------------------------------------
# The gate mechanism itself: exactly one reviewed cell is what ships
# ---------------------------------------------------------------------------


def test_shipped_published_prior_table_has_exactly_the_reviewed_cell() -> None:
    """The literal shipped state: exactly one cell has cleared review so far."""
    assert set(tf_cal._PUBLISHED_TIME_PRIOR) == {("claude-sonnet-5", "unknown", "streaming")}


def test_cold_cell_is_insufficient_data_not_unavailable() -> None:
    """No injected prior, and a cell key the shipped table doesn't carry —
    exercises the real module table's default (still-honest-unknown) path."""
    forecast = _build(model="model-a", effort="high", stream_mode=TimeForecastStreamMode.STREAMING)
    assert forecast.status is TimeForecastStatus.INSUFFICIENT_DATA
    assert forecast.remaining_time_likely_50_ms is None
    assert forecast.remaining_time_ceiling_90_ms is None


# ---------------------------------------------------------------------------
# Real shipped table — the one reviewed cell, exercised without injection
# ---------------------------------------------------------------------------


def test_shipped_sonnet_cell_is_available_against_the_real_table() -> None:
    """No injected ``published_prior`` — this reads the real module-level
    ``_PUBLISHED_TIME_PRIOR``, proving the one reviewed cell actually serves
    a band, not just that a test double would."""
    forecast = _build(
        model="claude-sonnet-5",
        effort="unknown",
        stream_mode=TimeForecastStreamMode.STREAMING,
    )
    assert forecast.status is TimeForecastStatus.AVAILABLE
    assert forecast.remaining_time_likely_50_ms is not None
    assert forecast.remaining_time_ceiling_90_ms is not None
    assert forecast.coverage.method == "walk-forward-split-conformal"
    assert forecast.coverage.history_n == 43
    assert forecast.coverage.drift_state is DriftState.STABLE
    # observed_coverage_50=64.90872210953347 clamped/scaled to a fraction.
    assert forecast.coverage.observed == pytest.approx(0.6491, abs=1e-4)


def test_shipped_sonnet_entry_coverage_matches_reviewed_evidence_exactly() -> None:
    """Lineage guard: the published entry's per-band coverage figures must
    trace character-for-character to the walk-forward re-verified
    remeasurement recorded for this cell (``coverage_50_pct:
    64.90872210953347``, ``coverage_90_pct: 96.14604462474645``,
    ``history_n: 43``, ``drift_state: stable``). Neither figure may drift,
    and ``drift_state`` must never stand in for a coverage number — each
    field is asserted independently here."""
    evidence = tf_cal._PUBLISHED_TIME_PRIOR[("claude-sonnet-5", "unknown", "streaming")]
    assert evidence.observed_coverage_50 == 64.90872210953347
    assert evidence.observed_coverage_90 == 96.14604462474645
    assert evidence.history_n == 43
    assert evidence.drift_state is DriftState.STABLE
    assert evidence.coverage_method == "walk-forward-split-conformal"


@pytest.mark.parametrize(
    "model,effort,stream_mode",
    [
        ("claude-sonnet-5", "unknown", TimeForecastStreamMode.NON_STREAMING),
        ("claude-sonnet-5", "high", TimeForecastStreamMode.STREAMING),
        ("claude-fable-5", "unknown", TimeForecastStreamMode.STREAMING),
        ("model-a", "high", TimeForecastStreamMode.STREAMING),
    ],
)
def test_every_other_real_cell_stays_honest_unknown(
    model: str, effort: str, stream_mode: TimeForecastStreamMode
) -> None:
    """The reviewed cell's presence must not leak into any neighboring key —
    still exercising the real module table, not an injected stand-in."""
    forecast = _build(model=model, effort=effort, stream_mode=stream_mode)
    assert forecast.status is TimeForecastStatus.INSUFFICIENT_DATA


# ---------------------------------------------------------------------------
# Never-a-point-estimate / honest-null branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("turn_index,elapsed_ms", [(0, 60_000.0), (1, 0.0), (-1, 60_000.0)])
def test_no_elapsed_signal_yet_is_unknown(turn_index: int, elapsed_ms: float) -> None:
    key = tf_cal._time_cell_key("model-a", "high", TimeForecastStreamMode.STREAMING)
    forecast = _build(
        turn_index=turn_index,
        elapsed_ms=elapsed_ms,
        published_prior={key: _evidence(full_confidence=True)},
    )
    assert forecast.status is TimeForecastStatus.UNKNOWN
    assert forecast.remaining_time_likely_50_ms is None
    assert forecast.remaining_time_ceiling_90_ms is None


def test_published_cell_with_no_bucket_coverage_is_insufficient_data() -> None:
    key = tf_cal._time_cell_key("model-a", "high", TimeForecastStreamMode.STREAMING)
    forecast = _build(published_prior={key: _evidence(full_confidence=True, band50={}, band90={})})
    assert forecast.status is TimeForecastStatus.INSUFFICIENT_DATA


def test_different_cell_key_is_not_matched() -> None:
    """A published cell for a different (model, effort, stream_mode) never leaks in."""
    other_key = tf_cal._time_cell_key("model-b", "low", TimeForecastStreamMode.NON_STREAMING)
    forecast = _build(
        model="model-a",
        effort="high",
        stream_mode=TimeForecastStreamMode.STREAMING,
        published_prior={other_key: _evidence(full_confidence=True)},
    )
    assert forecast.status is TimeForecastStatus.INSUFFICIENT_DATA


# ---------------------------------------------------------------------------
# learning / available — both populate a band; only the gate differs
# ---------------------------------------------------------------------------


def test_learning_cell_populates_a_borrowed_band() -> None:
    key = tf_cal._time_cell_key("model-a", "high", TimeForecastStreamMode.STREAMING)
    forecast = _build(published_prior={key: _evidence(full_confidence=False, history_n=12)})
    assert forecast.status is TimeForecastStatus.LEARNING
    band = forecast.remaining_time_likely_50_ms
    ceiling = forecast.remaining_time_ceiling_90_ms
    assert band is not None and ceiling is not None
    assert band.low is not None and band.high is not None
    assert 0 <= band.low <= band.high <= ceiling.value
    # The gate receipts are all true the moment ANY evidence exists for the
    # cell — "learning" is still real, reviewed evidence, just not yet at
    # the full-confidence threshold.
    assert forecast.gate.sedr_014_landed
    assert forecast.gate.calibration_evidence_published
    assert forecast.gate.inputs_verified_timing_facts_only
    assert forecast.coverage.history_n == 12


def test_available_cell_is_fully_gated() -> None:
    key = tf_cal._time_cell_key("model-a", "high", TimeForecastStreamMode.STREAMING)
    forecast = _build(published_prior={key: _evidence(full_confidence=True, history_n=48)})
    assert forecast.status is TimeForecastStatus.AVAILABLE
    assert forecast.gate.sedr_014_landed
    assert forecast.gate.calibration_evidence_published
    assert forecast.gate.inputs_verified_timing_facts_only
    assert forecast.coverage.method == "walk-forward-split-conformal"
    assert forecast.coverage.observed is not None
    assert forecast.coverage.history_n == 48
    assert forecast.basis == "timing-facts-v1"


def test_available_never_serializes_as_a_bare_point_estimate() -> None:
    """The contract's own invariant, re-asserted at the engine boundary."""
    key = tf_cal._time_cell_key("model-a", "high", TimeForecastStreamMode.STREAMING)
    payload = _build(published_prior={key: _evidence(full_confidence=True)}).to_dict()
    assert isinstance(payload["remaining_time_likely_50_ms"], dict)
    assert payload["remaining_time_likely_50_ms"]["low"] is not None
    assert payload["remaining_time_likely_50_ms"]["high"] is not None
    assert isinstance(payload["remaining_time_ceiling_90_ms"], dict)
    assert payload["remaining_time_ceiling_90_ms"]["value"] is not None


def test_observed_coverage_is_clamped_and_scaled_to_a_fraction() -> None:
    key = tf_cal._time_cell_key("model-a", "high", TimeForecastStreamMode.STREAMING)
    forecast = _build(
        published_prior={key: _evidence(full_confidence=True, observed_coverage_50=123.4)}
    )
    # A >100% measured figure (shouldn't happen, but defends the contract's
    # own <=1 invariant on Coverage.observed) is clamped before scaling.
    assert forecast.coverage.observed == 1.0


def test_nearest_bucket_is_used_when_turn_index_falls_between_published_keys() -> None:
    key = tf_cal._time_cell_key("model-a", "high", TimeForecastStreamMode.STREAMING)
    # turn_index=2 is nearer bucket 1 than bucket 10.
    forecast = _build(
        turn_index=2,
        elapsed_ms=60_000.0,
        published_prior={
            key: _evidence(
                full_confidence=True,
                band50={1: (-0.2, 0.6), 10: (-0.05, 0.3)},
                band90={1: 1.2, 10: 0.5},
            )
        },
    )
    assert forecast.status is TimeForecastStatus.AVAILABLE
    # Bucket 1's wider band should dominate, not bucket 10's tighter one.
    assert forecast.remaining_time_ceiling_90_ms.value > 60_000.0


def test_deep_turn_index_beyond_kmax_shares_the_last_bucket() -> None:
    key = tf_cal._time_cell_key("model-a", "high", TimeForecastStreamMode.STREAMING)
    forecast = _build(
        turn_index=tf_cal.KMAX + 50,
        published_prior={key: _evidence(full_confidence=True)},
    )
    assert forecast.status is TimeForecastStatus.AVAILABLE


# ---------------------------------------------------------------------------
# Fail-open: a corrupt/failing lookup degrades to unknown, never raises
# ---------------------------------------------------------------------------


def test_corrupt_published_evidence_degrades_to_unknown_not_a_raise() -> None:
    key = tf_cal._time_cell_key("model-a", "high", TimeForecastStreamMode.STREAMING)
    # band50_y_by_k has a non-numeric value: math.exp() on it raises inside
    # the try/except, which must degrade rather than propagate.
    broken = _evidence(full_confidence=True, band50={4: ("not-a-float", 0.9)})
    forecast = _build(published_prior={key: broken})
    assert forecast.status is TimeForecastStatus.UNKNOWN
    assert forecast.remaining_time_likely_50_ms is None


# ---------------------------------------------------------------------------
# Outer activation gate — default-off short-circuits regardless of evidence
# ---------------------------------------------------------------------------


def test_default_off_ignores_rich_published_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """``session_forecast._time_calibrated_or_fallback`` never even calls the
    engine unless the outer flag is on — the inner engine having a fully
    warmed, available cell must not leak through a default-off deployment."""
    from tokenpak.proxy import session_forecast

    key = tf_cal._time_cell_key("model-a", "high", TimeForecastStreamMode.STREAMING)
    # The real activation switch, forced off through its documented env var
    # — not a patch of the gate function itself.
    monkeypatch.setenv(session_forecast._TIME_FORECAST_ENV_VAR, "0")

    turn = type(
        "Row",
        (),
        {"stream_mode": "sse", "ttfb_ms": 100, "stream_duration_ms": 59_900},
    )()
    wrapped = type("Turn", (), {"row": turn})()
    forecast = session_forecast._time_calibrated_or_fallback(
        monitor_db_path="ignored",
        as_of=NOW,
        session_id="active",
        model="model-a",
        effort="high",
        turns=[wrapped],
        published_prior={key: _evidence(full_confidence=True)},
    )
    assert forecast.status is TimeForecastStatus.UNAVAILABLE
    assert forecast.remaining_time_likely_50_ms is None


def test_default_off_ignores_the_real_shipped_sonnet_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same proof as above, but against the real module-level table (no
    injected ``published_prior``) — the one reviewed cell must still not
    leak through a default-off deployment."""
    from tokenpak.proxy import session_forecast

    monkeypatch.delenv(session_forecast._TIME_FORECAST_ENV_VAR, raising=False)
    turn = type(
        "Row",
        (),
        {"stream_mode": "sse", "ttfb_ms": 100, "stream_duration_ms": 59_900},
    )()
    wrapped = type("Turn", (), {"row": turn})()
    forecast = session_forecast._time_calibrated_or_fallback(
        monitor_db_path="ignored",
        as_of=NOW,
        session_id="active",
        model="claude-sonnet-5",
        effort="unknown",
        turns=[wrapped],
    )
    assert forecast.status is TimeForecastStatus.UNAVAILABLE
    assert forecast.remaining_time_likely_50_ms is None


def test_enabled_serves_the_real_shipped_sonnet_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the outer switch explicitly on, the real shipped table serves an
    ``available`` band for the one reviewed cell — end to end through
    ``session_forecast``, not just the inner engine."""
    from tokenpak.proxy import session_forecast

    monkeypatch.setenv(session_forecast._TIME_FORECAST_ENV_VAR, "1")
    turn = type(
        "Row",
        (),
        {"stream_mode": "sse", "ttfb_ms": 100, "stream_duration_ms": 59_900},
    )()
    wrapped = type("Turn", (), {"row": turn})()
    forecast = session_forecast._time_calibrated_or_fallback(
        monitor_db_path="ignored",
        as_of=NOW,
        session_id="active",
        model="claude-sonnet-5",
        effort="unknown",
        turns=[wrapped],
    )
    assert forecast.status is TimeForecastStatus.AVAILABLE
    assert forecast.remaining_time_likely_50_ms is not None
    assert forecast.coverage.history_n == 43


def test_env_var_true_values_enable_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from tokenpak.proxy import session_forecast

    monkeypatch.setenv(session_forecast._TIME_FORECAST_ENV_VAR, "1")
    assert session_forecast._time_forecast_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no"])
def test_env_var_false_values_keep_the_gate_off(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    from tokenpak.proxy import session_forecast

    monkeypatch.setenv(session_forecast._TIME_FORECAST_ENV_VAR, value)
    assert session_forecast._time_forecast_enabled() is False


def test_no_env_var_and_no_config_key_defaults_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenpak import _paths
    from tokenpak.proxy import session_forecast

    monkeypatch.delenv(session_forecast._TIME_FORECAST_ENV_VAR, raising=False)
    # A real, isolated TOKENPAK_HOME with no config.json on disk at all —
    # load_config() reads the real (absent) file rather than a patched
    # function returning a canned value.
    monkeypatch.setenv(_paths.ENV_VAR, str(tmp_path))
    assert session_forecast._time_forecast_enabled() is False


def test_config_key_can_enable_without_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenpak import _paths
    from tokenpak.proxy import session_forecast

    monkeypatch.delenv(session_forecast._TIME_FORECAST_ENV_VAR, raising=False)
    monkeypatch.setenv(_paths.ENV_VAR, str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"time_forecast_bands": {"enabled": True}}))
    assert session_forecast._time_forecast_enabled() is True


# ---------------------------------------------------------------------------
# Clean-process regression: the config-key enable must survive real process
# startup, not just an in-process monkeypatch.
#
# A real proxy process imports ``tokenpak.proxy`` before ``session_forecast``
# ever reads config: ``tokenpak/proxy/config.py`` calls
# ``config_loader.load_config()`` at MODULE IMPORT TIME, which fires the
# one-shot ``config.json`` -> ``config.yaml`` auto-migration (see
# ``config_loader._maybe_migrate_json_to_yaml``) before this module's own
# gate check ever runs. Once that migration fires, ``config.json`` no longer
# exists on disk. A gate check that reads only the raw JSON file (rather than
# the migration-aware merged view) silently stops seeing a key the user set
# exactly as ``docs/REST_API.md`` instructs.
#
# An in-process test that monkeypatches ``TOKENPAK_HOME`` cannot reproduce
# this: by the time such a test runs, ``tokenpak.proxy`` (and therefore
# ``config_loader``) is typically already imported against a *different*
# home, so the migration this bug depends on either already ran elsewhere or
# never runs against the test's own tmp_path at all. Only a genuinely fresh
# subprocess — importing ``tokenpak.proxy`` for the first time against the
# test's isolated ``TOKENPAK_HOME`` — exercises the real, import-order-
# dependent failure.
# ---------------------------------------------------------------------------

_SONNET_CELL_PROBE = """
import json
from datetime import datetime, timezone

from tokenpak.proxy import session_forecast

turn = type(
    "Row", (), {"stream_mode": "sse", "ttfb_ms": 100, "stream_duration_ms": 59_900}
)()
wrapped = type("Turn", (), {"row": turn})()
forecast = session_forecast._time_calibrated_or_fallback(
    monitor_db_path=None,
    as_of=datetime(2026, 9, 1, tzinfo=timezone.utc),
    session_id="active",
    model="claude-sonnet-5",
    effort="unknown",
    turns=[wrapped],
)
print(json.dumps({
    "gate": session_forecast._time_forecast_enabled(),
    "status": forecast.status.value,
    "has_band": forecast.remaining_time_likely_50_ms is not None,
}))
"""


def _run_clean_process(tmp_path: Path, *, config: dict | None) -> dict:
    """Run the sonnet-cell probe in a genuinely fresh subprocess.

    ``tmp_path`` is the process's only ``TOKENPAK_HOME`` — nothing has ever
    imported ``tokenpak`` against it before this call, so the real one-shot
    migration and the real gate-check import order both fire exactly as they
    would for an operator following the docs on a brand-new machine.
    """
    if config is not None:
        (tmp_path / "config.json").write_text(json.dumps(config))
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "TOKENPAK_HOME": str(tmp_path),
    }
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        env["VIRTUAL_ENV"] = virtual_env
    result = subprocess.run(
        [sys.executable, "-c", _SONNET_CELL_PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_clean_process_documented_enable_activates_the_sonnet_cell(tmp_path: Path) -> None:
    """Fresh subprocess, config set exactly as the docs instruct: the gate
    reads True and the reviewed sonnet cell serves a real ``available`` band
    — not just an internal flag flip."""
    payload = _run_clean_process(tmp_path, config={"time_forecast_bands": {"enabled": True}})
    assert payload == {"gate": True, "status": "available", "has_band": True}


def test_clean_process_without_config_stays_fully_off(tmp_path: Path) -> None:
    """Same fresh-subprocess harness, no config at all: fully off end to
    end — the control case proving the harness itself is not silently
    always-on."""
    payload = _run_clean_process(tmp_path, config=None)
    assert payload == {"gate": False, "status": "unavailable", "has_band": False}
