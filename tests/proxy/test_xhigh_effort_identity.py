# SPDX-License-Identifier: Apache-2.0
"""Exact explicit effort labels survive ingestion and historical reads."""

import json
import sqlite3

import pytest

from tests.proxy.test_session_forecast_calibration import NOW, _corpus, _seed_history_db
from tokenpak.proxy import session_forecast_calibration as cal
from tokenpak.proxy.server import _provider_usage_observation
from tokenpak.proxy.spend_guard.session_state import _reasoning_effort_cell


@pytest.mark.parametrize(
    "body",
    [
        {"output_config": {"effort": "xhigh"}},
        {"reasoning": {"effort": "xhigh"}},
        {"reasoning_effort": "xhigh"},
    ],
)
def test_xhigh_request_observation_preserves_exact_label_and_wire_bytes(body):
    wire = json.dumps(body).encode()
    before = bytes(wire)
    observation = _provider_usage_observation(
        "anthropic", {"input_tokens": 2, "output_tokens": 3}, wire
    )
    assert wire == before
    assert observation["reasoning_effort"] == "xhigh"
    assert observation["reasoning_effort_raw"] == "xhigh"
    assert observation["reasoning_effort_source"] == "request_body"
    assert _reasoning_effort_cell(
        observation["reasoning_effort"],
        observation["reasoning_effort_raw"],
        observation["reasoning_effort_source"],
    ) == ("xhigh", False)


@pytest.mark.parametrize(
    "source", ["request_body_unrecognized", "provider_usage_object_unrecognized"]
)
def test_legacy_xhigh_provenance_is_read_without_rewriting_or_collapsing(source):
    assert _reasoning_effort_cell("", "xhigh", source) == ("xhigh", False)
    assert _reasoning_effort_cell("high", "xhigh", source) == ("unknown", True)
    assert _reasoning_effort_cell("", "xhigh", "") == ("unknown", True)
    assert _reasoning_effort_cell("", "", "") == ("unknown", False)


def test_legacy_xhigh_histories_enter_distinct_cell_with_no_database_edits(tmp_path):
    db = tmp_path / "monitor.db"
    _seed_history_db(db, _corpus(1))
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE requests ADD COLUMN reasoning_effort_source TEXT")
        conn.execute("ALTER TABLE requests ADD COLUMN reasoning_effort_raw TEXT")
        conn.execute(
            "UPDATE requests SET reasoning_effort = '', reasoning_effort_raw = 'xhigh', reasoning_effort_source = 'request_body_unrecognized'"
        )
    before = db.read_bytes()
    histories = cal.read_history(str(db), now=NOW)
    assert len(histories) == 1
    assert histories[0].effort == "xhigh"
    assert db.read_bytes() == before
