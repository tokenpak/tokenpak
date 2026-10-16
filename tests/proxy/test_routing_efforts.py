"""Regression: client effort labels remain distinct in telemetry (2026-10-16)."""

import json

import pytest

from tokenpak.proxy.server import _provider_usage_observation
from tokenpak.proxy.spend_guard.session_state import _reasoning_effort_cell


@pytest.mark.parametrize(
    "effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
)
@pytest.mark.parametrize("field", ["reasoning_effort", "reasoning", "output_config"])
def test_request_effort_labels(effort, field):
    body = {field: effort if field == "reasoning_effort" else {"effort": effort}}
    wire = json.dumps(body).encode()
    observation = _provider_usage_observation(
        "openai", {"input_tokens": 2, "output_tokens": 3}, wire
    )
    assert observation["reasoning_effort"] == effort
    assert observation["reasoning_effort_source"] == "request_body"
    assert observation["reasoning_effort_raw"] == effort
    assert json.loads(wire) == body
    assert _reasoning_effort_cell(effort, effort, "request_body") == (effort, False)


@pytest.mark.parametrize("effort", ["none", "minimal", "xhigh", "max", "ultra"])
def test_provider_and_legacy_effort_labels(effort):
    observation = _provider_usage_observation(
        "openai", {"input_tokens": 2, "output_tokens": 3, "reasoning_effort": effort}, b"{}"
    )
    assert observation["reasoning_effort"] == effort
    assert observation["reasoning_effort_source"] == "provider_usage_object"
    assert _reasoning_effort_cell("", effort, "request_body_unrecognized") == (effort, False)
    assert _reasoning_effort_cell("high", effort, "request_body_unrecognized") == ("unknown", True)


def test_unknown_effort_stays_raw():
    observation = _provider_usage_observation("openai", {}, b'{"reasoning_effort":"future"}')
    assert observation["reasoning_effort"] == ""
    assert observation["reasoning_effort_raw"] == "future"
    assert _reasoning_effort_cell("", "future", "request_body_unrecognized") == ("unknown", True)


@pytest.mark.parametrize("streaming", [False, True])
def test_codex_responses_reasoning_counts_are_already_observed(streaming, tmp_path):
    """The historical empty-column finding is not reproduced on this base."""
    import sqlite3

    from tokenpak.proxy import monitor as monitor_module
    from tokenpak.proxy.monitor import Monitor
    from tokenpak.proxy.server import _extract_response_usage
    from tokenpak.proxy.streaming import _extract_sse_usage

    usage = {
        "input_tokens": 100,
        "output_tokens": 40,
        "input_tokens_details": {"cached_tokens": 50},
        "output_tokens_details": {"reasoning_tokens": 30},
    }
    if streaming:
        wire = (
            b"data: "
            + json.dumps({"type": "response.completed", "response": {"usage": usage}}).encode()
            + b"\n\n"
        )
        extracted = _extract_sse_usage(wire)
    else:
        wire = json.dumps({"usage": usage}).encode()
        extracted = _extract_response_usage(wire)
    observation = _provider_usage_observation("openai-codex", extracted, b"{}")
    assert observation["reasoning_tokens"] == 30
    assert observation["visible_output_tokens"] == 10
    assert observation["provider_output_tokens"] == 40
    assert observation["provider_cache_read_tokens"] == 50
    db = tmp_path / "monitor.db"
    monitor = Monitor(db_path=str(db))
    monitor.log(
        model="test",
        input_tokens=100,
        output_tokens=40,
        cost=0,
        latency_ms=1,
        status_code=200,
        endpoint="/v1/responses",
        reasoning_tokens=observation["reasoning_tokens"],
        visible_output_tokens=observation["visible_output_tokens"],
    )
    assert monitor_module._stop_db_write_queue(timeout=20)
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT reasoning_tokens, visible_output_tokens FROM requests"
        ).fetchone() == (30, 10)
