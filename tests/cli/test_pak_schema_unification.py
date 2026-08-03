# SPDX-License-Identifier: Apache-2.0
"""Pak file-schema unification contract tests.

``pak create`` must emit the canonical Pak contract — never a deprecated
subtype alias — and the emitted payload must parse through
``tokenpak.tip.pak.Pak.from_dict`` warning-free. Legacy schema_version-1
files must keep opening (inspect / import / export) and must be
upgradeable via ``pak migrate`` with the checksum guarantee intact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import warnings
from types import SimpleNamespace

from tokenpak.cli.commands.pak import (
    build_pak_parser,
    cmd_pak_create,
    cmd_pak_export,
    cmd_pak_import,
    cmd_pak_inspect,
    cmd_pak_migrate,
    upgrade_pak_payload,
)
from tokenpak.tip.pak import Pak, PakSubtype, is_legacy_subtype

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_src(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello\n", encoding="utf-8")
    (src / "nested").mkdir()
    (src / "nested" / "b.md").write_text("# title\n", encoding="utf-8")
    return src


def _create_args(src, out, **over):
    base = dict(
        source_dir=str(src),
        output=str(out),
        title="",
        objective="unification objective",
        summary="unification summary",
        ttl="7d",
        continuation_notes="carry on",
        include_content=True,
        max_bytes=2_000_000,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _create_payload(tmp_path):
    src = _make_src(tmp_path)
    out = tmp_path / "out.pak.json"
    rc = cmd_pak_create(_create_args(src, out))
    assert rc == 0
    return json.loads(out.read_text(encoding="utf-8")), out, src


def _checksum_for(payload: dict) -> str:
    """Reproduce the checksum construction (unchanged from schema v1)."""
    body = json.dumps(
        {k: v for k, v in payload.items() if k not in ("checksum", "pak_id")},
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _legacy_v1_payload(src) -> dict:
    """Faithful reproduction of what the retired schema_version-1 writer
    emitted for ``src`` — including the deprecated ``context`` subtype."""
    anchors = []
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        anchors.append(
            {
                "path": str(path.relative_to(src)),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "content": data.decode("utf-8"),
                "encoding": "utf-8",
            }
        )
    payload = {
        "schema_version": 1,
        "pak_type": "context",
        "title": src.name,
        "objective": "legacy objective",
        "summary": "legacy summary",
        "ttl": "7d",
        "continuation_notes": "legacy notes",
        "created_at": "2026-05-15T00:00:00Z",
        "scope": {"source_root": str(src)},
        "anchors": anchors,
        "skipped": [],
        "token_estimate": 3,
    }
    payload["checksum"] = _checksum_for(payload)
    payload["pak_id"] = "pak:" + payload["checksum"][len("sha256:") : len("sha256:") + 16]
    return payload


def _write_legacy_pak(tmp_path):
    src = _make_src(tmp_path)
    payload = _legacy_v1_payload(src)
    pak_file = tmp_path / "legacy.pak.json"
    pak_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload, pak_file, src


# ---------------------------------------------------------------------------
# create — canonical emission
# ---------------------------------------------------------------------------


def test_create_does_not_emit_deprecated_subtype(tmp_path):
    payload, _, _ = _create_payload(tmp_path)
    assert not is_legacy_subtype(payload["pak_type"])
    # Parsing the emitted subtype must be warning-free (aliases warn).
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert PakSubtype.parse(payload["pak_type"]) in set(PakSubtype)


def test_create_emits_canonical_contract_fields(tmp_path):
    payload, _, src = _create_payload(tmp_path)
    assert payload["schema_version"] == 2
    for key in (
        "pak_id",
        "pak_type",
        "title",
        "summary",
        "scope",
        "source",
        "status",
        "authority",
        "confidence",
        "retention",
        "privacy",
        "relationships",
        "anchors",
    ):
        assert key in payload, f"canonical field missing from created Pak: {key}"
    assert payload["source"]["platform"] == "tokenpak-cli"
    assert payload["source"]["source_hash"]
    assert set(payload["scope"]) == {"user", "project", "topic"}
    assert payload["source_root"] == str(src)
    assert payload["ttl_hint"] == "7d"
    assert "ttl" not in payload


def test_create_payload_parses_via_canonical_contract(tmp_path):
    payload, _, _ = _create_payload(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        pak = Pak.from_dict(payload)
    assert pak.pak_type is PakSubtype.RECALL
    assert pak.pak_id == payload["pak_id"]
    assert len(pak.anchors) == len(payload["anchors"])
    assert all(a.source_hash for a in pak.anchors)


def test_create_anchor_records_carry_canonical_fields(tmp_path):
    payload, _, _ = _create_payload(tmp_path)
    for anchor in payload["anchors"]:
        assert anchor["anchor_id"]
        assert (
            anchor["source_hash"] == hashlib.sha256(anchor["content"].encode("utf-8")).hexdigest()
        )
        assert anchor["snippet_available"] is True
        assert "sha256" not in anchor  # legacy key not emitted


def test_create_checksum_guarantee_and_import(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "home"))
    payload, pak_file, _ = _create_payload(tmp_path)
    assert payload["checksum"] == _checksum_for(payload)
    assert payload["pak_id"] == "pak:" + payload["checksum"][len("sha256:") :][:16]
    rc = cmd_pak_import(SimpleNamespace(pak_file=str(pak_file), force=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "checksum verified" in out
    assert "pak migrate" not in out  # canonical files get no legacy nudge


def test_create_export_round_trip(tmp_path):
    _, pak_file, src = _create_payload(tmp_path)
    restored = tmp_path / "restored"
    rc = cmd_pak_export(SimpleNamespace(pak_ref=str(pak_file), output=str(restored)))
    assert rc == 0
    assert (restored / "a.txt").read_text(encoding="utf-8") == "hello\n"
    assert (restored / "nested" / "b.md").read_text(encoding="utf-8") == "# title\n"


# ---------------------------------------------------------------------------
# legacy v1 files — read-compat must not break
# ---------------------------------------------------------------------------


def test_legacy_v1_inspect_text_still_opens(tmp_path, capsys):
    payload, pak_file, _ = _write_legacy_pak(tmp_path)
    rc = cmd_pak_inspect(SimpleNamespace(pak_ref=str(pak_file), json=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert payload["pak_id"] in out
    # Display shows the canonical view + migration hint; file is untouched.
    assert "recall" in out
    assert "pak migrate" in out
    assert json.loads(pak_file.read_text(encoding="utf-8")) == payload


def test_legacy_v1_inspect_json_preserves_raw_payload(tmp_path, capsys):
    payload, pak_file, _ = _write_legacy_pak(tmp_path)
    rc = cmd_pak_inspect(SimpleNamespace(pak_ref=str(pak_file), json=True))
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == payload  # --json is the raw on-disk view, unrewritten


def test_legacy_v1_import_still_verifies_checksum(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "home"))
    _, pak_file, _ = _write_legacy_pak(tmp_path)
    rc = cmd_pak_import(SimpleNamespace(pak_file=str(pak_file), force=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "checksum verified" in out
    assert "pak migrate" in out  # legacy nudge


def test_legacy_v1_export_still_restores_content(tmp_path):
    _, pak_file, _ = _write_legacy_pak(tmp_path)
    restored = tmp_path / "restored"
    rc = cmd_pak_export(SimpleNamespace(pak_ref=str(pak_file), output=str(restored)))
    assert rc == 0
    assert (restored / "a.txt").read_text(encoding="utf-8") == "hello\n"
    assert (restored / "nested" / "b.md").read_text(encoding="utf-8") == "# title\n"


# ---------------------------------------------------------------------------
# migrate — the upgrade path
# ---------------------------------------------------------------------------


def test_migrate_upgrades_legacy_file_in_place(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "home"))
    legacy, pak_file, _ = _write_legacy_pak(tmp_path)
    rc = cmd_pak_migrate(SimpleNamespace(pak_file=str(pak_file), output=None))
    assert rc == 0

    migrated = json.loads(pak_file.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert not is_legacy_subtype(migrated["pak_type"])
    assert migrated["pak_type"] == "recall"
    assert migrated["pak_id"] == legacy["pak_id"]  # identity preserved
    assert migrated["checksum"] == _checksum_for(migrated)  # re-stamped, valid
    assert migrated["checksum"] != legacy["checksum"]  # body changed
    assert migrated["ttl_hint"] == "7d" and "ttl" not in migrated
    assert migrated["source_root"] == legacy["scope"]["source_root"]
    for anchor in migrated["anchors"]:
        assert "sha256" not in anchor
        assert anchor["source_hash"]
        assert anchor["anchor_id"]

    # Parses through the canonical contract without deprecation warnings.
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        pak = Pak.from_dict(migrated)
    assert pak.pak_type is PakSubtype.RECALL

    # Migrated file imports cleanly (checksum verified) with no nudge.
    capsys.readouterr()
    rc = cmd_pak_import(SimpleNamespace(pak_file=str(pak_file), force=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "checksum verified" in out
    assert "pak migrate" not in out


def test_migrate_preserves_anchor_content(tmp_path):
    _, pak_file, _ = _write_legacy_pak(tmp_path)
    rc = cmd_pak_migrate(SimpleNamespace(pak_file=str(pak_file), output=None))
    assert rc == 0
    restored = tmp_path / "restored"
    rc = cmd_pak_export(SimpleNamespace(pak_ref=str(pak_file), output=str(restored)))
    assert rc == 0
    assert (restored / "a.txt").read_text(encoding="utf-8") == "hello\n"
    assert (restored / "nested" / "b.md").read_text(encoding="utf-8") == "# title\n"


def test_migrate_is_idempotent(tmp_path, capsys):
    _, pak_file, _ = _write_legacy_pak(tmp_path)
    assert cmd_pak_migrate(SimpleNamespace(pak_file=str(pak_file), output=None)) == 0
    first = pak_file.read_bytes()
    capsys.readouterr()
    assert cmd_pak_migrate(SimpleNamespace(pak_file=str(pak_file), output=None)) == 0
    assert "nothing to migrate" in capsys.readouterr().out
    assert pak_file.read_bytes() == first


def test_migrate_refuses_tampered_file(tmp_path, capsys):
    payload, pak_file, _ = _write_legacy_pak(tmp_path)
    payload["objective"] = "tampered after stamping"
    pak_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    before = pak_file.read_bytes()
    rc = cmd_pak_migrate(SimpleNamespace(pak_file=str(pak_file), output=None))
    assert rc == 1
    assert "checksum mismatch" in capsys.readouterr().err
    assert pak_file.read_bytes() == before  # refused = untouched


def test_migrate_output_flag_leaves_source_untouched(tmp_path):
    legacy, pak_file, _ = _write_legacy_pak(tmp_path)
    dest = tmp_path / "migrated.pak.json"
    rc = cmd_pak_migrate(SimpleNamespace(pak_file=str(pak_file), output=str(dest)))
    assert rc == 0
    assert json.loads(pak_file.read_text(encoding="utf-8")) == legacy
    migrated = json.loads(dest.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["checksum"] == _checksum_for(migrated)


def test_migrate_missing_file_exits_1(tmp_path, capsys):
    rc = cmd_pak_migrate(SimpleNamespace(pak_file=str(tmp_path / "nope.pak.json"), output=None))
    assert rc == 1
    assert "file not found" in capsys.readouterr().err


def test_parser_registers_migrate_action(tmp_path):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    build_pak_parser(sub)
    args = parser.parse_args(["pak", "migrate", str(tmp_path / "x.pak.json")])
    assert args.pak_action == "migrate"
    assert args.output is None


# ---------------------------------------------------------------------------
# upgrade_pak_payload — normalization contract
# ---------------------------------------------------------------------------


def test_upgrade_leaves_canonical_wire_form_alone():
    wire = {
        "pak_id": "vault:x#y",
        "pak_type": "vault",
        "title": "t",
        "summary": "s",
        "scope": {"user": None, "project": "p", "topic": None},
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
    upgraded, changed = upgrade_pak_payload(wire)
    assert changed is False
    assert upgraded == wire


def test_upgrade_is_pure_and_warning_free(tmp_path):
    src = _make_src(tmp_path)
    legacy = _legacy_v1_payload(src)
    frozen = json.loads(json.dumps(legacy))
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        upgraded, changed = upgrade_pak_payload(legacy)
    assert changed is True
    assert legacy == frozen  # input not mutated
    assert upgraded["pak_type"] == "recall"
    # Idempotent: a second pass is a no-op.
    again, changed_again = upgrade_pak_payload(upgraded)
    assert changed_again is False
    assert again == upgraded


def test_upgrade_keeps_unknown_subtype_verbatim():
    payload = {"schema_version": 1, "pak_type": "mystery_future_type", "anchors": []}
    upgraded, changed = upgrade_pak_payload(payload)
    assert changed is True
    assert upgraded["pak_type"] == "mystery_future_type"


# ---------------------------------------------------------------------------
# contract-side tolerance — extended sub-records parse cleanly
# ---------------------------------------------------------------------------


def test_from_dict_tolerates_extended_anchor_and_scope_fields(tmp_path):
    payload, _, _ = _create_payload(tmp_path)
    payload["scope"]["future_scope_field"] = "ignored"
    payload["anchors"][0]["future_anchor_field"] = "ignored"
    pak = Pak.from_dict(payload)
    assert pak.anchors[0].anchor_id == payload["anchors"][0]["anchor_id"]
    assert pak.scope.project is None


# ---------------------------------------------------------------------------
# display convergence — one view for file-form and wire-form
# ---------------------------------------------------------------------------


def test_display_converged_for_v1_and_v2(tmp_path, capsys):
    # v2 (created) file
    _, v2_file, _ = _create_payload(tmp_path)
    assert cmd_pak_inspect(SimpleNamespace(pak_ref=str(v2_file), json=False)) == 0
    v2_out = capsys.readouterr().out

    # v1 (legacy) file
    legacy_dir = tmp_path / "legacy_home"
    legacy_dir.mkdir()
    _, v1_file, _ = _write_legacy_pak(legacy_dir)
    assert cmd_pak_inspect(SimpleNamespace(pak_ref=str(v1_file), json=False)) == 0
    v1_out = capsys.readouterr().out

    for out in (v2_out, v1_out):
        assert "type        : recall" in out
        assert "status      :" in out
        assert "authority   :" in out
        assert "anchors     :" in out
        assert "checksum    :" in out
    # Only the legacy file carries the migration hint.
    assert "pak migrate" not in v2_out
    assert "pak migrate" in v1_out
