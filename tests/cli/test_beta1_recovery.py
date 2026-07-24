# SPDX-License-Identifier: Apache-2.0
"""Beta 1 recovery tests — Lanes B, Q1, E, F, G.

Smoke + functional coverage for the verb families introduced in the
2026-05-15 Beta 1 readiness recovery sweep:

- Lane B  ``_paths`` resolver + ``tokenpak home`` family.
- Lane Q1 ``tokenpak pak status`` fast-fail (no daemon = ≤ 2s, no auto-start).
- Lane E  ``tokenpak tip conformance``.
- Lane F  ``pak create / inspect / export / import`` roundtrip.
- Lane G  ``tokenpak activate`` input validation + dynamic ``plan``.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Lane B — _paths resolver
# ---------------------------------------------------------------------------


def test_paths_env_override_wins(tmp_path, monkeypatch):
    from tokenpak import _paths

    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "custom"))
    assert _paths.home() == tmp_path / "custom"


def test_paths_canonical_default(tmp_path, monkeypatch):
    from tokenpak import _paths

    monkeypatch.delenv("TOKENPAK_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # neither dir exists → canonical
    assert _paths.home() == tmp_path / ".tpk"


def test_paths_legacy_fallback(tmp_path, monkeypatch):
    from tokenpak import _paths

    monkeypatch.delenv("TOKENPAK_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    legacy = tmp_path / ".tokenpak"
    legacy.mkdir()
    # legacy exists, canonical absent → legacy
    assert _paths.home() == legacy
    assert _paths.needs_migration() is True


def test_paths_canonical_wins_over_legacy(tmp_path, monkeypatch):
    from tokenpak import _paths

    monkeypatch.delenv("TOKENPAK_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    legacy = tmp_path / ".tokenpak"
    canonical = tmp_path / ".tpk"
    legacy.mkdir()
    canonical.mkdir()
    assert _paths.home() == canonical
    assert _paths.needs_migration() is False


# ---------------------------------------------------------------------------
# Lane B — tokenpak home migrate (backup-first, non-destructive)
# ---------------------------------------------------------------------------


def test_home_migrate_is_non_destructive(tmp_path, monkeypatch, capsys):
    from tokenpak import _paths
    from tokenpak.cli.commands.home_cmd import cmd_home_migrate

    monkeypatch.delenv("TOKENPAK_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    legacy = tmp_path / ".tokenpak"
    canonical = tmp_path / ".tpk"
    legacy.mkdir()
    (legacy / "config.json").write_text('{"hello": true}')

    ns = argparse.Namespace(dry_run=False, force=False)
    rc = cmd_home_migrate(ns)
    assert rc == 0
    # canonical now exists with the file
    assert (canonical / "config.json").exists()
    # legacy is untouched
    assert (legacy / "config.json").exists()


# ---------------------------------------------------------------------------
# Lane Q1 — pak status fast-fail
# ---------------------------------------------------------------------------


def test_pak_status_fast_fail_no_daemon(tmp_path, monkeypatch):
    from tokenpak.cli.commands.pak import cmd_pak_status

    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    ns = argparse.Namespace(json=True)
    start = time.monotonic()
    rc = cmd_pak_status(ns)
    elapsed = time.monotonic() - start
    assert rc == 0
    # No daemon, no vault index, no journal → must complete fast.
    assert elapsed < 2.0, f"pak status took {elapsed:.2f}s on a fresh install"


# ---------------------------------------------------------------------------
# Lane E — TIP conformance runner
# ---------------------------------------------------------------------------


def test_tip_conformance_passes_on_clean_install():
    from tokenpak.cli.commands.tip import exit_code_for, run_conformance_checks, summarize

    results = run_conformance_checks()
    summary = summarize(results)
    assert summary["counts"]["FAIL"] == 0, (
        f"TIP conformance regressed: {[(r.name, r.status, r.summary) for r in results if r.status == 'FAIL']}"
    )
    assert exit_code_for(summary) == 0


def test_tip_inspect_emits_capability_labels(capsys):
    from tokenpak.cli.commands.tip import cmd_tip_inspect

    ns = argparse.Namespace(json=True)
    rc = cmd_tip_inspect(ns)
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["total"] > 0
    # All emitted labels conform to the tip/ext pattern.
    import re

    pat = re.compile(r"^(tip|ext)\.[a-z0-9._-]+$")
    for group in data["groups"].values():
        for label in group:
            assert pat.match(label), label


# ---------------------------------------------------------------------------
# Lane F — pak create / inspect / export / import roundtrip
# ---------------------------------------------------------------------------


def test_pak_roundtrip(tmp_path, monkeypatch, capsys):
    from tokenpak.cli.commands.pak import (
        cmd_pak_create,
        cmd_pak_export,
        cmd_pak_import,
        cmd_pak_inspect,
    )

    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "home"))

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello\n")
    (src / "nested").mkdir()
    (src / "nested" / "b.md").write_text("# title\n")

    out_pak = tmp_path / "out.pak.json"
    rc = cmd_pak_create(
        argparse.Namespace(
            source_dir=str(src),
            output=str(out_pak),
            title="rt",
            objective="roundtrip",
            summary="",
            ttl="",
            continuation_notes="",
            include_content=True,
            max_bytes=2_000_000,
        )
    )
    assert rc == 0
    assert out_pak.exists()

    payload = json.loads(out_pak.read_text())
    assert payload["pak_id"].startswith("pak:")
    assert payload["checksum"].startswith("sha256:")
    assert {a["path"] for a in payload["anchors"]} == {"a.txt", "nested/b.md"}

    # inspect via file path — clear captured text from create first
    capsys.readouterr()
    rc = cmd_pak_inspect(argparse.Namespace(pak_ref=str(out_pak), json=True))
    assert rc == 0
    inspect_out = json.loads(capsys.readouterr().out)
    assert inspect_out["pak_id"] == payload["pak_id"]

    # import into local store
    rc = cmd_pak_import(argparse.Namespace(pak_file=str(out_pak), force=False))
    assert rc == 0
    pak_id = payload["pak_id"]
    store = Path(os.environ["TOKENPAK_HOME"]) / "paks"
    safe = pak_id.replace(":", "_") + ".pak.json"
    assert (store / safe).exists()

    # inspect via pak:<id> form
    rc = cmd_pak_inspect(argparse.Namespace(pak_ref=pak_id, json=True))
    assert rc == 0

    # export back to a directory and verify file contents survive
    restored = tmp_path / "restored"
    rc = cmd_pak_export(argparse.Namespace(pak_ref=str(out_pak), output=str(restored)))
    assert rc == 0
    assert (restored / "a.txt").read_text() == "hello\n"
    assert (restored / "nested" / "b.md").read_text() == "# title\n"


def test_pak_import_rejects_checksum_mismatch(tmp_path, monkeypatch, capsys):
    from tokenpak.cli.commands.pak import cmd_pak_import

    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "home"))
    tampered = tmp_path / "tampered.pak.json"
    tampered.write_text(
        json.dumps(
            {
                "pak_id": "pak:abcdef",
                "checksum": "sha256:00" * 32,
                "schema_version": 1,
                "pak_type": "context",
                "anchors": [{"path": "x", "sha256": "deadbeef"}],
            }
        )
    )
    rc = cmd_pak_import(argparse.Namespace(pak_file=str(tampered), force=False))
    assert rc == 1
    err = capsys.readouterr().err
    assert "checksum mismatch" in err


# ---------------------------------------------------------------------------
# Lane G — activate input hardening + dynamic plan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "   ",
        "abc",  # too short
        "test",  # placeholder
        "demo",
        "ab\x00cd-this-is-long-enough",  # non-printable
        "***!!!@@@invalid-charset****",
    ],
)
def test_activate_rejects_obvious_garbage(bad_key, tmp_path, monkeypatch):
    from tokenpak import licensing as _lic

    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(_lic, "_license_path", lambda: tmp_path / "license.json")
    result = _lic.activate(bad_key)
    assert result.ok is False, f"accepted garbage key: {bad_key!r}"


def test_activate_accepts_plausible_key(tmp_path, monkeypatch):
    from tokenpak import licensing as _lic

    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(_lic, "_license_path", lambda: tmp_path / "license.json")
    # Plausible base64url-ish token
    result = _lic.activate("ABCD-1234-EFGH-5678-IJKL")
    assert result.ok is True
    # But still defaults to Free + pending_validation (fail-safe).
    assert result.license.tier == _lic.TIER_FREE
    assert result.license.status == "pending_validation"


def test_discover_plans_is_dynamic_no_tbd():
    from tokenpak import licensing as _lic

    plans = _lic.discover_plans()
    tiers = {p["tier"] for p in plans}
    assert _lic.TIER_FREE in tiers
    # No misleading "TBD" string.
    for p in plans:
        assert p["price"] != "TBD", p
    # Pro tier exists and has > 0 gated features
    pro = next((p for p in plans if p["tier"] == _lic.TIER_PRO), None)
    assert pro is not None
    assert pro["feature_count"] > 0
