"""Tests for VDS-01 vault.yaml config schema + index health metadata."""

from __future__ import annotations

import os

import pytest

from tokenpak.vault.config import (
    SCHEMA_VERSION,
    VaultConfig,
    VaultPathEntry,
    get_default_index_path,
    load_config,
    load_health,
    save_config,
    update_health,
)


def test_empty_config_round_trip():
    """Empty VaultConfig serializes + deserializes cleanly."""
    c = VaultConfig()
    d = c.to_dict()
    assert d == {"version": SCHEMA_VERSION, "paths": []}
    c2 = VaultConfig.from_dict(d)
    assert c2.version == SCHEMA_VERSION
    assert c2.paths == []


def test_path_entry_with_schedule():
    """Entry preserves schedule + path verbatim."""
    e = VaultPathEntry(path="~/projects/myapp", schedule="every 4h")
    assert e.path == "~/projects/myapp"
    assert e.schedule == "every 4h"
    assert e.last_indexed_ts is None
    assert e.last_index_health is None


def test_config_round_trip_with_paths():
    """Multiple registered paths survive round-trip."""
    c = VaultConfig(paths=[
        VaultPathEntry(path="~/a", schedule="every 4h"),
        VaultPathEntry(path="~/b", last_indexed_ts=1714312345, last_index_health="ok"),
    ])
    c2 = VaultConfig.from_dict(c.to_dict())
    assert len(c2.paths) == 2
    assert c2.paths[0].path == "~/a"
    assert c2.paths[0].schedule == "every 4h"
    assert c2.paths[1].last_indexed_ts == 1714312345
    assert c2.paths[1].last_index_health == "ok"


def test_unsupported_version_raises():
    """v2+ data refused gracefully."""
    with pytest.raises(ValueError, match="version 2 unsupported"):
        VaultConfig.from_dict({"version": 2, "paths": []})


def test_malformed_paths_skipped():
    """Non-dict path entries dropped silently."""
    c = VaultConfig.from_dict({
        "version": 1,
        "paths": [
            "not-a-dict",  # ignored
            {"path": "~/valid"},
            {"no_path_key": True},  # ignored
        ],
    })
    assert len(c.paths) == 1
    assert c.paths[0].path == "~/valid"


def test_load_config_missing_returns_empty(monkeypatch, tmp_path):
    """Loading absent vault.yaml returns empty config."""
    cfg_path = tmp_path / "absent" / "vault.yaml"
    monkeypatch.setenv("TOKENPAK_VAULT_CONFIG", str(cfg_path))
    c = load_config()
    assert c.paths == []


def test_save_then_load_round_trip(monkeypatch, tmp_path):
    """save_config() + load_config() round-trips."""
    cfg_path = tmp_path / "vault.yaml"
    monkeypatch.setenv("TOKENPAK_VAULT_CONFIG", str(cfg_path))

    original = VaultConfig(paths=[
        VaultPathEntry(path="~/projects/x", schedule="every 2h"),
        VaultPathEntry(path="/tmp/sandbox"),
    ])
    save_config(original)

    assert cfg_path.exists()
    loaded = load_config()
    assert len(loaded.paths) == 2
    assert loaded.paths[0].schedule == "every 2h"
    assert loaded.paths[1].path == "/tmp/sandbox"


def test_save_atomic_no_stray_tmp(monkeypatch, tmp_path):
    """save_config() leaves no .tmp.* files after success."""
    cfg_path = tmp_path / "vault.yaml"
    monkeypatch.setenv("TOKENPAK_VAULT_CONFIG", str(cfg_path))

    save_config(VaultConfig(paths=[VaultPathEntry(path="~/a")]))
    stray = [
        p for p in os.listdir(tmp_path)
        if p.startswith("vault.yaml.tmp.")
    ]
    assert stray == []


def test_find_path_matches_expanded_form(monkeypatch, tmp_path):
    """find_path() finds entry by both raw + expanded path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    c = VaultConfig(paths=[VaultPathEntry(path="~/myproject")])
    # Match by raw form
    e = c.find_path("~/myproject")
    assert e is not None
    # Match by expanded form (resolves to tmp_path/myproject which doesn't
    # exist on disk; realpath collapses but still matches because both
    # sides go through realpath(expanduser))
    os.makedirs(os.path.join(tmp_path, "myproject"), exist_ok=True)
    e2 = c.find_path(str(tmp_path / "myproject"))
    assert e2 is not None


def test_health_update_round_trip(monkeypatch, tmp_path):
    """update_health() persists status + ts; load_health() reads it back."""
    health_path = tmp_path / "vault-index-health.json"
    monkeypatch.setenv("TOKENPAK_VAULT_HEALTH_PATH", str(health_path))

    update_health("/tmp/sandbox", "ok", ts=1714400000)
    h = load_health()
    key = os.path.realpath("/tmp/sandbox")
    assert key in h
    assert h[key]["status"] == "ok"
    assert h[key]["ts"] == 1714400000


def test_health_invalid_status_rejected(monkeypatch, tmp_path):
    """update_health() rejects non-enum status."""
    monkeypatch.setenv("TOKENPAK_VAULT_HEALTH_PATH", str(tmp_path / "h.json"))
    with pytest.raises(ValueError):
        update_health("/tmp/x", "broken")  # not in {ok, stale, error}


def test_default_index_path_env_override(monkeypatch):
    """TOKENPAK_VAULT_INDEX_PATH wins."""
    monkeypatch.setenv("TOKENPAK_VAULT_INDEX_PATH", "/custom/index/path")
    assert get_default_index_path() == "/custom/index/path"


def test_default_index_path_vault_dir_present(monkeypatch, tmp_path):
    """If ~/vault/ exists, default to ~/vault/.tokenpak."""
    home = tmp_path / "fake-home"
    (home / "vault").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TOKENPAK_VAULT_INDEX_PATH", raising=False)
    assert get_default_index_path() == str(home / "vault" / ".tokenpak")


def test_default_index_path_no_vault_dir(monkeypatch, tmp_path):
    """If ~/vault/ absent, default to ~/.tokenpak/vault_index/."""
    home = tmp_path / "fresh-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TOKENPAK_VAULT_INDEX_PATH", raising=False)
    assert get_default_index_path() == str(home / ".tokenpak" / "vault_index")
