"""OptimizationTrace serialization and invariants."""

from __future__ import annotations

from tokenpak.services.optimization.trace import OptimizationTrace, StageTrace


def test_service_dtos_keep_historical_runtime_identity():
    from tokenpak.core.contracts.trace import OptimizationTrace as ContractOptimizationTrace
    from tokenpak.core.contracts.trace import StageTrace as ContractStageTrace

    # Historical module path + class name are compatibility surface for
    # reflection and default pickle globals across releases.
    assert StageTrace.__module__ == "tokenpak.services.optimization.trace"
    assert StageTrace.__qualname__ == "StageTrace"
    assert OptimizationTrace.__module__ == "tokenpak.services.optimization.trace"
    assert OptimizationTrace.__qualname__ == "OptimizationTrace"

    # Same historical names, but distinct service-local shapes — never the
    # canonical contract classes.
    assert StageTrace is not ContractStageTrace
    assert OptimizationTrace is not ContractOptimizationTrace


def test_to_tip_dict_is_legacy_envelope_not_contract_shape():
    import pytest

    from tokenpak.core.contracts.trace import OptimizationTrace as ContractOptimizationTrace

    t = OptimizationTrace(request_id="r-7", mode="observe", started_at=1000.0)
    t.add_stage(StageTrace(name="cache", eligible=True))
    payload = t.to_tip_dict()

    # The envelope keeps the service-local observe-only key set.
    assert sorted(payload) == [
        "body_bytes_in",
        "body_bytes_out",
        "body_unchanged",
        "bypass_reason",
        "mode",
        "request_id",
        "stages",
        "started_at",
        "tip_version",
    ]

    # The envelope is not constructible as the canonical trace contract:
    # tip_version is informational only, not a contract-shape discriminator.
    with pytest.raises(TypeError):
        ContractOptimizationTrace(**payload)


def test_trace_to_dict_roundtrip():
    t = OptimizationTrace(request_id="r-1", mode="observe")
    t.add_stage(StageTrace(name="cache", eligible=True, duration_ms=0.42))
    t.add_stage(StageTrace(name="compress", eligible=False, skip_reason="capability-missing"))
    t.body_bytes_in = 100
    t.body_bytes_out = 100

    d = t.to_dict()
    assert d["request_id"] == "r-1"
    assert d["mode"] == "observe"
    assert d["body_unchanged"] is True
    assert len(d["stages"]) == 2
    assert d["stages"][0]["name"] == "cache"
    assert d["stages"][0]["eligible"] is True
    assert d["stages"][1]["skip_reason"] == "capability-missing"


def test_body_unchanged_when_byte_counts_match_and_no_apply():
    t = OptimizationTrace(request_id="r-2")
    t.body_bytes_in = 50
    t.body_bytes_out = 50
    t.add_stage(StageTrace(name="x", eligible=False))
    assert t.body_unchanged is True


def test_body_unchanged_false_when_apply_recorded():
    t = OptimizationTrace(request_id="r-3")
    t.body_bytes_in = 50
    t.body_bytes_out = 50
    t.add_stage(StageTrace(name="x", eligible=True, applied=True))
    assert t.body_unchanged is False


def test_body_unchanged_false_when_byte_counts_differ():
    t = OptimizationTrace(request_id="r-4")
    t.body_bytes_in = 50
    t.body_bytes_out = 47
    t.add_stage(StageTrace(name="x", eligible=False))
    assert t.body_unchanged is False


def test_to_tip_dict_includes_version_marker_when_tip_module_present():
    """If TIP-02 is importable the dict carries a tip_version. Otherwise
    the function falls back to to_dict() unchanged."""
    t = OptimizationTrace(request_id="r-5")
    d = t.to_tip_dict()
    # Either way, the proxy-layer fields must be present.
    assert d["request_id"] == "r-5"
    # Tip version is informational; only assert when present.
    if "tip_version" in d:
        assert d["tip_version"].startswith("v")


def test_mark_bypass_records_reason():
    t = OptimizationTrace(request_id="r-6")
    t.mark_bypass("control-path:/health")
    assert t.bypass_reason == "control-path:/health"
    d = t.to_dict()
    assert d["bypass_reason"] == "control-path:/health"
