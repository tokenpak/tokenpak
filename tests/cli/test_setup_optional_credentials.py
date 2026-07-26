# SPDX-License-Identifier: Apache-2.0
"""Setup contracts for client-owned OAuth and model selection."""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

import yaml

from tokenpak import _cli_core


def test_setup_continues_without_api_key_or_model_override(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    # Hermetic lifecycle: this test is about credential handling, not about
    # whether a proxy starts, and the developer machine running the suite may
    # well have a real proxy on the default port.
    from tokenpak.core.runtime import lifecycle

    monkeypatch.setattr(lifecycle, "port_in_use", lambda *_a, **_k: False)
    monkeypatch.setattr(
        lifecycle,
        "await_start",
        lambda *_a, **_k: SimpleNamespace(
            running=True, routed=False, owned=True, reasons=[], pid=4242
        ),
    )
    monkeypatch.setattr(lifecycle, "write_pid", lambda pid: None)

    fake_process = SimpleNamespace(pid=4242)
    monkeypatch.setattr("subprocess.Popen", lambda *_a, **_k: fake_process)
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: SimpleNamespace(
            read=lambda: json.dumps({"compilation_mode": "hybrid"}).encode()
        ),
    )

    # Setup is driven explicitly. EOF used to count as consent: piping
    # /dev/null selected a profile, wrote config and spawned a daemon with
    # nothing answered, so this test passed an empty Namespace and got a
    # fully configured install out of silence.
    rc = _cli_core.cmd_setup(Namespace(profile="balanced", yes=True, port=None))
    assert rc == 0

    # setup writes canonically; the legacy home is read-compatibility only.
    config = yaml.safe_load((tmp_path / ".tpk" / "config.yaml").read_text())
    assert config["api_keys"] == {}
    assert "provider" not in config["proxy"]
    output = capsys.readouterr().out
    assert "continuing without them" in output
    assert "existing OAuth/session credentials" in output
    assert "selected or default model" in output
