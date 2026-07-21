# SPDX-License-Identifier: Apache-2.0
"""End-to-end CLI-path tests for `tokenpak debug receipt <id>`.

Unlike ``tests/cli/test_debug_receipt_view.py`` (which calls the render helper
directly), these drive the **live parser** built by ``_cli_core.build_parser``
and dispatch through ``args.func(args)`` exactly as ``main()`` does — proving the
``receipt`` subcommand is actually reachable through the wired CLI.
"""

from __future__ import annotations

import json


def _seed_request(monkeypatch, tmp_path) -> str:
    """Create an isolated request-ledger row; return its id."""
    from tokenpak.cli import request_explorer

    home = tmp_path / "home"
    monkeypatch.setenv("TOKENPAK_HOME", str(home))
    monkeypatch.delenv("TOKENPAK_DB", raising=False)
    monkeypatch.delenv("TOKENPAK_MONITOR_DB", raising=False)

    # Isolate the debug-capture blob dir: it is derived from Path.home() at import,
    # so without this redirect captures would write to the real ~/.tokenpak/debug.
    from tokenpak.debug import capture as _capture

    blob_dir = tmp_path / "debug"
    blob_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_capture, "_BLOB_DIR", blob_dir)

    ledger = home / "requests.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(request_explorer, "REQUESTS_PATH", ledger)

    row = {
        "id": "req-rcpt-cli-1",
        "timestamp": "2026-06-25T18:05:00Z",
        "model": "claude-sonnet",
        "request_type": "chat",
        "input_tokens": 200,
        "output_tokens": 50,
        "estimated_cost": 0.018,
        "latency_ms": 71,
        "status_code": 200,
        "endpoint": "/v1/messages",
        "cache_read_tokens": 80,
        "cache_creation_tokens": 20,
        "would_have_saved": 0.006,
        "cache_origin": "proxy",
        "session_id": "sess-x",
        "agent_id": "proxy-test",
    }
    ledger.write_text(json.dumps(row) + "\n")

    return str(request_explorer.load_requests()[0]["id"])


def _run_cli(argv):
    """Parse ``argv`` with the live parser and dispatch as ``main()`` does."""
    from tokenpak._cli_core import build_parser

    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


def test_cli_receipt_renders_recorded_request(monkeypatch, tmp_path, capsys):
    rid = _seed_request(monkeypatch, tmp_path)

    _run_cli(["debug", "receipt", rid])
    out = capsys.readouterr().out

    payload = json.loads(out)
    assert payload["schema_version"] == "receipt.v1"
    assert payload["receipt_id"] == f"rcpt_{rid}"
    assert payload["trail"]["agent_id"]["value"] == "proxy-test"


def test_cli_receipt_missing_id_prints_support_pointer(monkeypatch, tmp_path, capsys):
    _seed_request(monkeypatch, tmp_path)

    _run_cli(["debug", "receipt", "does-not-exist"])
    out = capsys.readouterr().out

    assert "No receipt" in out
    assert "Debug capture bundle" in out
    assert "tokenpak debug list" in out


def test_cli_receipt_no_id_prints_support_pointer(monkeypatch, tmp_path, capsys):
    # `request_id` is an optional positional (nargs="?"); a bare `receipt`
    # reaches the render helper's "no id" branch instead of an argparse error.
    _seed_request(monkeypatch, tmp_path)

    _run_cli(["debug", "receipt"])
    out = capsys.readouterr().out

    assert "No receipt" in out


def test_cli_receipt_raw_toggles_redaction(monkeypatch, tmp_path, capsys):
    from tokenpak.debug import capture
    from tokenpak.debug.capture import CaptureMode

    rid = _seed_request(monkeypatch, tmp_path)

    # Seed a hash-only capture keyed by the request id so the pointer resolves.
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "hash_only")
    dest = capture.capture(
        rid, {"prompt": "secret"}, {"completion": "secret"}, mode=CaptureMode.HASH_ONLY
    )
    assert dest is not None

    # Redacted (default): never leaks the on-disk capture path.
    _run_cli(["debug", "receipt", rid])
    redacted = capsys.readouterr().out
    redacted_payload = json.loads(redacted)
    assert redacted_payload["debug_pointer"]["present"] is True
    assert "path" not in redacted_payload["debug_pointer"]
    assert str(dest) not in redacted

    # --raw: surfaces the unredacted path.
    _run_cli(["debug", "receipt", rid, "--raw"])
    raw = capsys.readouterr().out
    assert str(dest) in raw
