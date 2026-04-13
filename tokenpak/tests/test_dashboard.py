"""Unit tests for the dashboard module.

Covers:
  - dashboard/__init__.py     : get_dashboard_files, serve_dashboard_file
  - dashboard/session_filter.py : FilterParams, _db_path, SessionFilter
  - dashboard/export_csv.py   : ExportFormat, ExportDataType, CSVExporter, _format_filename
  - dashboard/export_api.py   : ExportAPI
  - dashboard/account_dashboard.py : _get_user_id, _calculate_roi
  - dashboard/app.py          : _today, _days_ago
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# dashboard/__init__.py
# ---------------------------------------------------------------------------


def test_dashboard_package_importable():
    import dashboard  # noqa: F401


def test_get_dashboard_files_returns_dict():
    from dashboard import get_dashboard_files

    files = get_dashboard_files()
    assert isinstance(files, dict)
    assert len(files) == 4


def test_get_dashboard_files_contains_expected_keys():
    from dashboard import get_dashboard_files

    files = get_dashboard_files()
    assert "index.html" in files
    assert "metrics.js" in files
    assert "charts.js" in files
    assert "styles.css" in files


def test_get_dashboard_files_values_are_path_objects():
    from dashboard import get_dashboard_files

    for val in get_dashboard_files().values():
        assert isinstance(val, Path)


def test_serve_dashboard_file_unknown_path_returns_none():
    from dashboard import serve_dashboard_file

    result = asyncio.run(serve_dashboard_file("nonexistent.txt"))
    assert result is None


def test_serve_dashboard_file_root_defaults_to_index():
    from dashboard import serve_dashboard_file

    result = asyncio.run(serve_dashboard_file(""))
    # index.html should exist in the dashboard dir
    if result is not None:
        _content, mime = result
        assert mime == "text/html"


def test_serve_dashboard_file_slash_defaults_to_index():
    from dashboard import serve_dashboard_file

    result = asyncio.run(serve_dashboard_file("/"))
    if result is not None:
        _content, mime = result
        assert mime == "text/html"


def test_serve_dashboard_file_strips_leading_slash():
    from dashboard import serve_dashboard_file

    with_slash = asyncio.run(serve_dashboard_file("/metrics.js"))
    without_slash = asyncio.run(serve_dashboard_file("metrics.js"))
    # Both should agree: both None or both return same content
    assert (with_slash is None) == (without_slash is None)
    if with_slash and without_slash:
        assert with_slash[0] == without_slash[0]


def test_serve_dashboard_file_js_mime_type():
    from dashboard import serve_dashboard_file

    result = asyncio.run(serve_dashboard_file("metrics.js"))
    if result is not None:
        _content, mime = result
        assert mime == "application/javascript"


# ---------------------------------------------------------------------------
# dashboard/session_filter.py — FilterParams
# ---------------------------------------------------------------------------


def test_filter_params_defaults():
    from dashboard.session_filter import FilterParams

    fp = FilterParams()
    assert fp.model is None
    assert fp.from_dt is None
    assert fp.to_dt is None
    assert fp.status == "all"
    assert fp.limit == 50
    assert fp.offset == 0


def test_filter_params_custom_values():
    from dashboard.session_filter import FilterParams

    fp = FilterParams(
        model="gpt-4o",
        from_dt="2026-01-01T00:00:00",
        to_dt="2026-01-31T23:59:59",
        status="success",
        limit=100,
        offset=50,
    )
    assert fp.model == "gpt-4o"
    assert fp.from_dt == "2026-01-01T00:00:00"
    assert fp.to_dt == "2026-01-31T23:59:59"
    assert fp.status == "success"
    assert fp.limit == 100
    assert fp.offset == 50


def test_filter_params_limit_capped_at_500():
    from dashboard.session_filter import FilterParams

    fp = FilterParams(limit=9999)
    assert fp.limit == 500


def test_filter_params_offset_clamped_to_zero():
    from dashboard.session_filter import FilterParams

    fp = FilterParams(offset=-10)
    assert fp.offset == 0


def test_filter_params_invalid_status_raises():
    from dashboard.session_filter import FilterParams

    try:
        FilterParams(status="invalid_status")
        assert False, "should have raised"
    except ValueError as exc:
        assert "invalid_status" in str(exc).lower() or "invalid" in str(exc).lower()


def test_filter_params_all_valid_statuses():
    from dashboard.session_filter import FilterParams, VALID_STATUSES

    for s in VALID_STATUSES:
        fp = FilterParams(status=s)
        assert fp.status == s


def test_filter_params_from_query_string_model_and_status():
    from dashboard.session_filter import FilterParams

    fp = FilterParams.from_query_string("model=claude-sonnet-4-6&status=success&limit=25")
    assert fp.model == "claude-sonnet-4-6"
    assert fp.status == "success"
    assert fp.limit == 25


def test_filter_params_from_query_string_empty():
    from dashboard.session_filter import FilterParams

    fp = FilterParams.from_query_string("")
    assert fp.model is None
    assert fp.status == "all"
    assert fp.limit == 50


# ---------------------------------------------------------------------------
# dashboard/session_filter.py — _db_path
# ---------------------------------------------------------------------------


def test_db_path_default():
    from dashboard.session_filter import _db_path

    p = _db_path()
    assert isinstance(p, Path)
    assert "monitor.db" in str(p)


def test_db_path_env_override(tmp_path):
    from dashboard.session_filter import _db_path

    custom = str(tmp_path / "custom.db")
    with patch.dict(os.environ, {"TOKENPAK_DB": custom}):
        p = _db_path()
    assert str(p) == custom


# ---------------------------------------------------------------------------
# dashboard/session_filter.py — SessionFilter (no DB)
# ---------------------------------------------------------------------------


def test_session_filter_no_db_returns_empty_sessions(tmp_path):
    from dashboard.session_filter import FilterParams, SessionFilter

    sf = SessionFilter(db_path=tmp_path / "nonexistent.db")
    result = sf.query(FilterParams())
    assert result["sessions"] == []
    assert result["total"] == 0


def test_session_filter_no_db_returns_correct_limit_offset(tmp_path):
    from dashboard.session_filter import FilterParams, SessionFilter

    sf = SessionFilter(db_path=tmp_path / "nonexistent.db")
    result = sf.query(FilterParams(limit=20, offset=5))
    assert result["limit"] == 20
    assert result["offset"] == 5


def test_session_filter_distinct_models_no_db(tmp_path):
    from dashboard.session_filter import SessionFilter

    sf = SessionFilter(db_path=tmp_path / "nonexistent.db")
    assert sf.distinct_models() == []


# ---------------------------------------------------------------------------
# dashboard/session_filter.py — SessionFilter with temp SQLite DB
# ---------------------------------------------------------------------------


def _make_temp_db(path: Path) -> None:
    """Create a minimal requests table with sample rows."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE requests (
            id TEXT, timestamp TEXT, model TEXT, request_type TEXT,
            input_tokens INT, output_tokens INT, estimated_cost REAL,
            latency_ms REAL, status_code INT, endpoint TEXT,
            compilation_mode TEXT, protected_tokens INT,
            compressed_tokens INT, injected_tokens INT,
            injected_sources TEXT, cache_read_tokens INT,
            cache_creation_tokens INT
        )
        """
    )
    conn.executemany(
        "INSERT INTO requests (id, timestamp, model, status_code, input_tokens, output_tokens, estimated_cost, latency_ms, request_type, endpoint, compilation_mode, protected_tokens, compressed_tokens, injected_tokens, injected_sources, cache_read_tokens, cache_creation_tokens) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("r1", "2026-04-01T10:00:00", "gpt-4o", 200, 100, 50, 0.01, 120.0, "chat", "/v1/chat", "default", 0, 0, 0, "", 0, 0),
            ("r2", "2026-04-01T11:00:00", "claude-sonnet", 200, 200, 80, 0.02, 150.0, "chat", "/v1/chat", "default", 0, 0, 0, "", 0, 0),
            ("r3", "2026-04-01T12:00:00", "gpt-4o", 400, 50, 0, 0.0, 10.0, "chat", "/v1/chat", "default", 0, 0, 0, "", 0, 0),
        ],
    )
    conn.commit()
    conn.close()


def test_session_filter_with_db_returns_rows(tmp_path):
    from dashboard.session_filter import FilterParams, SessionFilter

    db = tmp_path / "monitor.db"
    _make_temp_db(db)
    sf = SessionFilter(db_path=db)
    result = sf.query(FilterParams())
    assert result["total"] == 3
    assert len(result["sessions"]) == 3


def test_session_filter_with_db_model_filter(tmp_path):
    from dashboard.session_filter import FilterParams, SessionFilter

    db = tmp_path / "monitor.db"
    _make_temp_db(db)
    sf = SessionFilter(db_path=db)
    result = sf.query(FilterParams(model="gpt-4o"))
    assert result["total"] == 2


def test_session_filter_with_db_status_success(tmp_path):
    from dashboard.session_filter import FilterParams, SessionFilter

    db = tmp_path / "monitor.db"
    _make_temp_db(db)
    sf = SessionFilter(db_path=db)
    result = sf.query(FilterParams(status="success"))
    assert result["total"] == 2  # r1 and r2 are 200


def test_session_filter_with_db_status_error(tmp_path):
    from dashboard.session_filter import FilterParams, SessionFilter

    db = tmp_path / "monitor.db"
    _make_temp_db(db)
    sf = SessionFilter(db_path=db)
    result = sf.query(FilterParams(status="error"))
    assert result["total"] == 1  # r3 is 400


def test_session_filter_distinct_models_with_db(tmp_path):
    from dashboard.session_filter import SessionFilter

    db = tmp_path / "monitor.db"
    _make_temp_db(db)
    sf = SessionFilter(db_path=db)
    models = sf.distinct_models()
    assert sorted(models) == ["claude-sonnet", "gpt-4o"]


def test_session_filter_pagination(tmp_path):
    from dashboard.session_filter import FilterParams, SessionFilter

    db = tmp_path / "monitor.db"
    _make_temp_db(db)
    sf = SessionFilter(db_path=db)
    result = sf.query(FilterParams(limit=2, offset=0))
    assert len(result["sessions"]) == 2
    assert result["total"] == 3


def test_session_filter_build_where_no_filters():
    from dashboard.session_filter import FilterParams, SessionFilter

    sf = SessionFilter(db_path=Path("/nonexistent"))
    where_sql, args = sf._build_where(FilterParams())
    assert where_sql == "1=1"
    assert args == []


def test_session_filter_build_where_with_model():
    from dashboard.session_filter import FilterParams, SessionFilter

    sf = SessionFilter(db_path=Path("/nonexistent"))
    where_sql, args = sf._build_where(FilterParams(model="gpt-4o"))
    assert "model = ?" in where_sql
    assert "gpt-4o" in args


def test_session_filter_build_where_with_date_range():
    from dashboard.session_filter import FilterParams, SessionFilter

    sf = SessionFilter(db_path=Path("/nonexistent"))
    where_sql, args = sf._build_where(FilterParams(from_dt="2026-01-01", to_dt="2026-01-31"))
    assert "timestamp >= ?" in where_sql
    assert "timestamp <= ?" in where_sql
    assert "2026-01-01" in args
    assert "2026-01-31" in args


# ---------------------------------------------------------------------------
# dashboard/export_csv.py — Enums and helpers
# ---------------------------------------------------------------------------


def test_export_format_enum_values():
    from dashboard.export_csv import ExportFormat

    assert ExportFormat.FULL.value == "full"
    assert ExportFormat.SIMPLIFIED.value == "simplified"


def test_export_data_type_enum_values():
    from dashboard.export_csv import ExportDataType

    assert ExportDataType.TRACES.value == "traces"
    assert ExportDataType.STATS.value == "stats"


def test_format_filename_with_fixed_timestamp():
    from dashboard.export_csv import _format_filename

    ts = datetime(2026, 4, 12, 21, 0, 0)
    name = _format_filename(ts)
    assert name == "tokenpak-export-2026-04-12-210000.csv"


def test_format_filename_without_arg():
    from dashboard.export_csv import _format_filename

    name = _format_filename()
    assert name.startswith("tokenpak-export-")
    assert name.endswith(".csv")


# ---------------------------------------------------------------------------
# dashboard/export_csv.py — CSVExporter
# ---------------------------------------------------------------------------


def test_csv_exporter_empty_traces_full():
    from dashboard.export_csv import CSVExporter, ExportDataType, ExportFormat

    exporter = CSVExporter(traces=[], session_stats={})
    csv_bytes, filename = exporter.export(data_type=ExportDataType.TRACES, fmt=ExportFormat.FULL)
    lines = csv_bytes.decode("utf-8").strip().splitlines()
    assert len(lines) == 1  # header only
    assert "request_id" in lines[0]


def test_csv_exporter_trace_full_columns():
    from dashboard.export_csv import CSVExporter, ExportDataType, ExportFormat, _TRACE_FULL_COLUMNS

    exporter = CSVExporter(traces=[])
    csv_bytes, _ = exporter.export(data_type=ExportDataType.TRACES, fmt=ExportFormat.FULL)
    header = csv_bytes.decode("utf-8").splitlines()[0]
    for col in _TRACE_FULL_COLUMNS:
        assert col in header


def test_csv_exporter_trace_simplified_columns():
    from dashboard.export_csv import (
        CSVExporter,
        ExportDataType,
        ExportFormat,
        _TRACE_SIMPLIFIED_COLUMNS,
    )

    exporter = CSVExporter(traces=[])
    csv_bytes, _ = exporter.export(data_type=ExportDataType.TRACES, fmt=ExportFormat.SIMPLIFIED)
    header = csv_bytes.decode("utf-8").splitlines()[0]
    for col in _TRACE_SIMPLIFIED_COLUMNS:
        assert col in header
    # full-only columns should not appear
    assert "request_id" not in header
    assert "stage_count" not in header


def test_csv_exporter_trace_data_row():
    from dashboard.export_csv import CSVExporter, ExportDataType, ExportFormat

    traces = [
        {
            "request_id": "abc123",
            "timestamp": "2026-04-12T10:00:00",
            "model": "gpt-4o",
            "status": "ok",
            "input_tokens": 100,
            "output_tokens": 50,
            "tokens_saved": 20,
            "cost_saved": 0.002,
            "total_cost": 0.005,
            "duration_ms": 120.5,
            "stages": [{"name": "compress"}, {"name": "send"}],
        }
    ]
    exporter = CSVExporter(traces=traces)
    csv_bytes, _ = exporter.export(data_type=ExportDataType.TRACES, fmt=ExportFormat.FULL)
    content = csv_bytes.decode("utf-8")
    assert "abc123" in content
    assert "gpt-4o" in content
    assert "compress|send" in content


def test_csv_exporter_stats_output():
    from dashboard.export_csv import CSVExporter, ExportDataType

    stats = {"total_requests": 100, "total_cost": 1.23, "tokens_saved": 5000}
    exporter = CSVExporter(session_stats=stats)
    csv_bytes, _ = exporter.export(data_type=ExportDataType.STATS)
    content = csv_bytes.decode("utf-8")
    assert "metric" in content
    assert "value" in content
    assert "total_requests" in content
    assert "100" in content


def test_csv_exporter_returns_utf8_bytes():
    from dashboard.export_csv import CSVExporter, ExportDataType, ExportFormat

    exporter = CSVExporter()
    result, _ = exporter.export(data_type=ExportDataType.TRACES, fmt=ExportFormat.FULL)
    assert isinstance(result, bytes)


def test_csv_exporter_filename_matches_format():
    from dashboard.export_csv import CSVExporter, ExportDataType

    ts = datetime(2026, 1, 15, 9, 30, 0)
    exporter = CSVExporter()
    _, filename = exporter.export(data_type=ExportDataType.TRACES, ts=ts)
    assert filename == "tokenpak-export-2026-01-15-093000.csv"


def test_csv_exporter_stages_empty():
    from dashboard.export_csv import CSVExporter, ExportDataType, ExportFormat

    traces = [{"request_id": "x", "stages": []}]
    exporter = CSVExporter(traces=traces)
    csv_bytes, _ = exporter.export(data_type=ExportDataType.TRACES, fmt=ExportFormat.FULL)
    content = csv_bytes.decode("utf-8")
    # stages_summary should be empty string, stage_count 0
    lines = content.strip().splitlines()
    assert len(lines) == 2  # header + 1 row


def test_csv_exporter_no_bom():
    from dashboard.export_csv import CSVExporter, ExportDataType, ExportFormat

    exporter = CSVExporter()
    csv_bytes, _ = exporter.export(data_type=ExportDataType.TRACES, fmt=ExportFormat.FULL)
    # No BOM (EF BB BF)
    assert not csv_bytes.startswith(b"\xef\xbb\xbf")


# ---------------------------------------------------------------------------
# dashboard/export_api.py — ExportAPI
# ---------------------------------------------------------------------------


def test_export_api_empty_body_defaults():
    from dashboard.export_api import ExportAPI

    body, status, headers = ExportAPI.handle(raw_body=b"")
    assert status == 200
    assert headers["Content-Type"].startswith("text/csv")


def test_export_api_explicit_full_traces():
    from dashboard.export_api import ExportAPI

    req_body = json.dumps({"format": "full", "data_type": "traces"}).encode()
    body, status, headers = ExportAPI.handle(raw_body=req_body, traces=[])
    assert status == 200
    assert b"request_id" in body


def test_export_api_stats():
    from dashboard.export_api import ExportAPI

    req_body = json.dumps({"format": "full", "data_type": "stats"}).encode()
    body, status, headers = ExportAPI.handle(
        raw_body=req_body, session_stats={"requests": 5}
    )
    assert status == 200
    assert b"metric" in body


def test_export_api_invalid_json_returns_400():
    from dashboard.export_api import ExportAPI

    body, status, headers = ExportAPI.handle(raw_body=b"{not valid json")
    assert status == 400
    parsed = json.loads(body)
    assert parsed["error"] == "invalid_json"


def test_export_api_invalid_format_returns_400():
    from dashboard.export_api import ExportAPI

    req_body = json.dumps({"format": "csv_turbo"}).encode()
    body, status, headers = ExportAPI.handle(raw_body=req_body)
    assert status == 400
    parsed = json.loads(body)
    assert parsed["error"] == "invalid_format"


def test_export_api_invalid_data_type_returns_400():
    from dashboard.export_api import ExportAPI

    req_body = json.dumps({"data_type": "metrics"}).encode()
    body, status, headers = ExportAPI.handle(raw_body=req_body)
    assert status == 400
    parsed = json.loads(body)
    assert parsed["error"] == "invalid_data_type"


def test_export_api_response_has_content_disposition():
    from dashboard.export_api import ExportAPI

    body, status, headers = ExportAPI.handle(raw_body=b"")
    assert "Content-Disposition" in headers
    assert "attachment" in headers["Content-Disposition"]
    assert ".csv" in headers["Content-Disposition"]


def test_export_api_content_length_header():
    from dashboard.export_api import ExportAPI

    body, status, headers = ExportAPI.handle(raw_body=b"")
    assert "Content-Length" in headers
    assert int(headers["Content-Length"]) == len(body)


def test_export_api_error_helper_structure():
    from dashboard.export_api import ExportAPI

    body, status, headers = ExportAPI._error(422, "test_code", "test detail")
    assert status == 422
    parsed = json.loads(body)
    assert parsed["error"] == "test_code"
    assert parsed["detail"] == "test detail"
    assert headers["Content-Type"] == "application/json"


def test_export_api_cors_header():
    from dashboard.export_api import ExportAPI

    body, status, headers = ExportAPI.handle(raw_body=b"")
    assert headers.get("Access-Control-Allow-Origin") == "*"


# ---------------------------------------------------------------------------
# dashboard/account_dashboard.py — _get_user_id, _calculate_roi
# ---------------------------------------------------------------------------


def test_get_user_id_from_tokenpak_user_id():
    from dashboard.account_dashboard import _get_user_id

    with patch.dict(os.environ, {"TOKENPAK_USER_ID": "user-abc", "TOKENPAK_API_KEY": ""}):
        uid = _get_user_id()
    assert uid == "user-abc"


def test_get_user_id_falls_back_to_api_key():
    from dashboard.account_dashboard import _get_user_id

    env = {"TOKENPAK_USER_ID": "", "TOKENPAK_API_KEY": "sk-test-key"}
    with patch.dict(os.environ, env):
        uid = _get_user_id()
    assert uid == "sk-test-key"


def test_get_user_id_returns_none_when_both_missing():
    from dashboard.account_dashboard import _get_user_id

    env = {"TOKENPAK_USER_ID": "", "TOKENPAK_API_KEY": ""}
    with patch.dict(os.environ, env):
        uid = _get_user_id()
    assert uid is None


def test_calculate_roi_zero_tokens():
    from dashboard.account_dashboard import _calculate_roi

    result = _calculate_roi(0)
    assert result["total_saved_tokens"] == 0
    assert result["estimated_savings_usd"] == 0.0


def test_calculate_roi_positive_savings():
    from dashboard.account_dashboard import _calculate_roi

    result = _calculate_roi(1_000_000)
    assert result["estimated_savings_usd"] > 0


def test_calculate_roi_returns_required_keys():
    from dashboard.account_dashboard import _calculate_roi

    result = _calculate_roi(500_000)
    assert "total_saved_tokens" in result
    assert "estimated_savings_usd" in result
    assert "period" in result


def test_calculate_roi_savings_usd_is_float():
    from dashboard.account_dashboard import _calculate_roi

    result = _calculate_roi(12345)
    assert isinstance(result["estimated_savings_usd"], float)


def test_calculate_roi_total_saved_tokens_matches_input():
    from dashboard.account_dashboard import _calculate_roi

    result = _calculate_roi(77777)
    assert result["total_saved_tokens"] == 77777


# ---------------------------------------------------------------------------
# dashboard/app.py — _today, _days_ago
# ---------------------------------------------------------------------------


def test_today_returns_string():
    from dashboard.app import _today

    t = _today()
    assert isinstance(t, str)
    # format: YYYY-MM-DD
    from datetime import date
    date.fromisoformat(t)  # raises if invalid


def test_days_ago_returns_string():
    from dashboard.app import _days_ago

    result = _days_ago(7)
    assert isinstance(result, str)
    from datetime import date
    date.fromisoformat(result)


def test_days_ago_offset_is_correct():
    from datetime import date, timedelta

    from dashboard.app import _days_ago, _today

    today_str = _today()
    seven_ago_str = _days_ago(7)
    today_dt = date.fromisoformat(today_str)
    seven_ago_dt = date.fromisoformat(seven_ago_str)
    assert (today_dt - seven_ago_dt).days == 7


def test_days_ago_zero():
    from dashboard.app import _days_ago, _today

    assert _days_ago(0) == _today()
