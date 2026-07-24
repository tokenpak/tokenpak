"""Tests for enterprise audit logging and compliance reports.

Covers:
- AuditLog record, list, filter, export (JSON + CSV), verify chain
- Retention pruning
- Hash chain tamper detection
- CLI commands: audit list, export, verify, prune, summary
- ComplianceReporter: SOC2, GDPR, CCPA reports
- CLI commands: compliance report
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "tokenpak.enterprise.compliance", reason="module not available in current build"
)
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tokenpak.enterprise.audit import AuditLog, _parse_date
from tokenpak.enterprise.compliance import ComplianceReporter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def log():
    """In-memory audit log for testing."""
    return AuditLog(":memory:")


@pytest.fixture
def log_with_entries(log):
    """Audit log pre-populated with a handful of entries."""
    log.record(
        "proxy_request",
        user_id="alice",
        model="openai/gpt-4o",
        provider="openai",
        data_classification="internal",
    )
    log.record(
        "auth_failure", user_id="mallory", outcome="auth_failure", metadata={"ip": "10.0.0.99"}
    )
    log.record("config_change", user_id="admin", metadata={"key": "retention_days", "value": 60})
    log.record(
        "proxy_request",
        user_id="alice",
        model="anthropic/claude-3-5-sonnet",
        provider="anthropic",
        data_classification="confidential",
    )
    log.record("data_export", user_id="bob", outcome="ok", metadata={"format": "json", "rows": 42})
    return log


# ---------------------------------------------------------------------------
# AuditLog — basic record / list
# ---------------------------------------------------------------------------


def test_record_returns_id(log):
    entry_id = log.record("proxy_request", user_id="alice")
    assert isinstance(entry_id, str) and len(entry_id) == 36  # UUID


def test_list_all(log_with_entries):
    rows = log_with_entries.list(limit=100)
    assert len(rows) == 5


def test_list_filter_user(log_with_entries):
    rows = log_with_entries.list(user_id="alice")
    assert len(rows) == 2
    assert all(r["user_id"] == "alice" for r in rows)


def test_list_filter_action(log_with_entries):
    rows = log_with_entries.list(action="proxy_request")
    assert len(rows) == 2


def test_list_filter_outcome(log_with_entries):
    rows = log_with_entries.list(outcome="auth_failure")
    assert len(rows) == 1
    assert rows[0]["user_id"] == "mallory"


def test_list_filter_model(log_with_entries):
    rows = log_with_entries.list(model="openai/gpt-4o")
    assert len(rows) == 1


def test_list_limit(log_with_entries):
    rows = log_with_entries.list(limit=2)
    assert len(rows) == 2


def test_list_offset(log_with_entries):
    all_rows = log_with_entries.list(limit=100)
    offset_rows = log_with_entries.list(offset=2, limit=100)
    assert len(offset_rows) == len(all_rows) - 2


def test_count(log_with_entries):
    assert log_with_entries.count() == 5
    assert log_with_entries.count(user_id="alice") == 2


# ---------------------------------------------------------------------------
# AuditLog — metadata
# ---------------------------------------------------------------------------


def test_metadata_round_trip(log):
    meta = {"tokens": 123, "tags": ["a", "b"], "nested": {"k": "v"}}
    log.record("proxy_request", metadata=meta)
    rows = log.list()
    assert rows[0]["metadata"] == meta


# ---------------------------------------------------------------------------
# AuditLog — hash chain integrity
# ---------------------------------------------------------------------------


def test_verify_chain_empty(log):
    ok, errors = log.verify_chain()
    assert ok and errors == []


def test_verify_chain_valid(log_with_entries):
    ok, errors = log_with_entries.verify_chain()
    assert ok, f"Chain invalid: {errors}"


def test_verify_chain_tampered(log_with_entries):
    """Directly corrupt a row and expect chain failure detection."""
    log_with_entries._conn.execute(
        "UPDATE tp_audit_log SET action='TAMPERED' WHERE user_id='alice' LIMIT 1"
    )
    log_with_entries._conn.commit()
    ok, errors = log_with_entries.verify_chain()
    assert not ok
    assert len(errors) > 0


# ---------------------------------------------------------------------------
# AuditLog — export (JSON + CSV)
# ---------------------------------------------------------------------------


def test_export_json(log_with_entries, tmp_path):
    out = tmp_path / "audit.json"
    n = log_with_entries.export(str(out), fmt="json")
    assert n == 5
    data = json.loads(out.read_text())
    assert len(data) == 5
    assert "user_id" in data[0]


def test_export_csv(log_with_entries, tmp_path):
    out = tmp_path / "audit.csv"
    n = log_with_entries.export(str(out), fmt="csv")
    assert n == 5
    rows = list(csv.DictReader(out.read_text().splitlines()))
    assert len(rows) == 5
    assert "user_id" in rows[0]


def test_export_unsupported_format(log_with_entries, tmp_path):
    with pytest.raises(ValueError, match="Unsupported format"):
        log_with_entries.export(str(tmp_path / "x.xml"), fmt="xml")


def test_export_empty(log, tmp_path):
    out = tmp_path / "empty.json"
    n = log.export(str(out), fmt="json")
    assert n == 0
    assert json.loads(out.read_text()) == []


# ---------------------------------------------------------------------------
# AuditLog — retention pruning
# ---------------------------------------------------------------------------


def test_prune_old_entries(log):
    log.record("proxy_request", user_id="alice")
    # Force a very old timestamp directly
    log._conn.execute("UPDATE tp_audit_log SET ts=1 WHERE user_id='alice'")
    log._conn.commit()
    removed = log.prune(retention_days=1)
    assert removed == 1
    assert log.count() == 0


def test_prune_keeps_recent(log):
    log.record("proxy_request", user_id="alice")
    removed = log.prune(retention_days=90)
    assert removed == 0
    assert log.count() == 1


# ---------------------------------------------------------------------------
# AuditLog — summary
# ---------------------------------------------------------------------------


def test_summary_total(log_with_entries):
    stats = log_with_entries.summary()
    assert stats["total"] == 5


def test_summary_by_action(log_with_entries):
    stats = log_with_entries.summary()
    actions = {r["action"] if isinstance(r, dict) else r[0] for r in stats["by_action"]}
    assert "proxy_request" in actions


# ---------------------------------------------------------------------------
# ComplianceReporter
# ---------------------------------------------------------------------------


@pytest.fixture
def reporter(tmp_path):
    """ComplianceReporter backed by a temp in-memory audit log."""
    db = str(tmp_path / "audit.db")
    with AuditLog(db) as al:
        al.record(
            "proxy_request", user_id="alice", model="openai/gpt-4o", data_classification="internal"
        )
        al.record("auth_failure", user_id="mallory", outcome="auth_failure")
        al.record("config_change", user_id="admin")
    return ComplianceReporter(audit_db=db, organization="Acme Corp")


def test_soc2_report(reporter):
    report = reporter.generate("soc2")
    assert report.standard == "soc2"
    assert len(report.controls) > 0
    summary = report.summary
    assert summary["total_controls"] == len(report.controls)
    assert "compliant" in summary


def test_gdpr_report(reporter):
    report = reporter.generate("gdpr")
    assert report.standard == "gdpr"
    assert any(c.id.startswith("GDPR") for c in report.controls)


def test_ccpa_report(reporter):
    report = reporter.generate("ccpa")
    assert report.standard == "ccpa"
    assert any(c.id.startswith("CCPA") for c in report.controls)


def test_unknown_standard(reporter):
    with pytest.raises(ValueError, match="Unknown compliance standard"):
        reporter.generate("hipaa")


def test_report_as_text(reporter):
    report = reporter.generate("soc2")
    text = report.as_text()
    assert "SOC2" in text
    assert "Acme Corp" in text
    assert "compliant" in text.lower() or "✅" in text


def test_report_as_json(reporter):
    report = reporter.generate("gdpr")
    data = json.loads(report.as_json())
    assert data["standard"] == "gdpr"
    assert "controls" in data
    assert "summary" in data


def test_report_save_json(reporter, tmp_path):
    report = reporter.generate("ccpa")
    out = tmp_path / "ccpa.json"
    report.save(str(out), fmt="json")
    data = json.loads(out.read_text())
    assert data["standard"] == "ccpa"


def test_report_save_text(reporter, tmp_path):
    report = reporter.generate("soc2")
    out = tmp_path / "soc2.txt"
    report.save(str(out), fmt="text")
    assert "SOC2" in out.read_text()


# ---------------------------------------------------------------------------
# _parse_date helper
# ---------------------------------------------------------------------------


def test_parse_date_iso():
    ts = _parse_date("2026-01-01")
    assert ts > 0


def test_parse_date_iso_datetime():
    ts = _parse_date("2026-01-01T00:00:00")
    assert ts > 0


def test_parse_date_invalid():
    with pytest.raises(ValueError, match="Cannot parse date"):
        _parse_date("not-a-date")


# ---------------------------------------------------------------------------
# CLI integration — audit commands
# ---------------------------------------------------------------------------


def _run_cli(*args):
    """Run CLI and capture stdout."""
    import io
    from contextlib import redirect_stdout

    from tokenpak.cli import build_parser

    parser = build_parser()
    parsed = parser.parse_args(list(args))
    buf = io.StringIO()
    with redirect_stdout(buf):
        parsed.func(parsed)
    return buf.getvalue()


def test_cli_audit_list_empty(tmp_path):
    db = str(tmp_path / "audit.db")
    out = _run_cli("audit", "list", "--db", db)
    assert "No audit entries" in out


def test_cli_audit_summary_empty(tmp_path):
    db = str(tmp_path / "audit.db")
    out = _run_cli("audit", "summary", "--db", db)
    assert "0" in out


def test_cli_audit_verify_empty(tmp_path):
    db = str(tmp_path / "audit.db")
    out = _run_cli("audit", "verify", "--db", db)
    assert "OK" in out


def test_cli_compliance_report_soc2_text(tmp_path):
    db = str(tmp_path / "audit.db")
    out = _run_cli("compliance", "report", "--standard", "soc2", "--db", db, "--format", "text")
    assert "SOC2" in out
    assert "compliant" in out.lower() or "✅" in out


def test_cli_compliance_report_gdpr_json(tmp_path):
    db = str(tmp_path / "audit.db")
    out = _run_cli("compliance", "report", "--standard", "gdpr", "--db", db, "--format", "json")
    data = json.loads(out)
    assert data["standard"] == "gdpr"
