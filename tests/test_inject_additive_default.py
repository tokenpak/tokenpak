# SPDX-License-Identifier: Apache-2.0
"""tokenpak-inject.sh — default mode is additive; --exclusive is destructive.

Verifies the standing rule (``feedback_tokenpak_additive_only.md``):

  - Default mode mirrors providers + adds tokenpak-* to the model
    allowlist + copies auth profiles, but does NOT touch the user's
    ``primary``, ``fallbacks``, or per-agent model selections.
  - ``--exclusive`` mode (or ``TOKENPAK_INJECT_EXCLUSIVE=1`` env)
    additionally rewrites primary to ``tokenpak-*`` and clears
    fallbacks.

Tests run the script against synthetic openclaw.json fixtures in tmp
dirs — no host config is touched.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "integrations/openclaw/tokenpak-inject.sh"


def _make_config(tmp_path: Path) -> Path:
    """Synthesize a minimal openclaw.json fixture with two providers,
    a primary, and a fallback chain — the shape inject is supposed to
    leave alone in default mode."""
    cfg_dir = tmp_path / "openclaw"
    cfg_dir.mkdir()
    config = {
        "models": {
            "providers": {
                "anthropic": {
                    "baseUrl": "https://api.anthropic.com",
                    "api": "anthropic-messages",
                    "models": [
                        {"id": "claude-opus-4-7", "name": "Opus 4.7"},
                        {"id": "claude-haiku-4-5", "name": "Haiku 4.5"},
                    ],
                },
                "openai": {
                    "baseUrl": "https://api.openai.com",
                    "api": "openai-responses",
                    "models": [
                        {"id": "gpt-5.4", "name": "GPT 5.4"},
                    ],
                },
            }
        },
        "agents": {
            "defaults": {
                "model": {
                    "primary": "anthropic/claude-opus-4-7",
                    "fallbacks": [
                        "anthropic/claude-haiku-4-5",
                        "openai/gpt-5.4",
                    ],
                },
                "models": {
                    "anthropic/claude-opus-4-7": {},
                    "anthropic/claude-haiku-4-5": {},
                    "openai/gpt-5.4": {},
                },
            },
            "list": [
                {
                    "id": "test-agent",
                    "model": {
                        "primary": "anthropic/claude-haiku-4-5",
                        "fallbacks": ["openai/gpt-5.4"],
                    },
                }
            ],
        },
    }
    path = cfg_dir / "openclaw.json"
    path.write_text(json.dumps(config, indent=2))
    return path


def _run_inject(config_path: Path, *, exclusive: bool) -> subprocess.CompletedProcess:
    """Run the inject script against a tmp config dir."""
    env = os.environ.copy()
    # OPENCLAW_CONFIG_PATH is the env var the script honors to find
    # the config file (instead of the default ~/.openclaw/openclaw.json).
    env["OPENCLAW_CONFIG_PATH"] = str(config_path)
    if exclusive:
        env["TOKENPAK_INJECT_EXCLUSIVE"] = "1"
    else:
        env.pop("TOKENPAK_INJECT_EXCLUSIVE", None)
    return subprocess.run(
        [str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


# ── DEFAULT MODE: additive only ──────────────────────────────────────


class TestDefaultModeAdditive:
    def test_mirrors_providers_without_removing_originals(self, tmp_path: Path):
        cfg_path = _make_config(tmp_path)
        result = _run_inject(cfg_path, exclusive=False)
        assert result.returncode == 0, f"script failed:\n{result.stderr}"
        config = json.loads(cfg_path.read_text())
        providers = config["models"]["providers"]
        # Originals preserved
        assert "anthropic" in providers
        assert "openai" in providers
        # Tokenpak mirrors added
        assert "tokenpak-anthropic" in providers
        assert "tokenpak-openai" in providers

    def test_does_not_touch_primary_in_defaults(self, tmp_path: Path):
        cfg_path = _make_config(tmp_path)
        _run_inject(cfg_path, exclusive=False)
        config = json.loads(cfg_path.read_text())
        primary = config["agents"]["defaults"]["model"]["primary"]
        assert primary == "anthropic/claude-opus-4-7", (
            f"default mode mutated primary: {primary!r}"
        )

    def test_does_not_clear_fallbacks_in_defaults(self, tmp_path: Path):
        cfg_path = _make_config(tmp_path)
        _run_inject(cfg_path, exclusive=False)
        config = json.loads(cfg_path.read_text())
        fallbacks = config["agents"]["defaults"]["model"]["fallbacks"]
        assert fallbacks == [
            "anthropic/claude-haiku-4-5",
            "openai/gpt-5.4",
        ], f"default mode mutated fallbacks: {fallbacks!r}"

    def test_does_not_touch_per_agent_model(self, tmp_path: Path):
        cfg_path = _make_config(tmp_path)
        _run_inject(cfg_path, exclusive=False)
        config = json.loads(cfg_path.read_text())
        agent = config["agents"]["list"][0]
        assert agent["model"]["primary"] == "anthropic/claude-haiku-4-5"
        assert agent["model"]["fallbacks"] == ["openai/gpt-5.4"]

    def test_adds_tokenpak_models_to_allowlist(self, tmp_path: Path):
        cfg_path = _make_config(tmp_path)
        _run_inject(cfg_path, exclusive=False)
        config = json.loads(cfg_path.read_text())
        allowlist = config["agents"]["defaults"]["models"]
        # Originals preserved
        assert "anthropic/claude-opus-4-7" in allowlist
        # Tokenpak version added
        assert "tokenpak-anthropic/claude-opus-4-7" in allowlist


# ── EXCLUSIVE MODE: destructive routing ──────────────────────────────


class TestExclusiveMode:
    def test_replaces_primary_with_tokenpak_version(self, tmp_path: Path):
        cfg_path = _make_config(tmp_path)
        _run_inject(cfg_path, exclusive=True)
        config = json.loads(cfg_path.read_text())
        primary = config["agents"]["defaults"]["model"]["primary"]
        assert primary == "tokenpak-anthropic/claude-opus-4-7", (
            f"exclusive mode should rewrite primary, got {primary!r}"
        )

    def test_clears_fallbacks(self, tmp_path: Path):
        cfg_path = _make_config(tmp_path)
        _run_inject(cfg_path, exclusive=True)
        config = json.loads(cfg_path.read_text())
        fallbacks = config["agents"]["defaults"]["model"]["fallbacks"]
        assert fallbacks == []

    def test_still_does_not_remove_original_providers(self, tmp_path: Path):
        # Even in exclusive mode, original provider entries must
        # remain — the user can still manually flip back to a raw
        # provider via OpenClaw's UI/config.
        cfg_path = _make_config(tmp_path)
        _run_inject(cfg_path, exclusive=True)
        config = json.loads(cfg_path.read_text())
        providers = config["models"]["providers"]
        assert "anthropic" in providers
        assert "openai" in providers


# ── Mode reporting in script output ──────────────────────────────────


class TestModeLogging:
    def test_default_mode_logs_additive(self, tmp_path: Path):
        cfg_path = _make_config(tmp_path)
        result = _run_inject(cfg_path, exclusive=False)
        assert "additive" in result.stdout.lower()
        assert "exclusive" not in result.stdout.lower() or "rewrites" not in result.stdout.lower()

    def test_exclusive_mode_logs_warning(self, tmp_path: Path):
        cfg_path = _make_config(tmp_path)
        result = _run_inject(cfg_path, exclusive=True)
        assert "exclusive" in result.stdout.lower()


# ── Idempotent in both modes ─────────────────────────────────────────


class TestIdempotent:
    @pytest.mark.parametrize("exclusive", [False, True])
    def test_running_twice_yields_same_output(self, tmp_path: Path, exclusive):
        cfg_path = _make_config(tmp_path)
        _run_inject(cfg_path, exclusive=exclusive)
        first = json.loads(cfg_path.read_text())
        _run_inject(cfg_path, exclusive=exclusive)
        second = json.loads(cfg_path.read_text())
        assert first == second
