# SPDX-License-Identifier: Apache-2.0
"""Tests for the ``tokenpak dispatch`` CLI (P-CLI-01).

Covers Standards Delta v0 §14.1 (every verb), §14.2 (default autonomy by
caller / flag combinations on ``run``), the Decision Inbox MVP
(``decisions`` / ``approve`` / ``reject``), the ``--json`` output mode, and the
§11 verification gate (no ``Fleet Worker`` string in any formatted output).

Commands are driven in-process via the argparse builder + handler functions
(faster, exception-traceable). Every test points ``TOKENPAK_HOME`` at a tmp dir
so the real ``~/.tpk/`` is never touched. A real-entry-point smoke test at the
bottom exercises ``python -m tokenpak.cli.main dispatch`` once end-to-end.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest

pytest.importorskip("pydantic")

from tokenpak.cli.commands.dispatch_cmd import (  # noqa: E402
    build_dispatch_parser,
    cmd_dispatch_approve,
    cmd_dispatch_cancel,
    cmd_dispatch_decisions,
    cmd_dispatch_delivery,
    cmd_dispatch_discard_late,
    cmd_dispatch_inspect,
    cmd_dispatch_pause,
    cmd_dispatch_receipt,
    cmd_dispatch_reject,
    cmd_dispatch_resume,
    cmd_dispatch_run,
    cmd_dispatch_status,
)

_NOW = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point TOKENPAK_HOME at a tmp dir so the CLI never touches ~/.tpk/."""
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    return tmp_path


def _parser():
    parser = argparse.ArgumentParser(prog="tokenpak")
    sub = parser.add_subparsers(dest="command")
    build_dispatch_parser(sub)
    return parser


def _capture(handler, args) -> tuple[int, str]:
    """Run a handler, capture stdout, return (exit_code, stdout)."""
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        rc = handler(args)
    finally:
        sys.stdout = saved
    return int(rc or 0), buf.getvalue()


