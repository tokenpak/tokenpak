# SPDX-License-Identifier: Apache-2.0
"""Tests for VDS-01 — ``vault.yaml`` v1 schema + ``tokenpak index`` reindex flags.

Covers:
* round-trip load/save of ``~/.tokenpak/vault.yaml``
* ``add_path`` / ``remove_path`` idempotency
* ``update_index_health`` stamps the doctor-readable fields
* ``TOKENPAK_VAULT_CONFIG`` and ``TOKENPAK_VAULT_INDEX_PATH`` env overrides
* ``--reindex-path`` against a registered + unregistered path
* ``--reindex-all`` over a temp registry on a temp directory
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from tokenpak.vault import config as vault_config

# ---------------------------------------------------------------------------
# vault.yaml schema
# ---------------------------------------------------------------------------

def test_default_config_path_uses_env_override(tmp_path, monkeypatch):
    """TOKENPAK_VAULT_CONFIG overrides the default ~/.tokenpak/vault.yaml."""
    custom = tmp_path / "custom.yaml"
    monkeypatch.setenv("TOKENPAK_VAULT_CONFIG", str(custom))
    assert vault_config.default_config_path() == custom


def test_default_index_path_uses_env_override(tmp_path, monkeypatch):
    """TOKENPAK_VAULT_INDEX_PATH overrides the default ~/vault/.tokenpak."""
    custom = tmp_path / "vault-out"
    monkeypatch.setenv("TOKENPAK_VAULT_INDEX_PATH", str(custom))
    assert vault_config.default_index_path() == custom


def test_default_index_path_proxy_compatible_when_unset(monkeypatch):
    """Default falls back to ~/vault/.tokenpak (proxy-compatible per spec)."""
    monkeypatch.delenv("TOKENPAK_VAULT_INDEX_PATH", raising=False)
    assert vault_config.default_index_path() == Path.home() / "vault" / ".tokenpak"


def test_load_missing_file_returns_empty_v1(tmp_path):
    """Missing vault.yaml returns an empty v1 config, not an error."""
    cfg = vault_config.load(tmp_path / "absent.yaml")
    assert cfg.version == vault_config.SCHEMA_VERSION
    assert cfg.paths == []


def test_save_then_load_roundtrip(tmp_path):
    """A registered path round-trips through save/load with all fields."""
    cfg_path = tmp_path / "vault.yaml"
    cfg = vault_config.VaultConfig(
        paths=[
            vault_config.VaultPathEntry(
                path=str(tmp_path / "proj"),
                schedule="every 6 hours",
                expected_interval_seconds=21600,
            )
        ]
    )
    vault_config.save(cfg, cfg_path)
    loaded = vault_config.load(cfg_path)
    assert loaded.version == 1
    assert len(loaded.paths) == 1
    entry = loaded.paths[0]
    assert entry.path == str(tmp_path / "proj")
    assert entry.schedule == "every 6 hours"
    assert entry.expected_interval_seconds == 21600


def test_load_rejects_unsupported_schema_version(tmp_path):
    """A future schema version raises rather than silently downgrading."""
    cfg_path = tmp_path / "vault.yaml"
    cfg_path.write_text("version: 99\npaths: []\n")
    with pytest.raises(ValueError, match="schema version 99"):
        vault_config.load(cfg_path)


def test_add_path_is_idempotent(tmp_path):
    """add_path twice with same path yields one entry; schedule is updated."""
    cfg = vault_config.VaultConfig()
    target = str(tmp_path / "p")
    Path(target).mkdir()
    vault_config.add_path(cfg, target, schedule="hourly")
    vault_config.add_path(cfg, target, schedule="every 6 hours")
    assert len(cfg.paths) == 1
    assert cfg.paths[0].schedule == "every 6 hours"


def test_remove_path_returns_false_when_absent(tmp_path):
    """remove_path on an unregistered path is a no-op returning False."""
    cfg = vault_config.VaultConfig()
    assert vault_config.remove_path(cfg, str(tmp_path / "ghost")) is False


def test_remove_path_removes_registered_entry(tmp_path):
    """remove_path drops the entry and returns True."""
    cfg = vault_config.VaultConfig()
    target = str(tmp_path / "p")
    Path(target).mkdir()
    vault_config.add_path(cfg, target)
    assert vault_config.remove_path(cfg, target) is True
    assert cfg.paths == []


def test_update_index_health_stamps_fields(tmp_path):
    """update_index_health writes status + duration + indexed_at on a registered path."""
    cfg = vault_config.VaultConfig()
    target = str(tmp_path / "p")
    Path(target).mkdir()
    vault_config.add_path(cfg, target)
    entry = vault_config.update_index_health(
        cfg, target, status="ok", duration_ms=1234, files_indexed=7
    )
    assert entry is not None
    assert entry.last_index_status == "ok"
    assert entry.last_index_duration_ms == 1234
    assert entry.last_index_files == 7
    # ISO-8601 zulu timestamp
    assert entry.last_indexed and entry.last_indexed.endswith("Z")


def test_update_index_health_returns_none_for_unregistered_path(tmp_path):
    """update_index_health on an unknown path is a no-op returning None."""
    cfg = vault_config.VaultConfig()
    assert (
        vault_config.update_index_health(cfg, str(tmp_path / "ghost"), status="ok")
        is None
    )


# ---------------------------------------------------------------------------
# CLI flag wiring
# ---------------------------------------------------------------------------

def test_argparse_registers_reindex_flags():
    """`tokenpak index --reindex-all` and `--reindex-path X` parse cleanly."""
    from tokenpak._cli_core import build_parser

    parser = build_parser()
    a = parser.parse_args(["index", "--reindex-all"])
    assert a.reindex_all is True
    assert a.reindex_path is None
    b = parser.parse_args(["index", "--reindex-path", "/tmp/fake"])
    assert b.reindex_all is False
    assert b.reindex_path == "/tmp/fake"


def _make_args(**overrides) -> argparse.Namespace:
    """Build a Namespace mirroring what argparse would hand to cmd_index."""
    base = dict(
        help=False,
        db=".tokenpak/registry.db",
        command="index",
        directory=None,
        status=False,
        budget=8000,
        workers=1,
        auto_workers=False,
        recalibrate=False,
        calibration_rounds=2,
        max_workers=8,
        watch=False,
        debounce=500,
        no_treesitter=True,  # avoid tree-sitter setup costs in tests
        reindex_all=False,
        reindex_path=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_reindex_path_unregistered_exits_nonzero(tmp_path, monkeypatch, capsys):
    """`--reindex-path` against an unregistered dir exits with a clear error."""
    cfg_path = tmp_path / "vault.yaml"
    monkeypatch.setenv("TOKENPAK_VAULT_CONFIG", str(cfg_path))
    monkeypatch.setenv("TOKENPAK_VAULT_INDEX_PATH", str(tmp_path / "out"))
    vault_config.save(vault_config.VaultConfig(), cfg_path)

    from tokenpak._cli_core import _cmd_reindex

    args = _make_args(reindex_path=str(tmp_path / "not-registered"))
    with pytest.raises(SystemExit) as excinfo:
        _cmd_reindex(args)
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "not registered" in err


def test_reindex_all_with_empty_config_exits_nonzero(tmp_path, monkeypatch, capsys):
    """`--reindex-all` against an empty vault.yaml exits with a clear error."""
    cfg_path = tmp_path / "vault.yaml"
    monkeypatch.setenv("TOKENPAK_VAULT_CONFIG", str(cfg_path))
    monkeypatch.setenv("TOKENPAK_VAULT_INDEX_PATH", str(tmp_path / "out"))

    from tokenpak._cli_core import _cmd_reindex

    args = _make_args(reindex_all=True)
    with pytest.raises(SystemExit) as excinfo:
        _cmd_reindex(args)
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "No registered vault paths" in err


def test_reindex_path_indexes_registered_dir_and_stamps_health(
    tmp_path, monkeypatch, capsys
):
    """`--reindex-path` indexes a registered dir and updates health metadata."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "hello.py").write_text("def hello():\n    return 'world'\n")
    (proj / "README.md").write_text("# Hello\n\nDocs.\n")

    cfg_path = tmp_path / "vault.yaml"
    out_dir = tmp_path / "out"
    monkeypatch.setenv("TOKENPAK_VAULT_CONFIG", str(cfg_path))
    monkeypatch.setenv("TOKENPAK_VAULT_INDEX_PATH", str(out_dir))

    cfg = vault_config.VaultConfig()
    vault_config.add_path(cfg, str(proj), schedule="every 6 hours")
    vault_config.save(cfg, cfg_path)

    from tokenpak._cli_core import _cmd_reindex

    args = _make_args(reindex_path=str(proj))
    _cmd_reindex(args)

    # Registry DB was created under the env-overridden index root.
    assert (out_dir / "registry.db").exists()

    # Health metadata was stamped back into vault.yaml.
    reloaded = vault_config.load(cfg_path)
    entry = reloaded.find(str(proj))
    assert entry is not None
    assert entry.last_index_status == "ok"
    assert entry.last_indexed is not None
    assert (entry.last_index_duration_ms or 0) >= 0
    # We indexed at least one supported file.
    assert (entry.last_index_files or 0) >= 1


def test_reindex_all_indexes_every_registered_path(tmp_path, monkeypatch):
    """`--reindex-all` walks every registered directory in vault.yaml."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "a.py").write_text("def a():\n    return 1\n")
    (b / "b.py").write_text("def b():\n    return 2\n")

    cfg_path = tmp_path / "vault.yaml"
    out_dir = tmp_path / "out"
    monkeypatch.setenv("TOKENPAK_VAULT_CONFIG", str(cfg_path))
    monkeypatch.setenv("TOKENPAK_VAULT_INDEX_PATH", str(out_dir))

    cfg = vault_config.VaultConfig()
    vault_config.add_path(cfg, str(a))
    vault_config.add_path(cfg, str(b))
    vault_config.save(cfg, cfg_path)

    from tokenpak._cli_core import _cmd_reindex

    args = _make_args(reindex_all=True)
    _cmd_reindex(args)

    reloaded = vault_config.load(cfg_path)
    for target in (a, b):
        entry = reloaded.find(str(target))
        assert entry is not None
        assert entry.last_index_status == "ok"
        assert entry.last_indexed is not None
