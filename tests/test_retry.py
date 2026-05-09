"""Tests for tokenpak.agentic.retry"""

import pytest

pytest.importorskip("tokenpak.agentic.retry", reason="module not available in current build")
import json

import pytest
from tokenpak.agentic.retry import (
    MODEL_DOWNGRADE_PATH,
    PROVIDER_FALLBACK_PATH,
    RetryEngine,
    RetryExhaustedError,
    _extract_http_status,
)

# ── helpers ──────────────────────────────────────────────────────────────────

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


def make_http_fn(status_code: int, succeed_after: int = 999):
    """Always raises an HTTPError with *status_code* until succeed_after calls."""
    calls = {"count": 0}

    class FakeHTTPError(Exception):
        def __init__(self, code):
            self.status_code = code
            super().__init__(f"HTTP {code} error")

    def fn(context, state):
        idx = calls["count"]
        calls["count"] += 1
        if idx < succeed_after:
            raise FakeHTTPError(status_code)
        return f"success-{idx}"

    fn.calls = calls
    return fn


# ── basic escalation ──────────────────────────────────────────────────────────

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


# ── per-error-type behavior ───────────────────────────────────────────────────

def test_429_triggers_wait_behavior(tmp_path):
    """429 rate-limit errors should trigger exponential backoff (wait behavior)."""
    fn = make_http_fn(status_code=429, succeed_after=2)
    waits_used = []

    # Patch sleep to capture wait times
    import tokenpak.agentic.retry as retry_mod
    original_sleep = retry_mod.time.sleep

    def fake_sleep(s):
        waits_used.append(s)

    retry_mod.time.sleep = fake_sleep
    try:
        engine = RetryEngine(
            fn=fn,
            context={"task_id": "t-429"},
            state_dir=tmp_path,
            wait_seconds=[0.01, 0.02, 0.04],
        )
        result = engine.run()
        assert "success" in result
        # Should have waited at least once (429 → "wait" behavior)
        assert len(waits_used) >= 1
    finally:
        retry_mod.time.sleep = original_sleep


def test_500_triggers_retry(tmp_path):
    """500 server errors should retry without waiting (retry behavior)."""
    fn = make_http_fn(status_code=500, succeed_after=1)
    engine = RetryEngine(
        fn=fn,
        context={"task_id": "t-500"},
        state_dir=tmp_path,
        wait_seconds=[99, 99, 99],  # large waits — 500 should skip them
    )
    # Monkeypatch sleep so test doesn't actually wait
    import tokenpak.agentic.retry as retry_mod
    original_sleep = retry_mod.time.sleep
    waits_used = []
    retry_mod.time.sleep = lambda s: waits_used.append(s)
    try:
        result = engine.run()
        assert "success" in result
        # For 500 (retry, not wait), we expect zero waits
        assert all(w == 0 for w in waits_used)
    finally:
        retry_mod.time.sleep = original_sleep


def test_401_triggers_immediate_alert(tmp_path):
    """401 auth errors should bypass escalation and alert immediately."""
    alerts = []
    fn = make_http_fn(status_code=401)

    engine = RetryEngine(
        fn=fn,
        context={"task_id": "t-401"},
        state_dir=tmp_path,
        wait_seconds=[0],
        on_human_alert=lambda a: alerts.append(a),
    )
    with pytest.raises(RetryExhaustedError) as exc_info:
        engine.run()

    # Should have alerted immediately with minimal attempts
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"
    # The error message should reference the 401
    err = exc_info.value
    assert len(err.attempts) <= 2  # immediate alert = very few attempts


def test_403_triggers_immediate_alert(tmp_path):
    """403 forbidden errors also trigger immediate alert."""
    alerts = []
    fn = make_http_fn(status_code=403)

    engine = RetryEngine(
        fn=fn,
        context={"task_id": "t-403"},
        state_dir=tmp_path,
        wait_seconds=[0],
        on_human_alert=lambda a: alerts.append(a),
    )
    with pytest.raises(RetryExhaustedError):
        engine.run()
    assert len(alerts) == 1


