"""Regression: usage reports aggregate metadata without opening prompt content."""

import json
import sqlite3
from argparse import Namespace

from tokenpak import _cli_core


def _seed(path):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE requests (work_tag TEXT, session_id TEXT, model TEXT, served_model TEXT, reasoning_effort TEXT, input_tokens INTEGER, cache_read_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER, timestamp TEXT, user_id TEXT, prompt TEXT)"
        )
        conn.executemany(
            "INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "orca:job_example:1",
                    "a",
                    "alias",
                    "served-a",
                    "xhigh",
                    10,
                    3,
                    5,
                    2,
                    "2026-10-16T01:00:00",
                    "owner",
                    "secret",
                ),
                (
                    "orca:job_example:1",
                    "a",
                    "alias",
                    "served-b",
                    "max",
                    20,
                    4,
                    6,
                    3,
                    "2026-10-16T02:00:00",
                    "owner",
                    "secret",
                ),
                (
                    "orca:job_example:1",
                    "b",
                    "alias",
                    "",
                    "ultra",
                    30,
                    5,
                    7,
                    None,
                    "2026-10-16T03:00:00",
                    "other",
                    "secret",
                ),
            ],
        )


def test_usage_groups_tags_sessions_and_preserves_unknowns(tmp_path):
    from tokenpak.telemetry.job_usage import read_usage

    db = tmp_path / "monitor.db"
    _seed(db)
    before = db.read_bytes()
    data = read_usage(db, work_tag="orca:job_example:1")
    assert db.read_bytes() == before
    total = data["tags"][0]
    assert (
        total["request_count"],
        total["input_tokens"],
        total["cached_tokens"],
        total["output_tokens"],
        total["reasoning_tokens"],
    ) == (3, 60, 12, 18, 5)
    assert total["served_models"] == ["served-a", "served-b"]
    assert total["unknown_model_requests"] == 1
    assert total["efforts"] == ["max", "ultra", "xhigh"]
    assert len(data["sessions"]) == 2
    assert "secret" not in json.dumps(data)
    assert read_usage(db, user_id="owner")["tags"][0]["request_count"] == 2
    assert read_usage(db, session_id="b")["sessions"][0]["reasoning_tokens"] is None


def test_read_only_legacy_and_missing_database(tmp_path):
    from tokenpak.telemetry.job_usage import read_usage

    db = tmp_path / "missing.db"
    assert read_usage(db)["tags"] == []
    assert not db.exists()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE requests (model TEXT, timestamp TEXT, input_tokens INTEGER, output_tokens INTEGER)"
        )
        conn.execute("INSERT INTO requests VALUES ('legacy', '2026-10-16', 1, 2)")
    before = db.read_bytes()
    result = read_usage(db)
    assert result["sessions"][0]["work_tag"] == ""
    assert result["sessions"][0]["reasoning_tokens"] is None
    assert db.read_bytes() == before
    assert read_usage(db, work_tag="orca:job_example:1")["tags"] == []


def test_report_cli_reads_usage_locally(tmp_path, capsys):
    db = tmp_path / "monitor.db"
    _seed(db)
    _cli_core.cmd_report(
        Namespace(usage=True, db=str(db), work_tag="orca:job_example:1", session_id="a", json=True)
    )
    assert json.loads(capsys.readouterr().out)["sessions"][0]["request_count"] == 2


def test_endpoint_scopes_authenticated_users_and_rejects_ambiguous_filters(tmp_path):
    from io import BytesIO
    from types import SimpleNamespace

    from tokenpak.proxy.job_usage import handle_usage

    db = tmp_path / "monitor.db"
    _seed(db)
    codes = []
    handler = SimpleNamespace(
        path="/stats/usage?work_tag=orca:job_example:1",
        _tokenpak_user_id="owner",
        wfile=BytesIO(),
        send_response=codes.append,
        send_header=lambda *args: None,
        end_headers=lambda: None,
    )
    handle_usage(handler, db)
    assert codes == [200]
    assert json.loads(handler.wfile.getvalue())["tags"][0]["request_count"] == 2
    handler.path = "/stats/usage?work_tag=a&work_tag=b"
    handler.wfile = BytesIO()
    handle_usage(handler, db)
    assert codes[-1] == 400
    assert "secret" not in handler.wfile.getvalue().decode()


def test_served_model_observation_and_monitor_persistence(tmp_path):
    from tokenpak.proxy import monitor as monitor_module
    from tokenpak.proxy.job_usage import observed_model
    from tokenpak.proxy.monitor import Monitor
    from tokenpak.telemetry.job_usage import read_usage

    events = b'data: {"type":"response.completed","response":{"model":"served-response"}}\n\n'
    before = bytes(events)
    assert observed_model(events, streaming=True) == "served-response"
    assert events == before
    assert observed_model(b'{"model":"served-json"}') == "served-json"
    assert observed_model(b"{}") == ""
    db = tmp_path / "monitor.db"
    monitor = Monitor(db_path=str(db))
    monitor.log(
        model="requested-alias",
        served_model="served-response",
        input_tokens=1,
        output_tokens=2,
        cost=0,
        latency_ms=1,
        status_code=200,
        endpoint="/",
        work_tag="opaque",
    )
    assert monitor_module._stop_db_write_queue(timeout=20)
    result = read_usage(db)
    assert result["tags"][0]["served_models"] == ["served-response"]
    assert "requested-alias" not in json.dumps(result)


def test_usage_endpoint_runs_behind_existing_auth_gate(tmp_path):
    from io import BytesIO
    from types import SimpleNamespace

    from tokenpak.proxy.server import _ProxyHandler

    db = tmp_path / "monitor.db"
    _seed(db)
    handler = object.__new__(_ProxyHandler)
    handler.server = SimpleNamespace(
        proxy_server=SimpleNamespace(monitor=SimpleNamespace(db_path=str(db)))
    )
    handler.headers = {}
    handler.path = "/stats/usage?session_id=a"
    handler._tokenpak_user_id = "owner"
    handler.wfile = BytesIO()
    codes = []
    handler.send_response = codes.append
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    handler._enforce_proxy_auth = lambda: False
    handler.do_GET()
    assert codes == [] and handler.wfile.getvalue() == b""
    handler._enforce_proxy_auth = lambda: True
    handler.do_GET()
    assert codes == [200]
    assert json.loads(handler.wfile.getvalue())["sessions"][0]["request_count"] == 2


def test_nullable_and_historical_efforts_keep_distinct_groups(tmp_path):
    from tokenpak.telemetry.job_usage import read_usage

    db = tmp_path / "monitor.db"
    _seed(db)
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE requests ADD COLUMN reasoning_effort_raw TEXT")
        conn.execute("ALTER TABLE requests ADD COLUMN reasoning_effort_source TEXT")
        conn.execute(
            "UPDATE requests SET reasoning_effort='', reasoning_effort_raw='max', reasoning_effort_source='request_body_unrecognized' WHERE session_id='b'"
        )
    total = read_usage(db)["tags"][0]
    assert total["efforts"] == ["max", "xhigh"]
    assert total["first_timestamp"] == "2026-10-16T01:00:00"
    assert total["last_timestamp"] == "2026-10-16T03:00:00"
    assert read_usage(db, work_tag="' OR 1=1 --")["tags"] == []
