# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokenpak import _paths
from tokenpak.debug import capture as cap
from tokenpak.debug._diagnostic_report import (
    SCHEMA_VERSION,
    DiagnosticReportError,
    diagnostic_receipts_dir,
    diagnostic_reports_dir,
    diagnostic_store_dir,
    sanitize_diagnostic_report,
    validate_sanitized_report,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv(_paths.ENV_VAR, raising=False)
    return tmp_path


def test_debug_home_paths_are_resolved_through_paths(fake_home):
    assert diagnostic_store_dir() == _paths.resolved_home() / "debug" / "store"
    assert diagnostic_reports_dir() == _paths.resolved_home() / "debug" / "reports"
    assert diagnostic_receipts_dir() == _paths.resolved_home() / "debug" / "receipts"


def test_sanitizer_projects_allowlisted_report_and_manifest(fake_home):
    raw = {
        "runtime": {
            "tokenpak_version": "1.2.3",
            "python_version": "3.12.1",
            "os_family": "Linux",
            "arch": "x86_64",
            "install_source": "wheel",
        },
        "command": {
            "group": "debug",
            "subcommand": "report",
            "exit_code": 1,
            "duration_ms": 1420,
            "flags": {"json": True, "path": "/home/alice/project"},
        },
        "error": {
            "exception_class": "RuntimeError",
            "message": "failed at /home/alice/project/app.py with sk-abcdefghijklmnopqrstuv",
            "stack": ["/home/alice/project/app.py:10 in run"],
        },
        "logs": [
            "open /home/alice/project/app.py",
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
        ],
        "config": {"debug": True, "api_key": "sk-secret-value"},
        "store": {"record_count": 2, "size_bytes": 100},
    }

    report = sanitize_diagnostic_report(raw, report_id="diag_test")
    validate_sanitized_report(report)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["report_id"] == "diag_test"
    assert report["command"]["duration_bucket"] == "lt_10s"
    rendered = json.dumps(report)
    assert "/home/alice" not in rendered
    assert "sk-secret-value" not in rendered
    assert "abcdefghijklmnopqrstuvwxyz123456" not in rendered
    assert "<home>" in rendered
    manifest = report["sanitizer_manifest"]
    assert "runtime" in manifest["retained_sections"]
    assert manifest["path_normalization_count"] >= 1
    assert manifest["redaction_counts"]
    assert manifest["report_sha256"]


@pytest.mark.parametrize(
    "unsafe_key",
    ["prompt", "completion", "messages", "request", "response", "body", "payload"],
)
def test_hard_reject_keys_are_not_retained(unsafe_key, fake_home):
    raw = {
        "runtime": {"tokenpak_version": "1.2.3"},
        "logs": [{"message": "safe line", unsafe_key: "user content"}],
    }

    report = sanitize_diagnostic_report(raw)
    validate_sanitized_report(report)
    rendered = json.dumps(report)
    assert "user content" not in rendered
    assert unsafe_key not in rendered
    assert {"section": "logs[0]", "reason": "hard_reject_key"} in report["sanitizer_manifest"][
        "omitted_sections"
    ]


def test_raw_env_dump_is_rejected(fake_home):
    raw = {
        "runtime": {"tokenpak_version": "1.2.3"},
        "config": {"env": {"OPENAI_API_KEY": "sk-abcdefghijklmnopqrstuv"}},
    }

    report = sanitize_diagnostic_report(raw)
    validate_sanitized_report(report)
    rendered = json.dumps(report)
    assert "OPENAI_API_KEY" not in rendered
    assert "abcdefghijklmnopqrstuv" not in rendered


def test_empty_after_sanitization_fails_closed(fake_home):
    with pytest.raises(DiagnosticReportError, match="no safe sections"):
        sanitize_diagnostic_report(
            {
                "request": {"prompt": "hello"},
                "response": {"completion": "world"},
            }
        )


def test_encrypted_capture_export_is_not_direct_report_payload(tmp_path, monkeypatch):
    blob_dir = tmp_path / "debug"
    key = b"1" * 32
    monkeypatch.setattr(cap, "_BLOB_DIR", blob_dir)
    monkeypatch.setattr(cap, "_KEY_FILE", blob_dir / ".key")
    cap.capture(
        "trace-unsafe",
        {"prompt": "summarize this private text"},
        {"completion": "private answer"},
        key=key,
        mode=cap.CaptureMode.ENCRYPTED,
    )

    exported = cap.export_capture("trace-unsafe", key=key)
    with pytest.raises(DiagnosticReportError, match="no safe sections"):
        sanitize_diagnostic_report(exported)
