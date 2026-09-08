# SPDX-License-Identifier: Apache-2.0
"""Opt-in real native TUI capture; no prompts are submitted to a provider.

Run TOKENPAK_TEST_NATIVE_TUI=1 pytest -q tests/companion/test_forecast_native_terminal.py.
Requires the real client binaries and tmux. ASCII cache lines use one byte per
display cell as the width oracle; both the forecast and length-matched plain
control are exercised through the host after input editing and a redraw.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from tests.session_economics_fixtures import learning_payload
from tokenpak.companion import session_binding
from tokenpak.companion.statusline.launch import SCRIPTS, start_panel
from tokenpak.core.contracts.session_economics import SessionEconomics
from tokenpak.status.display import WIDTHS, render
from tokenpak.status.snapshot import StatusSnapshot
from tokenpak.status.worker import atomic_write

pytestmark = pytest.mark.skipif(
    os.environ.get("TOKENPAK_TEST_NATIVE_TUI") != "1",
    reason="explicit native TUI verification requires installed clients",
)


@pytest.mark.parametrize("client", ["claude", "codex"])
@pytest.mark.parametrize("width", [40, 80, 120])
def test_native_forecast_width_edit_redraw(tmp_path, monkeypatch, client, width):
    assert shutil.which("tmux") and shutil.which(client), "native prerequisites missing"
    native = shutil.which(client)
    assert native
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "project"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    monkeypatch.setenv(session_binding.ENV, str(run))
    sid = "12345678-1234-4234-8234-123456789012"
    session_binding.write_session(sid)
    data = learning_payload()
    data["session"]["id"] = sid
    fact = StatusSnapshot(sid, time.time(), "proxy", SessionEconomics.from_dict(data), "")
    cache = run / "status"
    cache.mkdir(mode=0o700)
    expiration = int(time.time()) + 180

    def write_line(control=False):
        lines = [(w, render(fact, w)) for w in WIDTHS]
        atomic_write(
            cache / f"{sid}.line",
            f"{expiration}\n"
            + "".join(f"{w}|{('x' * len(line)) if control else line}\n" for w, line in lines),
        )

    write_line()
    env = {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        session_binding.ENV: str(run),
        "NO_COLOR": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    if client == "claude":
        (home / ".claude.json").write_text(
            json.dumps(
                {
                    "hasCompletedOnboarding": True,
                    "theme": "dark",
                    "projects": {str(workspace): {"hasTrustDialogAccepted": True}},
                }
            )
        )
        settings = home / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "statusLine": {
                        "type": "command",
                        "command": shlex.join(["bash", str(SCRIPTS / "native.sh")]),
                        "refreshInterval": 2,
                    }
                }
            )
        )
        env.update(ANTHROPIC_API_KEY="local-test-fixture", ANTHROPIC_BASE_URL="http://127.0.0.1:1")
        command = [
            native,
            "--session-id",
            sid,
            "--settings",
            str(settings),
            "--setting-sources",
            "user",
        ]
    else:
        (home / ".codex").mkdir()
        env["CODEX_HOME"] = str(home / ".codex")
        command = [
            native,
            "-c",
            'model_provider="fixture"',
            "-c",
            'model_providers.fixture.name="Fixture"',
            "-c",
            'model_providers.fixture.base_url="http://127.0.0.1:1/v1"',
            "-c",
            'model_providers.fixture.wire_api="responses"',
            "-c",
            "model_providers.fixture.requires_openai_auth=false",
            "-c",
            f'projects.{json.dumps(str(workspace))}.trust_level="trusted"',
            "--no-alt-screen",
        ]
    server = "forecast-test-" + uuid.uuid4().hex
    base = ["tmux", "-L", server, "-f", os.devnull]

    def tmux(*args, check=True):
        return subprocess.run(
            [*base, *args], env=env, text=True, capture_output=True, timeout=5, check=check
        ).stdout.strip()

    gate = tmp_path / "start"
    wrapper = (
        f"while [ ! -f {shlex.quote(str(gate))} ]; do sleep 0.1; done; exec {shlex.join(command)}"
    )
    try:
        tmux(
            "new-session",
            "-d",
            "-s",
            "probe",
            "-x",
            str(width),
            "-y",
            "24",
            "-c",
            str(workspace),
            wrapper,
        )
        tmux("set-option", "-t", "probe", "status", "off")
        tmux("pipe-pane", "-O", "-t", "%0", "cat > " + shlex.quote(str(tmp_path / "native.bytes")))
        gate.touch()
        if client == "codex":
            socket = tmux("display-message", "-p", "-t", "%0", "#{socket_path},#{pid},0")
            pane = start_panel({**env, "TMUX": socket, "TMUX_PANE": "%0"})
            assert pane
        else:
            pane = "%0"
        deadline = time.monotonic() + 25
        visible = ""
        while time.monotonic() < deadline:
            visible = tmux("capture-pane", "-p", "-t", pane)
            native_view = tmux("capture-pane", "-p", "-t", "%0")
            ready = (
                "Claude Code v" in native_view
                if client == "claude"
                else "OpenAI Codex" in native_view
            )
            if ready and ("TP 12345678" in visible or "TP | guard" in visible):
                break
            if "use this api key" in native_view.lower():
                tmux("send-keys", "-t", "%0", "Up", "Enter")
            elif any(word in native_view.lower() for word in ("trust this", "yes, i trust")):
                tmux("send-keys", "-t", "%0", "Enter")
            time.sleep(0.25)
        (tmp_path / "forecast.screen").write_text(visible)
        assert "TP " in visible, f"native forecast absent: {visible}"
        native_screen = tmux("capture-pane", "-p", "-t", "%0")
        (tmp_path / "native.screen").write_text(native_screen)
        assert ("Claude Code v" if client == "claude" else "OpenAI Codex") in native_screen, (
            native_screen
        )
        tmux("send-keys", "-t", "%0", "-l", "width edit probe")
        tmux("send-keys", "-t", "%0", "BSpace", "BSpace", "C-l")
        time.sleep(1)
        edited = tmux("capture-pane", "-p", "-t", "%0")
        (tmp_path / "edited.screen").write_text(edited)
        assert "width edit pro" in edited, edited
        after = tmux("capture-pane", "-p", "-t", pane)
        assert "TP " in after
        write_line(control=True)
        tmux("send-keys", "-t", "%0", "BSpace", "C-l")
        deadline = time.monotonic() + 8
        control = ""
        while time.monotonic() < deadline:
            control = tmux("capture-pane", "-p", "-t", pane)
            if "xxxxxxxx" in control:
                break
            time.sleep(0.25)
        (tmp_path / "control.screen").write_text(control)
        assert "xxxxxxxx" in control
        # No wrapped continuation from either of the one-line adapters.
        assert all(len(line) <= width for line in after.splitlines())
        assert all(len(line) <= width for line in control.splitlines())
        assert (tmp_path / "native.bytes").stat().st_size > 0
    finally:
        tmux("kill-server", check=False)  # only this uniquely named test server
