"""Tests for tokenpak.agent.agentic.retry"""
import pytest
from tokenpak.agent.agentic.retry import (
    RetryEngine, RetryExhaustedError,
    MODEL_DOWNGRADE_PATH, PROVIDER_FALLBACK_PATH,
)


def make_fn(successes_on=None, always_fail=False, fail_levels=None):
    """
    successes_on: set of call indices (0-based) that succeed
    always_fail: always raises
    fail_levels: raise on first N calls, then succeed
    """
    calls = {"count": 0}

    def fn(context, state):
        idx = calls["count"]
        calls["count"] += 1
        state["last_call"] = idx
        if always_fail:
            raise RuntimeError(f"always-fail call {idx}")
        if fail_levels is not None and idx < fail_levels:
            raise RuntimeError(f"fail call {idx}")
        if successes_on is not None and idx not in successes_on:
            raise RuntimeError(f"fail call {idx}")
        return f"success-{idx}"

    fn.calls = calls
    return fn


def test_success_on_first_attempt(tmp_path):
    fn = make_fn(successes_on={0})
    engine = RetryEngine(fn=fn, context={"task_id": "t1"}, state_dir=tmp_path, wait_seconds=[0])
    result = engine.run()
    assert result == "success-0"


def test_level0_retries_then_succeeds(tmp_path):
    fn = make_fn(fail_levels=2)
    engine = RetryEngine(fn=fn, context={"task_id": "t2"}, state_dir=tmp_path, wait_seconds=[0, 0])
    result = engine.run()
    assert "success" in result


def test_model_downgrade_succeeds(tmp_path):
    """Fail level0, succeed after model downgrade."""
    calls = {"count": 0, "models": []}

    def fn(context, state):
        calls["count"] += 1
        calls["models"].append(context.get("model"))
        if calls["count"] <= 4:  # all level-0 attempts
            raise RuntimeError("fail")
        return "ok"

    engine = RetryEngine(
        fn=fn,
        context={"task_id": "t3", "model": MODEL_DOWNGRADE_PATH[0]},
        state_dir=tmp_path,
        wait_seconds=[0, 0, 0],
    )
    result = engine.run()
    assert result == "ok"
    assert any(m != MODEL_DOWNGRADE_PATH[0] for m in calls["models"])


def test_exhausted_saves_state_and_alerts(tmp_path):
    alerts = []

    def on_alert(alert):
        alerts.append(alert)

    fn = make_fn(always_fail=True)
    engine = RetryEngine(
        fn=fn,
        context={"task_id": "t-exhaust", "task": "test task"},
        state_dir=tmp_path,
        wait_seconds=[0, 0],
        on_human_alert=on_alert,
        # no handoff handler → level 3 fails too
    )
    with pytest.raises(RetryExhaustedError) as exc_info:
        engine.run()

    err = exc_info.value
    assert len(err.attempts) > 0
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"
    assert (tmp_path / "t-exhaust.json").exists()


def test_partial_state_preserved(tmp_path):
    """Partial state written by fn is saved on failure."""
    def fn(context, state):
        state["progress"] = "halfway"
        raise RuntimeError("fail")

    alerts = []
    engine = RetryEngine(
        fn=fn,
        context={"task_id": "t-state"},
        state_dir=tmp_path,
        wait_seconds=[0],
        on_human_alert=lambda a: alerts.append(a),
    )
    with pytest.raises(RetryExhaustedError) as exc:
        engine.run()
    assert exc.value.partial_state.get("progress") == "halfway"


def test_handoff_accepted(tmp_path):
    calls = {"count": 0}

    def fn(context, state):
        calls["count"] += 1
        raise RuntimeError("fail")

    def on_handoff(ctx, state):
        return True  # accept handoff

    engine = RetryEngine(
        fn=fn,
        context={"task_id": "t-handoff"},
        state_dir=tmp_path,
        wait_seconds=[0],
        on_handoff=on_handoff,
    )
    result = engine.run()
    assert result["_handoff"] is True


def test_handoff_rejected_escalates_to_human(tmp_path):
    alerts = []

    def on_handoff(ctx, state):
        return False

    fn = make_fn(always_fail=True)
    engine = RetryEngine(
        fn=fn,
        context={"task_id": "t-rejected-handoff"},
        state_dir=tmp_path,
        wait_seconds=[0],
        on_handoff=on_handoff,
        on_human_alert=lambda a: alerts.append(a),
    )
    with pytest.raises(RetryExhaustedError):
        engine.run()
    assert len(alerts) == 1


def test_default_model_downgrade_path(tmp_path):
    engine = RetryEngine(fn=make_fn(), context={"task_id": "x"}, state_dir=tmp_path)
    current = MODEL_DOWNGRADE_PATH[0]
    for expected in MODEL_DOWNGRADE_PATH[1:]:
        assert engine._default_model_downgrade(current) == expected
        current = expected
    # At end of path, returns last entry
    assert engine._default_model_downgrade(MODEL_DOWNGRADE_PATH[-1]) == MODEL_DOWNGRADE_PATH[-1]


def test_default_provider_switch_path(tmp_path):
    engine = RetryEngine(fn=make_fn(), context={"task_id": "x"}, state_dir=tmp_path)
    current = PROVIDER_FALLBACK_PATH[0]
    for expected in PROVIDER_FALLBACK_PATH[1:]:
        assert engine._default_provider_switch(current) == expected
        current = expected
