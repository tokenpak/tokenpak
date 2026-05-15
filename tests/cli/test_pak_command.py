# SPDX-License-Identifier: Apache-2.0
"""Offline contract tests for ``tokenpak pak`` CLI (Std 32 §10).

Tests use the in-process argparse builder + handler functions rather than
``subprocess`` — faster, exception-traceable, deterministic across CI.
The smoke test at the bottom exercises the real entry point once for
end-to-end coverage.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tokenpak.cli.commands.pak import (
    build_pak_parser,
    cmd_pak_export,
    cmd_pak_import,
    cmd_pak_inspect,
    cmd_pak_status,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parser():
    """Build a parser with just the ``pak`` subcommand registered."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    build_pak_parser(sub)
    return parser


def _capture_stdout(handler, args) -> tuple[int, str]:
    """Run a handler, capture its stdout, return (exit_code, stdout)."""
    import io

    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        rc = handler(args)
    finally:
        sys.stdout = saved
    return int(rc or 0), buf.getvalue()


# ---------------------------------------------------------------------------
# Argparse registration
# ---------------------------------------------------------------------------


def test_parser_registers_pak_subcommand():
    parser = _make_parser()
    args = parser.parse_args(["pak", "status"])
    assert args.command == "pak"
    assert args.pak_action == "status"


def test_parser_registers_all_actions():
    parser = _make_parser()
    for action in ("inspect", "export", "create", "import", "status"):
        # Each action requires a positional or option, so we provide enough
        # to make argparse happy and still validate registration.
        if action == "inspect":
            args = parser.parse_args(["pak", action, "vault:x#y"])
        elif action == "export":
            args = parser.parse_args(["pak", action, "vault:x#y", "-o", "/tmp/o"])
        elif action == "create":
            args = parser.parse_args(["pak", action, "/tmp/src",
                                      "-o", "/tmp/out.pak.json"])
        elif action == "import":
            # Beta 1: import now takes a pak file (not a directory).
            args = parser.parse_args(["pak", action, "/tmp/in.pak.json"])
        else:  # status
            args = parser.parse_args(["pak", action])
        assert args.pak_action == action


def test_parser_inspect_requires_pak_ref():
    parser = _make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["pak", "inspect"])  # missing pak_ref


def test_parser_export_requires_output():
    parser = _make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["pak", "export", "vault:x#y"])  # missing --output


# ---------------------------------------------------------------------------
# pak status
# ---------------------------------------------------------------------------


def test_status_text_exits_0():
    args = SimpleNamespace(json=False)
    rc, out = _capture_stdout(cmd_pak_status, args)
    assert rc == 0
    assert "MultiPak Pro Phase 1 status" in out
    assert "Daemon state" in out


def test_status_json_emits_canonical_payload():
    args = SimpleNamespace(json=True)
    rc, out = _capture_stdout(cmd_pak_status, args)
    assert rc == 0
    payload = json.loads(out)
    for key in (
        "daemon_state",
        "multipak_enabled",
        "pak_store_present",
        "vault_paks_indexed",
        "promotion_candidates",
    ):
        assert key in payload


def test_status_daemon_state_unavailable_by_default():
    """No daemon installed on this host."""
    args = SimpleNamespace(json=True)
    rc, out = _capture_stdout(cmd_pak_status, args)
    payload = json.loads(out)
    assert payload["daemon_state"] == "unavailable"


# ---------------------------------------------------------------------------
# pak inspect
# ---------------------------------------------------------------------------


def test_inspect_non_vault_id_exits_1_pro_required(capsys):
    args = SimpleNamespace(pak_ref="interaction:s1:42", json=False)
    rc = cmd_pak_inspect(args)
    err = capsys.readouterr().err
    assert rc == 1
    assert "Pro daemon" in err or "tokenpak-paid" in err


def test_inspect_non_vault_id_json_envelope():
    args = SimpleNamespace(pak_ref="decision:foo", json=True)
    rc, out = _capture_stdout(cmd_pak_inspect, args)
    assert rc == 1
    payload = json.loads(out)
    assert payload["error"] == "not_implemented"
    assert payload["reason"] == "pro_daemon_required"


def test_inspect_vault_id_not_indexed_exits_1(capsys):
    """Vault: prefix routes to adapter; missing block is a 1 with file-not-found-ish msg."""
    args = SimpleNamespace(pak_ref="vault:nosuch#deadbeef", json=False)
    rc = cmd_pak_inspect(args)
    err = capsys.readouterr().err
    assert rc == 1
    assert "vault block not indexed" in err


def test_inspect_vault_id_json_emits_pak_not_found():
    args = SimpleNamespace(pak_ref="vault:nosuch#deadbeef", json=True)
    rc, out = _capture_stdout(cmd_pak_inspect, args)
    assert rc == 1
    payload = json.loads(out)
    assert payload["error"] == "pak_not_found"