def _run_args(request, **over):
    base = dict(
        request=request,
        route=None,
        autonomy=None,
        ci=False,
        dry_run=False,
        confirm=False,
        as_json=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _do_run(request, **over):
    """Helper: run intake+route for *request*, return (rc, stdout, payload)."""
    args = _run_args(request, as_json=True, **over)
    rc, out = _capture(cmd_dispatch_run, args)
    return rc, out, json.loads(out)


# ---------------------------------------------------------------------------
# Parser registration (§14.1 — all twelve verbs)
# ---------------------------------------------------------------------------


_VERBS = [
    "run",
    "status",
    "inspect",
    "decisions",
    "approve",
    "reject",
    "pause",
    "resume",
    "cancel",
    "discard-late",
    "delivery",
    "receipt",
]


def test_parser_registers_all_verbs():
    parser = _parser()
    # The dispatch subparser action holds the verb choices.
    dispatch_action = next(
        a
        for a in parser._subparsers._group_actions  # type: ignore[attr-defined]
        if a.dest == "command"
    )
    dispatch_sub = dispatch_action.choices["dispatch"]
    verb_action = next(
        a
        for a in dispatch_sub._subparsers._group_actions  # type: ignore[attr-defined]
        if a.dest == "dispatch_action"
    )
    for verb in _VERBS:
        assert verb in verb_action.choices, f"missing verb: {verb}"


def test_parse_run_with_flags():
    parser = _parser()
    ns = parser.parse_args(
        [
            "dispatch",
            "run",
            "do a thing",
            "--route=code_task",
            "--autonomy=advisory",
            "--json",
        ]
    )
    assert ns.request == "do a thing"
    assert ns.route == "code_task"
    assert ns.autonomy == "advisory"
    assert ns.as_json is True


# ---------------------------------------------------------------------------
# run — default autonomy by caller (§14.2)
# ---------------------------------------------------------------------------


def test_run_bare_cli_default_autonomy(home):
    _, _, p = _do_run("write a python function to read a file")
    assert p["autonomy_mode"] == "dispatch_with_approval"


def test_run_ci_flag_autonomy(home):
    _, _, p = _do_run("write a python function", ci=True)
    assert p["autonomy_mode"] == "auto_dispatch_limited"


def test_run_dry_run_flag_autonomy(home):
    _, _, p = _do_run("write a python function", dry_run=True)
    assert p["autonomy_mode"] == "draft"


def test_run_explicit_autonomy_wins(home):
    # explicit --autonomy beats --ci and --dry-run
    _, _, p = _do_run("write a function", autonomy="advisory", ci=True, dry_run=True)
    assert p["autonomy_mode"] == "advisory"


def test_run_explicit_route(home):
    _, _, p = _do_run("answer a quick question", route="code_task")
    assert p["route_id"] == "route.code_task.v1"


def test_run_persists_job_to_ledger(home):
    _, _, p = _do_run("write a python function to parse a file")
    job_id = p["job_id"]
    # status should now read the persisted job
    rc, out = _capture(
        cmd_dispatch_status,
        argparse.Namespace(job_id=job_id, as_json=True),
    )
    assert rc == 0
    status = json.loads(out)
    assert status["job_id"] == job_id
    assert status["detected_intent"] == "code_task"


def test_run_highrisk_creates_decisions(home):
    _, _, p = _do_run("delete all the production secrets and drop the database")
    # FrontDock blocking gap + route-selection decision both filed in the inbox.
    assert len(p["decision_ids"]) >= 1
    assert p["selection_status"] in {"decision", "refused"}


def test_run_human_output_smoke(home):
    rc, out = _capture(cmd_dispatch_run, _run_args("write a python function"))
    assert rc == 0
    assert "Dispatch run" in out
    assert "Route" in out


# ---------------------------------------------------------------------------
# Decision Inbox: decisions / approve / reject (§13 item 17)
# ---------------------------------------------------------------------------


def _seed_decision(home):
    _, _, p = _do_run("delete the production database now")
    assert p["decision_ids"], "expected at least one decision to be filed"
    return p["job_id"], p["decision_ids"][0]


def test_decisions_list_empty(home):
    rc, out = _capture(cmd_dispatch_decisions, argparse.Namespace(job=None, as_json=False))
    assert rc == 0
    assert "empty" in out.lower()


def test_decisions_list_and_cards(home):
    job_id, _ = _seed_decision(home)
    rc, out = _capture(cmd_dispatch_decisions, argparse.Namespace(job=job_id, as_json=False))
    assert rc == 0
    assert "Decision Inbox" in out
    assert "Options:" in out


def test_decisions_list_json(home):
    job_id, _ = _seed_decision(home)
    rc, out = _capture(cmd_dispatch_decisions, argparse.Namespace(job=job_id, as_json=True))
    assert rc == 0
    payload = json.loads(out)
    assert payload["count"] >= 1
    card = payload["decisions"][0]
    assert card["scope"] in {"job", "station"}
    assert "options" in card and card["options"]
    assert "recommendation" in card


def test_decision_scopes_are_job_or_station_only(home):
    """v0.1-alpha decision scopes: job, station — NO branch scope (§13 item 4)."""
    job_id, _ = _seed_decision(home)
    rc, out = _capture(cmd_dispatch_decisions, argparse.Namespace(job=job_id, as_json=True))
    payload = json.loads(out)
    for card in payload["decisions"]:
        assert card["scope"] in {"job", "station"}
        assert card["scope"] != "branch"


def test_approve_decision(home):
    _, decision_id = _seed_decision(home)
    rc, out = _capture(
        cmd_dispatch_approve,
        argparse.Namespace(decision_id=decision_id, option=None, as_json=True),
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["status"] == "resolved"
    assert payload["selected_option_id"]  # defaulted to the recommended option


def test_reject_decision(home):
    _, decision_id = _seed_decision(home)
    rc, out = _capture(
        cmd_dispatch_reject,
        argparse.Namespace(decision_id=decision_id, as_json=True),
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["status"] == "cancelled"


def test_approve_already_resolved_is_error(home):
    _, decision_id = _seed_decision(home)
    _capture(
        cmd_dispatch_approve,
        argparse.Namespace(decision_id=decision_id, option=None, as_json=True),
    )
    rc, out = _capture(
        cmd_dispatch_approve,
        argparse.Namespace(decision_id=decision_id, option=None, as_json=True),
    )
    assert rc == 1
    assert json.loads(out)["error"] == "decision_not_pending"


def test_approve_unknown_option_is_error(home):
    _, decision_id = _seed_decision(home)
    rc, out = _capture(
        cmd_dispatch_approve,
        argparse.Namespace(decision_id=decision_id, option="nope", as_json=True),
    )
    assert rc == 1
    assert json.loads(out)["error"] == "unknown_option"


def test_approve_missing_decision_is_error(home):
    rc, out = _capture(
        cmd_dispatch_approve,
        argparse.Namespace(decision_id="decision_nope", option=None, as_json=True),
    )
    assert rc == 1
    assert json.loads(out)["error"] == "decision_not_found"


# ---------------------------------------------------------------------------
# status / inspect
# ---------------------------------------------------------------------------


def test_status_missing_job(home):
    rc, out = _capture(
        cmd_dispatch_status,
        argparse.Namespace(job_id="job_nope", as_json=True),
    )
    assert rc == 1
    assert json.loads(out)["error"] == "job_not_found"


def test_inspect_job(home):
    _, _, p = _do_run("write a python function")
    rc, out = _capture(
        cmd_dispatch_inspect,
        argparse.Namespace(job_id=p["job_id"], late=False, as_json=True),
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["job"]["id"] == p["job_id"]
    assert payload["manifests"]


def test_inspect_with_late_flag(home):
    _, _, p = _do_run("write a python function")
    rc, out = _capture(
        cmd_dispatch_inspect,
        argparse.Namespace(job_id=p["job_id"], late=True, as_json=True),
    )
    assert rc == 0
    payload = json.loads(out)
    assert "late_results" in payload


# ---------------------------------------------------------------------------
# pause / resume / cancel / discard-late
# ---------------------------------------------------------------------------


def test_pause_resume_does_not_corrupt_job(home):
    """pause/resume are CLI control states; they must NOT poison the §6 enum."""
    _, _, p = _do_run("write a python function")
    job_id = p["job_id"]

    rc, out = _capture(cmd_dispatch_pause, argparse.Namespace(job_id=job_id, as_json=True))
    assert rc == 0
    assert json.loads(out)["control_state"] == "paused"

    # The job is still readable (enum not corrupted) and reports paused control.
    rc, out = _capture(cmd_dispatch_status, argparse.Namespace(job_id=job_id, as_json=True))
    assert rc == 0
    assert json.loads(out)["control_state"] == "paused"

    rc, out = _capture(cmd_dispatch_resume, argparse.Namespace(job_id=job_id, as_json=True))
    assert rc == 0
    assert json.loads(out)["control_state"] == "active"


def test_cancel_job(home):
    _, _, p = _do_run("write a python function")
    job_id = p["job_id"]
    rc, out = _capture(cmd_dispatch_cancel, argparse.Namespace(job_id=job_id, as_json=True))
    assert rc == 0
    assert json.loads(out)["status"] == "cancelled"

    # status reflects the cancelled state and the job still validates.
    rc, out = _capture(cmd_dispatch_status, argparse.Namespace(job_id=job_id, as_json=True))
    assert rc == 0
    assert json.loads(out)["status"] == "cancelled"


def test_cancel_missing_job(home):
    rc, out = _capture(cmd_dispatch_cancel, argparse.Namespace(job_id="job_nope", as_json=True))
    assert rc == 1
    assert json.loads(out)["error"] == "job_not_found"


def test_discard_late_none(home):
    rc, out = _capture(
        cmd_dispatch_discard_late,
        argparse.Namespace(station_run_id="stationrun_nope", as_json=True),
    )
    assert rc == 1
    assert json.loads(out)["error"] == "no_late_results"


def test_discard_late_removes_record(home):
    # Seed a late result directly via the ledger, then discard it.
    from tokenpak.orchestration.dispatch.ledger.db import RunLedger
    from tokenpak.orchestration.dispatch.models import LateResult

    led = RunLedger()
    try:
        led.write_late_result(
            LateResult(
                id="late_01",
                job_id="job_x",
                station_run_id="stationrun_01",
                received_at=_NOW,
                result_hash="abc",
            )
        )
    finally:
        led.close()

    rc, out = _capture(
        cmd_dispatch_discard_late,
        argparse.Namespace(station_run_id="stationrun_01", as_json=True),
    )
    assert rc == 0
    assert "late_01" in json.loads(out)["discarded"]


# ---------------------------------------------------------------------------
# delivery / receipt (+ Std 36 public-safe sanitization, §10)
# ---------------------------------------------------------------------------


def test_delivery_view(home):
    _, _, p = _do_run("write a python function")
    rc, out = _capture(
        cmd_dispatch_delivery,
        argparse.Namespace(job_id=p["job_id"], as_json=True),
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["job_id"] == p["job_id"]
    assert payload["delivered"] is False


def test_receipt_missing(home):
    _, _, p = _do_run("write a python function")
    rc, out = _capture(
        cmd_dispatch_receipt,
        argparse.Namespace(job_id=p["job_id"], as_json=True),
    )
    assert rc == 1
    assert json.loads(out)["error"] == "no_receipt"


# Preview-honesty: receipt / delivery / inspect must explain *why* a receipt is
# absent in the v0.1-alpha preview (station execution is not CLI-wired), so an
# empty receipt does not read as a defect. Narrow preview-honesty contract — it
# does NOT imply station execution is wired.
_PREVIEW_NOTE_MARK = "no receipt is produced in this build"


def test_receipt_missing_explains_preview_boundary(home):
    _, _, p = _do_run("write a python function")
    rc, out = _capture(
        cmd_dispatch_receipt,
        argparse.Namespace(job_id=p["job_id"], as_json=True),
    )
    assert rc == 1
    payload = json.loads(out)
    assert payload["error"] == "no_receipt"
    assert _PREVIEW_NOTE_MARK in payload["detail"]


def test_delivery_undelivered_carries_preview_note(home):
    _, _, p = _do_run("write a python function")
    rc, out = _capture(
        cmd_dispatch_delivery,
        argparse.Namespace(job_id=p["job_id"], as_json=True),
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["delivered"] is False
    assert _PREVIEW_NOTE_MARK in payload["note"]


def test_inspect_no_receipt_carries_preview_note(home):
    _, _, p = _do_run("write a python function")
    rc, out = _capture(
        cmd_dispatch_inspect,
        argparse.Namespace(job_id=p["job_id"], late=False, as_json=True),
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["receipts"] == []
    assert _PREVIEW_NOTE_MARK in payload["note"]


def _seed_receipt(home, *, excerpt: str):
    """Persist a job + receipt whose excerpt carries internal-name leakage."""
    from tokenpak.orchestration.dispatch.ledger.db import RunLedger
    from tokenpak.orchestration.dispatch.models import (
        DispatchJob,
        DispatchReceipt,
    )
    from tokenpak.orchestration.dispatch.models.receipt import ReceiptStation

    led = RunLedger()
    try:
        led.write_job(
            DispatchJob(
                id="job_rcpt",
                created_at=_NOW,
                raw_request="do a thing",
                detected_intent="code_task",
                autonomy_mode="dispatch_with_approval",
                status="delivered",
            )
        )
        led.write_receipt(
            DispatchReceipt(
                id="receipt_01",
                job_id="job_rcpt",
                run_id="run_01",
                route_id="route.code_task.v1",
                stations=[
                    ReceiptStation(
                        station_run_id="stationrun_01",
                        worker_id="worker.builder.default.v1",
                        status="completed",
                        result_payload_excerpt=excerpt,
                    )
                ],
                final_status="delivered",
                created_at=_NOW,
            )
        )
    finally:
        led.close()


def test_receipt_renders(home):
    _seed_receipt(home, excerpt="produced a clean patch")
    rc, out = _capture(
        cmd_dispatch_receipt,
        argparse.Namespace(job_id="job_rcpt", as_json=False),
    )
    assert rc == 0
    assert "Receipt receipt_01" in out
    assert "Worker worker.builder.default.v1" in out


def test_receipt_is_public_safe_sanitized(home):
    """Receipt output is run through the public-safe path (§10).

    Home paths and internal task-ID-shaped tokens planted in the excerpt must be
    redacted by default in both JSON and human-readable output. (Agent-name
    redaction is caller-injectable via ``extra_terms`` — exercised directly in
    :func:`test_sanitizer_extra_terms_redacts_injected_names` — so it is not
    asserted here at the CLI surface, which passes no extra terms by default.)
    """
    leaky = "Reviewed /home/operator/secret/path and approved task TSR-1234"
    _seed_receipt(home, excerpt=leaky)

    # JSON path
    rc, out = _capture(
        cmd_dispatch_receipt,
        argparse.Namespace(job_id="job_rcpt", as_json=True),
    )
    assert rc == 0
    assert "/home/operator/" not in out
    assert "TSR-1234" not in out
    assert "[redacted]" in out

    # Human path
    rc, out = _capture(
        cmd_dispatch_receipt,
        argparse.Namespace(job_id="job_rcpt", as_json=False),
    )
    assert rc == 0
    assert "/home/operator/" not in out
    assert "TSR-1234" not in out


def test_sanitizer_extra_terms_redacts_injected_names():
    """The sanitizer redacts paths + id-shaped tokens by default, and redacts
    caller-supplied ``extra_terms`` (e.g. internal agent names) on top of that.

    This is where a non-public caller would inject internal names; the
    open-source default carries none, so they must be passed explicitly.
    """
    from tokenpak.orchestration.dispatch.public_safe import (
        sanitize_public_text,
    )

    leaky = "Sue reviewed /home/sue/secret/path and approved task TSR-1234"

    # Default: paths + id-shaped tokens redacted; injected name still present.
    default_out = sanitize_public_text(leaky)
    assert "/home/sue/" not in default_out
    assert "TSR-1234" not in default_out
    assert "[redacted]" in default_out
    assert "Sue" in default_out  # not redacted without extra_terms

    # With extra_terms: the injected name is redacted too.
    injected_out = sanitize_public_text(leaky, extra_terms=["Sue"])
    assert "Sue" not in injected_out
    assert "/home/sue/" not in injected_out
    assert "TSR-1234" not in injected_out


# ---------------------------------------------------------------------------
# §11 verification gate — no "Fleet Worker" anywhere in formatted output
# ---------------------------------------------------------------------------


def test_no_fleet_worker_string_in_any_output(home):
    """No Dispatch surface may emit the literal string 'Fleet Worker' (§11)."""
    outputs: list[str] = []

    # run (human + json)
    outputs.append(_capture(cmd_dispatch_run, _run_args("write a python function"))[1])
    _, jrun = _capture(cmd_dispatch_run, _run_args("write a python function", as_json=True))
    outputs.append(jrun)
    job_id = json.loads(jrun)["job_id"]

    # high-risk run to seed decisions
    _, jrisk = _capture(
        cmd_dispatch_run,
        _run_args("delete the production database", as_json=True),
    )
    outputs.append(jrisk)
    risk_job = json.loads(jrisk)["job_id"]

    # status / inspect / decisions / delivery
    outputs.append(
        _capture(cmd_dispatch_status, argparse.Namespace(job_id=job_id, as_json=False))[1]
    )
    outputs.append(
        _capture(cmd_dispatch_inspect, argparse.Namespace(job_id=job_id, late=True, as_json=False))[
            1
        ]
    )
    outputs.append(
        _capture(cmd_dispatch_decisions, argparse.Namespace(job=risk_job, as_json=False))[1]
    )
    outputs.append(
        _capture(cmd_dispatch_delivery, argparse.Namespace(job_id=job_id, as_json=False))[1]
    )

    # receipt (with a Worker row)
    _seed_receipt(home, excerpt="produced a clean patch")
    outputs.append(
        _capture(cmd_dispatch_receipt, argparse.Namespace(job_id="job_rcpt", as_json=False))[1]
    )

    blob = "\n".join(outputs)
    assert "Fleet Worker" not in blob
    assert "fleet worker" not in blob.lower()
    # Positive: plain "Worker" terminology IS used on the receipt.
    assert "Worker " in blob


# ---------------------------------------------------------------------------
# Real entry-point smoke test
# ---------------------------------------------------------------------------


def test_entry_point_help_registers():
    """`python -m tokenpak.cli.main dispatch --help` lists every verb."""
    proc = subprocess.run(
        [sys.executable, "-m", "tokenpak.cli.main", "dispatch", "--help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    for verb in _VERBS:
        assert verb in proc.stdout, f"verb {verb} missing from help"
    assert "Fleet Worker" not in proc.stdout
