# SPDX-License-Identifier: Apache-2.0
"""runtime load-order repoint — acceptance coverage (doctor fix).

The runtime loaders (core/config_loader.py for config.yaml, core/config.py
for the config.json toggle layer) must resolve through the canonical home
resolver instead of the hardcoded legacy literal, with drift-respect:

- $TOKENPAK_CONFIG still wins (layer 6).
- Legacy-only installs read legacy unchanged (no regression).
- Canonical-only installs read canonical.
- Split-home with both configs → canonical wins (the D7 remedy).
- Split-home where only legacy holds the file → legacy keeps working
  (no silent config loss mid-migration).
- The loaders never create or move files across homes; JSON→YAML
  auto-migration stays an in-place, same-directory rename.

All tests are hermetic: HOME is a tmp dir, TOKENPAK_HOME/TOKENPAK_CONFIG are
cleared, and the loader's module cache is reset around each test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokenpak.core import config as toggle_config
from tokenpak.core import config_loader


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Fake $HOME with no TokenPak dirs; returns (canonical, legacy) paths."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TOKENPAK_HOME", raising=False)
    monkeypatch.delenv("TOKENPAK_CONFIG", raising=False)
    monkeypatch.setattr(config_loader, "_config", None)
    yield tmp_path / ".tpk", tmp_path / ".tokenpak"
    monkeypatch.setattr(config_loader, "_config", None)


def _put(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# config.yaml resolution (core/config_loader.py)
# ---------------------------------------------------------------------------


class TestYamlResolution:
    def test_tokenpak_config_env_override_wins(self, homes, tmp_path, monkeypatch):
        canonical, legacy = homes
        _put(canonical / "config.yaml", "port: 1111\n")
        explicit = _put(tmp_path / "elsewhere" / "my.yaml", "port: 2222\n")
        monkeypatch.setenv("TOKENPAK_CONFIG", str(explicit))
        assert config_loader.CONFIG_PATH == explicit
        assert config_loader.load_config()["port"] == 2222

    def test_legacy_only_install_reads_legacy_unchanged(self, homes):
        canonical, legacy = homes
        _put(legacy / "config.yaml", "port: 3333\n")
        assert config_loader.CONFIG_PATH == legacy / "config.yaml"
        assert config_loader.load_config()["port"] == 3333

    def test_canonical_only_install_reads_canonical(self, homes):
        canonical, legacy = homes
        _put(canonical / "config.yaml", "port: 4444\n")
        assert config_loader.CONFIG_PATH == canonical / "config.yaml"
        assert config_loader.load_config()["port"] == 4444

    def test_split_home_with_both_configs_canonical_wins(self, homes):
        """The D7 remedy text: canonical (~/.tpk) wins."""
        canonical, legacy = homes
        _put(canonical / "config.yaml", "port: 5555\n")
        _put(legacy / "config.yaml", "port: 6666\n")
        assert config_loader.CONFIG_PATH == canonical / "config.yaml"
        assert config_loader.load_config()["port"] == 5555

    def test_split_home_with_legacy_only_config_keeps_reading_legacy(self, homes):
        """Drift-respect: mid-migration hosts must not silently lose config."""
        canonical, legacy = homes
        canonical.mkdir(parents=True)  # split-home: both dirs exist
        _put(legacy / "config.yaml", "port: 7777\n")
        assert config_loader.CONFIG_PATH == legacy / "config.yaml"
        assert config_loader.load_config()["port"] == 7777

    def test_fresh_host_defaults_to_canonical_path_without_creating_it(self, homes):
        canonical, legacy = homes
        assert config_loader.CONFIG_PATH == canonical / "config.yaml"
        assert config_loader.load_config() == {}
        assert not canonical.exists() and not legacy.exists()  # loader never mkdirs

    def test_resolution_is_call_time_not_import_time(self, homes, monkeypatch):
        canonical, legacy = homes
        _put(legacy / "config.yaml", "port: 1\n")
        assert config_loader.CONFIG_PATH == legacy / "config.yaml"
        _put(canonical / "config.yaml", "port: 2\n")
        assert config_loader.CONFIG_PATH == canonical / "config.yaml"
        monkeypatch.setattr(config_loader, "_config", None)
        assert config_loader.load_config()["port"] == 2


# ---------------------------------------------------------------------------
# JSON → YAML auto-migration (in place, never cross-home)
# ---------------------------------------------------------------------------


class TestJsonAutoMigration:
    def test_legacy_only_json_migrates_in_place(self, homes):
        canonical, legacy = homes
        _put(legacy / "config.json", json.dumps({"port": 8888}))
        cfg = config_loader.load_config()
        assert cfg["port"] == 8888
        assert (legacy / "config.yaml").exists()
        assert (legacy / "config.json.migrated").exists()
        assert not (legacy / "config.json").exists()
        assert not canonical.exists()  # nothing crossed homes

    def test_split_home_legacy_json_migrates_within_legacy(self, homes):
        canonical, legacy = homes
        canonical.mkdir(parents=True)
        _put(legacy / "config.json", json.dumps({"port": 9999}))
        cfg = config_loader.load_config()
        assert cfg["port"] == 9999
        assert (legacy / "config.yaml").exists()
        assert not (canonical / "config.yaml").exists()
        assert not (canonical / "config.json").exists()

    def test_canonical_json_migrates_within_canonical(self, homes):
        canonical, legacy = homes
        _put(canonical / "config.json", json.dumps({"port": 1212}))
        cfg = config_loader.load_config()
        assert cfg["port"] == 1212
        assert (canonical / "config.yaml").exists()
        assert (canonical / "config.json.migrated").exists()
        assert not legacy.exists()

    def test_yaml_present_suppresses_migration(self, homes):
        canonical, legacy = homes
        _put(canonical / "config.yaml", "port: 1\n")
        _put(canonical / "config.json", json.dumps({"port": 2}))
        assert config_loader.load_config()["port"] == 1
        assert (canonical / "config.json").exists()  # untouched


# ---------------------------------------------------------------------------
# config.json toggle layer (core/config.py)
# ---------------------------------------------------------------------------


class TestToggleJsonResolution:
    def test_legacy_resident_file_is_read_and_written_in_place(self, homes):
        canonical, legacy = homes
        canonical.mkdir(parents=True)  # split-home
        _put(legacy / "config.json", json.dumps({"debug": True}))
        assert toggle_config.CONFIG_PATH == legacy / "config.json"
        assert toggle_config.get_debug_enabled() is True
        toggle_config.set_config("stats_footer", True)
        # State stayed together in the legacy file; canonical untouched.
        on_disk = json.loads((legacy / "config.json").read_text())
        assert on_disk == {"debug": True, "stats_footer": True}
        assert not (canonical / "config.json").exists()

    def test_canonical_wins_when_both_files_exist(self, homes):
        canonical, legacy = homes
        _put(canonical / "config.json", json.dumps({"debug": True}))
        _put(legacy / "config.json", json.dumps({"debug": False}))
        assert toggle_config.CONFIG_PATH == canonical / "config.json"
        assert toggle_config.get_debug_enabled() is True

    def test_new_file_lands_in_resolved_home(self, homes):
        canonical, legacy = homes
        toggle_config.set_config("debug", True)
        assert (canonical / "config.json").exists()
        assert not legacy.exists()
        assert toggle_config.get_debug_enabled() is True

    def test_env_var_still_overrides_file(self, homes, monkeypatch):
        canonical, legacy = homes
        _put(canonical / "config.json", json.dumps({"debug": True}))
        monkeypatch.setenv("TOKENPAK_DEBUG", "0")
        assert toggle_config.get_debug_enabled() is False