def test_inspect_file_path_missing(capsys, tmp_path):
    args = SimpleNamespace(pak_ref=str(tmp_path / "nope.pak"), json=False)
    rc = cmd_pak_inspect(args)
    err = capsys.readouterr().err
    assert rc == 1
    assert "file not found" in err


def test_inspect_file_path_emits_text_summary(tmp_path):
    """Read a Pak from a JSON file and render the text view."""
    pak_dict = {
        "pak_id": "vault:abc#def",
        "pak_type": "vault",
        "title": "README.md",
        "summary": "A test Pak file",
        "scope": {"project": "tokenpak"},
        "source": {
            "platform": "tokenpak-vault",
            "source_type": "file",
            "created_at": "2026-05-08T00:00:00+00:00",
            "source_hash": "abc12345" * 8,
        },
        "status": "proposed",
        "authority": "file_source",
        "confidence": "medium",
        "retention": {"ttl": "source_lifetime"},
        "privacy": {"class": "local_only"},
        "anchors": [],
        "relationships": {
            "depends_on": [],
            "supersedes": [],
            "related": [],
            "conflicts_with": [],
        },
    }
    pak_file = tmp_path / "test.pak.json"
    pak_file.write_text(json.dumps(pak_dict))
    args = SimpleNamespace(pak_ref=str(pak_file), json=False)
    rc, out = _capture_stdout(cmd_pak_inspect, args)
    assert rc == 0
    assert "vault:abc#def" in out
    assert "README.md" in out


def test_inspect_file_path_json_round_trips(tmp_path):
    pak_dict = {
        "pak_id": "vault:x#y",
        "pak_type": "vault",
        "title": "t",
        "summary": "s",
        "scope": {},
        "source": {
            "platform": "tokenpak-vault",
            "source_type": "file",
            "created_at": "2026-05-08T00:00:00+00:00",
            "source_hash": "h",
        },
        "status": "proposed",
        "authority": "file_source",
        "confidence": "medium",
        "retention": {"ttl": "source_lifetime"},
        "privacy": {"class": "local_only"},
        "anchors": [],
        "relationships": {
            "depends_on": [],
            "supersedes": [],
            "related": [],
            "conflicts_with": [],
        },
    }
    pak_file = tmp_path / "p.json"
    pak_file.write_text(json.dumps(pak_dict))
    args = SimpleNamespace(pak_ref=str(pak_file), json=True)
    rc, out = _capture_stdout(cmd_pak_inspect, args)
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["pak_id"] == "vault:x#y"


def test_inspect_invalid_pak_file(tmp_path, capsys):
    bad = tmp_path / "bad.pak"
    bad.write_text("not json {")
    args = SimpleNamespace(pak_ref=str(bad), json=False)
    rc = cmd_pak_inspect(args)
    err = capsys.readouterr().err
    assert rc == 1
    assert "cannot parse Pak file" in err


# ---------------------------------------------------------------------------
# pak export
# ---------------------------------------------------------------------------


def test_export_non_vault_returns_pro_required(capsys):
    args = SimpleNamespace(pak_ref="interaction:foo", output="/tmp/out")
    rc = cmd_pak_export(args)
    err = capsys.readouterr().err
    assert rc == 1
    assert "Pro daemon" in err


def test_export_vault_not_indexed_exits_1(capsys, tmp_path):
    out_dir = tmp_path / "out"
    args = SimpleNamespace(pak_ref="vault:nosuch#x", output=str(out_dir))
    rc = cmd_pak_export(args)
    err = capsys.readouterr().err
    assert rc == 1
    assert "vault block not indexed" in err


# ---------------------------------------------------------------------------
# pak import
# ---------------------------------------------------------------------------


def test_import_rejects_missing_file(capsys, tmp_path):
    """Beta 1 ``pak import`` is OSS (Kevin directive 2026-05-15).

    The verb installs a Pak file into the local store, verifying the
    file's declared checksum. ``pak import`` against a non-existent
    file must exit 1 with a clear ``file not found`` error.
    """
    args = SimpleNamespace(
        pak_file=str(tmp_path / "nope.pak.json"), force=False,
    )
    rc = cmd_pak_import(args)
    err = capsys.readouterr().err
    assert rc == 1
    assert "file not found" in err


# ---------------------------------------------------------------------------
# End-to-end smoke (subprocess) — proves the entry-point wiring works
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_e2e_pak_status_via_module_entry():
    """One subprocess test to confirm `python -m tokenpak pak status` works.

    Slower than the unit tests above (loads the full CLI), so we only run
    the always-on diagnostic action. CI smoke; not exhaustive.
    """
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak", "pak", "status", "--json"],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    # The CLI prints config-loader logs to stdout before our JSON. Find the
    # JSON object in the output.
    json_start = result.stdout.find("{")
    assert json_start >= 0, f"no JSON in stdout: {result.stdout!r}"
    payload = json.loads(result.stdout[json_start:])
    assert "daemon_state" in payload
    assert "multipak_enabled" in payload
