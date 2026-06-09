"""tests/cli/test_shell_detect.py

Covers tokenpak._formatting.shell_detect — the cross-platform shell
classifier and env-var renderer.

Four branches:
  · posix
  · cmd
  · powershell
  · undetected fallback (i.e. unknown TOKENPAK_SHELL override → fall
    through to platform-based detection)
"""

from __future__ import annotations

import pytest

from tokenpak._formatting import shell_detect

# ---------------------------------------------------------------------------
# detect_shell — environment override
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("override", ["posix", "cmd", "powershell"])
def test_detect_shell_honors_override(monkeypatch, override):
    monkeypatch.setenv("TOKENPAK_SHELL", override)
    assert shell_detect.detect_shell() == override


def test_detect_shell_uppercase_override(monkeypatch):
    monkeypatch.setenv("TOKENPAK_SHELL", "POWERSHELL")
    assert shell_detect.detect_shell() == "powershell"


def test_detect_shell_ignores_unknown_override(monkeypatch):
    """Unknown override falls back to platform-based detection (not a hard error)."""
    monkeypatch.setenv("TOKENPAK_SHELL", "fish-but-actually-zsh")
    monkeypatch.setattr(shell_detect.sys, "platform", "linux")
    assert shell_detect.detect_shell() == "posix"


# ---------------------------------------------------------------------------
# detect_shell — platform-based detection
# ---------------------------------------------------------------------------


def test_detect_shell_posix_on_linux(monkeypatch):
    monkeypatch.delenv("TOKENPAK_SHELL", raising=False)
    monkeypatch.setattr(shell_detect.sys, "platform", "linux")
    assert shell_detect.detect_shell() == "posix"


def test_detect_shell_posix_on_darwin(monkeypatch):
    monkeypatch.delenv("TOKENPAK_SHELL", raising=False)
    monkeypatch.setattr(shell_detect.sys, "platform", "darwin")
    assert shell_detect.detect_shell() == "posix"


def test_detect_shell_powershell_on_windows_with_psmodulepath(monkeypatch):
    monkeypatch.delenv("TOKENPAK_SHELL", raising=False)
    monkeypatch.setattr(shell_detect.sys, "platform", "win32")
    monkeypatch.setenv("PSModulePath", r"C:\WINDOWS\system32\WindowsPowerShell\v1.0\Modules")
    assert shell_detect.detect_shell() == "powershell"


def test_detect_shell_cmd_on_windows_no_psmodulepath(monkeypatch):
    monkeypatch.delenv("TOKENPAK_SHELL", raising=False)
    monkeypatch.delenv("PSModulePath", raising=False)
    monkeypatch.setattr(shell_detect.sys, "platform", "win32")
    assert shell_detect.detect_shell() == "cmd"


# ---------------------------------------------------------------------------
# render_env_var — per-shell syntax
# ---------------------------------------------------------------------------


def test_render_env_var_posix():
    out = shell_detect.render_env_var("ANTHROPIC_BASE_URL", "http://localhost:8766", "posix")
    assert out == "export ANTHROPIC_BASE_URL='http://localhost:8766'"


def test_render_env_var_cmd():
    out = shell_detect.render_env_var("ANTHROPIC_BASE_URL", "http://localhost:8766", "cmd")
    assert out == "set ANTHROPIC_BASE_URL=http://localhost:8766"


def test_render_env_var_powershell():
    out = shell_detect.render_env_var("ANTHROPIC_BASE_URL", "http://localhost:8766", "powershell")
    assert out == "$env:ANTHROPIC_BASE_URL='http://localhost:8766'"


def test_render_env_var_defaults_to_detected_shell(monkeypatch):
    """No `shell=` arg → uses detect_shell() result."""
    monkeypatch.setenv("TOKENPAK_SHELL", "cmd")
    out = shell_detect.render_env_var("FOO", "bar")
    assert out == "set FOO=bar"


# ---------------------------------------------------------------------------
# render_env_var — quote escaping (round-trip safety)
# ---------------------------------------------------------------------------


def test_render_env_var_posix_escapes_single_quotes():
    out = shell_detect.render_env_var("KEY", "ab'cd", "posix")
    # posix idiom: close-quote, escaped-quote, open-quote
    assert out == "export KEY='ab'\"'\"'cd'"


def test_render_env_var_powershell_escapes_single_quotes():
    out = shell_detect.render_env_var("KEY", "ab'cd", "powershell")
    # powershell single-quote escape is double-up
    assert out == "$env:KEY='ab''cd'"


def test_render_env_var_cmd_no_escape():
    """cmd reads value verbatim to end-of-line; no quoting added."""
    out = shell_detect.render_env_var("KEY", "ab'cd", "cmd")
    assert out == "set KEY=ab'cd"