def test_all_providers_fail_triggers_alert(tmp_path):
    """When all providers fail, should hit Level 4 alert."""
    alerts = []
    provider_chain = ["anthropic", "openai", "google"]
    called_providers = []

    def fn(context, state):
        called_providers.append(context.get("provider", "anthropic"))
        raise RuntimeError("provider down")

    engine = RetryEngine(
        fn=fn,
        context={"task_id": "t-all-fail", "provider": "anthropic"},
        state_dir=tmp_path,
        wait_seconds=[0],
        on_human_alert=lambda a: alerts.append(a),
    )
    with pytest.raises(RetryExhaustedError):
        engine.run()
    assert len(alerts) == 1
    # Should have tried multiple providers
    assert len(set(called_providers)) >= 2


# ── config loading ────────────────────────────────────────────────────────────

def test_wait_seconds_default_is_1_2_4(tmp_path):
    """Default wait_seconds should be [1, 2, 4] per spec."""
    import tokenpak.agentic.retry as retry_mod
    # Temporarily override config path to avoid reading real config
    original = retry_mod.CONFIG_PATH
    retry_mod.CONFIG_PATH = tmp_path / "nonexistent_config.json"
    try:
        engine = RetryEngine(fn=make_fn(), context={"task_id": "x"}, state_dir=tmp_path)
        assert engine.wait_seconds == [1.0, 2.0, 4.0]
    finally:
        retry_mod.CONFIG_PATH = original


def test_config_file_overrides_wait_seconds(tmp_path):
    """Config file retry section should override defaults."""
    import tokenpak.agentic.retry as retry_mod
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"retry": {"wait_seconds": [0.5, 1.0, 2.0]}}))
    original = retry_mod.CONFIG_PATH
    retry_mod.CONFIG_PATH = cfg_file
    try:
        engine = RetryEngine(fn=make_fn(), context={"task_id": "x"}, state_dir=tmp_path)
        assert engine.wait_seconds == [0.5, 1.0, 2.0]
    finally:
        retry_mod.CONFIG_PATH = original


# ── shadow / event logging ────────────────────────────────────────────────────

def test_retry_events_logged(tmp_path, monkeypatch):
    """Retry events should be appended to the JSONL log."""
    import tokenpak.agentic.retry as retry_mod
    event_log = tmp_path / "retry_events.jsonl"
    monkeypatch.setattr(retry_mod, "RETRY_EVENT_LOG", event_log)

    fn = make_fn(fail_levels=1)
    engine = RetryEngine(fn=fn, context={"task_id": "t-log"}, state_dir=tmp_path, wait_seconds=[0])
    engine.run()

    assert event_log.exists()
    events = [json.loads(l) for l in event_log.read_text().strip().splitlines()]
    event_types = [e["event"] for e in events]
    assert "run_start" in event_types
    assert "run_success" in event_types


def test_load_recent_retry_events(tmp_path, monkeypatch):
    """load_recent_retry_events should return events from the JSONL log."""
    import tokenpak.agentic.retry as retry_mod
    event_log = tmp_path / "retry_events.jsonl"
    monkeypatch.setattr(retry_mod, "RETRY_EVENT_LOG", event_log)

    # Write some fake events
    for i in range(5):
        with event_log.open("a") as fh:
            fh.write(json.dumps({"event": f"test-event-{i}", "timestamp": i}) + "\n")

    events = retry_mod.load_recent_retry_events(n=3)
    assert len(events) == 3
    assert events[-1]["event"] == "test-event-4"


# ── extract_http_status ───────────────────────────────────────────────────────

def test_extract_http_status_from_attribute():
    class FakeErr(Exception):
        status_code = 429
    assert _extract_http_status(FakeErr()) == "429"


def test_extract_http_status_from_message():
    err = RuntimeError("Server responded with 503 Service Unavailable")
    assert _extract_http_status(err) == "503"


def test_extract_http_status_none_when_no_code():
    err = RuntimeError("connection refused")
    assert _extract_http_status(err) is None
