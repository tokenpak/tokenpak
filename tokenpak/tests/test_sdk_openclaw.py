"""Tests for tokenpak.sdk.openclaw multi-config discovery + setup.

Covers the 2026-04-18 regression where `setup_openclaw()` only ever
touched `~/.openclaw/openclaw.json` and missed sibling installs like
`~/.openclaw-governor/openclaw.json` (the governor install).
"""

from __future__ import annotations

import json
from pathlib import Path

from tokenpak.sdk.openclaw import (
    detect_openclaw,
    discover_openclaw_configs,
    setup_openclaw,
)


def _write_stub_config(path: Path) -> None:
    """Write a minimal but realistic openclaw.json stub at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "models": {
                    "mode": "merge",
                    "providers": {
                        # pre-existing non-tokenpak provider should survive untouched
                        "anthropic": {
                            "baseUrl": "https://api.anthropic.com",
                            "api": "anthropic-messages",
                            "models": [
                                {
                                    "id": "claude-opus-4-7",
                                    "name": "Opus 4.7",
                                    "cost": {
                                        "input": 0,
                                        "output": 0,
                                        "cacheRead": 0,
                                        "cacheWrite": 0,
                                    },
                                },
                            ],
                        },
                    },
                },
            }
        )
    )


def test_discover_finds_multiple_configs(tmp_path, monkeypatch):
    """Both `.openclaw/` and `.openclaw-governor/` configs are discovered."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)

    main_cfg = home / ".openclaw" / "openclaw.json"
    governor_cfg = home / ".openclaw-governor" / "openclaw.json"
    _write_stub_config(main_cfg)
    _write_stub_config(governor_cfg)

    found = discover_openclaw_configs()

    assert main_cfg in found
    assert governor_cfg in found
    assert len(found) == 2
    assert detect_openclaw() is True


def test_discover_honors_env_var(tmp_path, monkeypatch):
    """OPENCLAW_CONFIG_PATH pins discovery to a single target."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)

    main_cfg = home / ".openclaw" / "openclaw.json"
    governor_cfg = home / ".openclaw-governor" / "openclaw.json"
    _write_stub_config(main_cfg)
    _write_stub_config(governor_cfg)

    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(governor_cfg))
    found = discover_openclaw_configs()

    assert found == [governor_cfg]


def test_discover_empty_when_nothing_installed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)

    assert discover_openclaw_configs() == []
    assert detect_openclaw() is False


def test_setup_updates_all_configs(tmp_path, monkeypatch):
    """Default setup iterates every discovered install."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)

    main_cfg = home / ".openclaw" / "openclaw.json"
    governor_cfg = home / ".openclaw-governor" / "openclaw.json"
    _write_stub_config(main_cfg)
    _write_stub_config(governor_cfg)

    # Use a localhost URL that won't answer — forces template fallback path.
    result = setup_openclaw(proxy_url="http://127.0.0.1:1")

    assert "error" not in result, result
    assert len(result["configs"]) == 2

    for path in (main_cfg, governor_cfg):
        loaded = json.loads(path.read_text())
        providers = loaded["models"]["providers"]
        # tokenpak-claude-code is the feature-under-test; should now exist
        assert "tokenpak-claude-code" in providers, path
        # sanity: original non-tokenpak provider preserved
        assert "anthropic" in providers, path
        # template providers added too
        assert "tokenpak-anthropic" in providers, path
        # auth profiles for every tokenpak-* provider — including
        # tokenpak-claude-code (regression: previously only templated
        # providers got auth profiles, so the claude-code path stayed
        # unreachable from Telegram even after the provider existed).
        profiles = loaded.get("auth", {}).get("profiles", {})
        assert "tokenpak-anthropic:manual" in profiles, path
        assert "tokenpak-claude-code:manual" in profiles, path
        assert "tokenpak-gemini:manual" in profiles, path


def test_setup_with_explicit_path_targets_one(tmp_path, monkeypatch):
    """Explicit config_path does not touch sibling installs."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)

    main_cfg = home / ".openclaw" / "openclaw.json"
    governor_cfg = home / ".openclaw-governor" / "openclaw.json"
    _write_stub_config(main_cfg)
    _write_stub_config(governor_cfg)

    governor_before = governor_cfg.read_text()

    result = setup_openclaw(proxy_url="http://127.0.0.1:1", config_path=main_cfg)

    assert "error" not in result
    assert len(result["configs"]) == 1
    assert result["configs"][0]["path"] == str(main_cfg)

    # main updated
    assert "tokenpak-claude-code" in json.loads(main_cfg.read_text())["models"]["providers"]
    # governor untouched (byte-for-byte)
    assert governor_cfg.read_text() == governor_before


def test_setup_no_install_returns_error(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)

    result = setup_openclaw(proxy_url="http://127.0.0.1:1")

    assert "error" in result
    assert "No OpenClaw install detected" in result["error"]
