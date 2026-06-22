# SPDX-License-Identifier: Apache-2.0
"""Security regression coverage for ``tokenpak pak`` (codex-review-1 A1/A2/A5).

- A1  export must contain writes inside the chosen output dir; an untrusted
      anchor ``path`` that is absolute or climbs out with ``..`` is skipped.
- A2  create must not follow symlinks (a link to e.g. /etc/hostname would
      otherwise embed the link target's content into the Pak).
- A5  import must not print "checksum verified" when the Pak carries no
      declared checksum — nothing was verified in that case.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenpak.cli.commands import pak

# ── helpers ───────────────────────────────────────────────────────────────────


def _write_pak(path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _checksum_for(payload: dict) -> str:
    """Reproduce the checksum the create/import path computes."""
    body = json.dumps(
        {k: v for k, v in payload.items() if k not in ("checksum", "pak_id")},
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


# ── A1: export path traversal containment ─────────────────────────────────────


def test_export_rejects_traversal_and_absolute_keeps_safe(tmp_path, capsys):
    out_dir = tmp_path / "exported"
    out_dir.mkdir()
    # An absolute target that lands OUTSIDE out_dir (kept under tmp_path so the
    # test never touches a real system path — same property as /tmp/escape.txt).
    abs_escape = tmp_path / "outside" / "pwned.txt"

    pak_file = tmp_path / "evil.pak.json"
    _write_pak(
        pak_file,
        {
            "pak_id": "pak:deadbeef",
            "anchors": [
                {"path": "../escape.txt", "content": "owned", "encoding": "utf-8"},
                {"path": str(abs_escape), "content": "owned", "encoding": "utf-8"},
                {"path": "sub/ok.txt", "content": "safe", "encoding": "utf-8"},
            ],
        },
    )

    rc = pak._export_file_pak(str(pak_file), out_dir)
    assert rc == 0

    # Safe nested anchor written inside out_dir.
    safe = out_dir / "sub" / "ok.txt"
    assert safe.is_file()
    assert safe.read_text(encoding="utf-8") == "safe"

    # Neither escape target was created.
    assert not (out_dir.parent / "escape.txt").exists()
    assert not abs_escape.exists()

    err = capsys.readouterr().err
    assert "escapes export dir" in err


def test_within_dir_helper():
    base = Path("/srv/export")
    assert pak._within_dir(base, "ok/file.txt") is True
    assert pak._within_dir(base, "../escape.txt") is False
    assert pak._within_dir(base, "/etc/passwd") is False
    assert pak._within_dir(base, "a/../b.txt") is True  # normalizes inside base


# ── A2: create must skip symlinks ─────────────────────────────────────────────


def test_create_skips_symlinks(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.txt").write_text("real content", encoding="utf-8")

    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    link = src / "link.txt"
    try:
        link.symlink_to(secret)
    except OSError:  # pragma: no cover - platform without symlink support
        pytest.skip("symlinks unsupported on this platform")

    out = tmp_path / "out.pak.json"
    args = SimpleNamespace(
        source_dir=str(src),
        output=str(out),
        title="t",
        objective="o",
        summary="s",
        ttl="7d",
        continuation_notes="",
        include_content=True,
        max_bytes=2_000_000,
    )
    rc = pak.cmd_pak_create(args)
    assert rc == 0

    payload = json.loads(out.read_text(encoding="utf-8"))
    paths = {a["path"] for a in payload["anchors"]}
    assert "real.txt" in paths
    # The symlink was skipped: neither its name nor the secret's content leaked.
    assert "link.txt" not in paths
    blob = out.read_text(encoding="utf-8")
    assert "TOP SECRET" not in blob


# ── A5: import checksum honesty ───────────────────────────────────────────────


def _import_payload(tmp_path, monkeypatch, payload: dict):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "home"))
    pak_file = tmp_path / "in.pak.json"
    _write_pak(pak_file, payload)
    args = SimpleNamespace(pak_file=str(pak_file), force=False)
    return pak.cmd_pak_import(args)


def test_import_missing_checksum_not_claimed_verified(tmp_path, monkeypatch, capsys):
    payload = {"title": "no checksum here", "anchors": []}
    rc = _import_payload(tmp_path, monkeypatch, payload)
    assert rc == 0
    out = capsys.readouterr().out
    assert "verified" not in out
    assert "no declared checksum" in out


def test_import_checksum_mismatch_rejected(tmp_path, monkeypatch, capsys):
    payload = {"title": "tampered", "anchors": [], "checksum": "sha256:" + "0" * 64}
    rc = _import_payload(tmp_path, monkeypatch, payload)
    assert rc == 1
    err = capsys.readouterr().err
    assert "checksum mismatch" in err


def test_import_valid_checksum_reports_verified(tmp_path, monkeypatch, capsys):
    payload = {"title": "honest", "anchors": []}
    payload["checksum"] = _checksum_for(payload)
    rc = _import_payload(tmp_path, monkeypatch, payload)
    assert rc == 0
    out = capsys.readouterr().out
    assert "checksum verified" in out
