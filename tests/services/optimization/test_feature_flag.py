"""TOKENPAK_OPTIMIZATION_PIPELINE flag semantics."""

from __future__ import annotations

from tokenpak.services.optimization.policies import (
    ENV_FLAG,
    MODE_OBSERVE,
    MODE_OFF,
    is_pipeline_enabled,
    read_mode,
)


def test_default_is_off():
    assert read_mode({}) == MODE_OFF
    assert is_pipeline_enabled({}) is False


def test_explicit_off_is_off():
    for raw in ("", "0", "off", "OFF"):
        assert read_mode({ENV_FLAG: raw}) == MODE_OFF
        assert is_pipeline_enabled({ENV_FLAG: raw}) is False


def test_observe_aliases():
    for raw in ("1", "on", "observe", "OBSERVE", "TRUE", "yes"):
        assert read_mode({ENV_FLAG: raw}) == MODE_OBSERVE
        assert is_pipeline_enabled({ENV_FLAG: raw}) is True


def test_apply_value_downgrades_to_observe_in_this_module():
    """Observe-only scaffolding must NEVER expose an apply mode itself.

    Even if a deployer sets the flag to ``apply``, this module reports
    observe so the pipeline cannot accidentally call ``stage.apply``.
    """
    assert read_mode({ENV_FLAG: "apply"}) == MODE_OBSERVE


def test_unknown_value_is_off():
    assert read_mode({ENV_FLAG: "not-a-real-value"}) == MODE_OFF
