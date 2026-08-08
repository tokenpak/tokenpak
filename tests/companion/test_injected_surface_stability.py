# SPDX-License-Identifier: Apache-2.0
"""Conformance coverage for deterministic, bounded companion injections."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokenpak.companion import launcher
from tokenpak.companion.capsules.builder import _wrap_capsule
from tokenpak.companion.codex import agents_md
from tokenpak.companion.config import CompanionConfig
from tokenpak.companion.mcp import server
from tokenpak.companion.mcp.tools import CompanionState

MAX_INJECTED_ENVELOPE_BYTES = 256


def _rendered_system_prompt(tmp_path: Path, profile: str, run: str) -> bytes:
    config = CompanionConfig(profile=profile, journal_dir=tmp_path / run)
    config.run_dir.mkdir(parents=True)
    return Path(launcher._write_system_prompt(config)).read_bytes()


def _serialized_tools_list(monkeypatch: pytest.MonkeyPatch, profile: str) -> bytes:
    responses: list[dict] = []
    monkeypatch.setattr(server, "_send", responses.append)
    state = CompanionState(config=CompanionConfig(profile=profile))
    server._handle_tools_list(1, state)
    assert len(responses) == 1
    return json.dumps(
        responses[0]["result"]["tools"],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


@pytest.mark.parametrize("profile", ["lean", "balanced"])
def test_session_surfaces_are_byte_stable(monkeypatch, tmp_path, profile):
    first = (
        _rendered_system_prompt(tmp_path, profile, "first"),
        _serialized_tools_list(monkeypatch, profile),
        agents_md.generate_agents_md("standard").encode(),
    )
    second = (
        _rendered_system_prompt(tmp_path, profile, "second"),
        _serialized_tools_list(monkeypatch, profile),
        agents_md.generate_agents_md("standard").encode(),
    )
    assert second == first


def test_pak_envelope_is_replace_not_accumulate_across_turns():
    previous = ""
    sizes: list[int] = []
    for turn in range(5):
        original = f"source-{turn}:" + ("x" * 192)
        compressed = f"summary-{turn}:" + ("y" * 32)
        envelope = _wrap_capsule(original, compressed)
        assert envelope.count("[PAK ") == 1
        assert envelope.count("[/PAK]") == 1
        if previous:
            assert previous not in envelope
        sizes.append(len(envelope.encode()))
        previous = envelope

    assert len(set(sizes)) == 1
    assert max(sizes) < MAX_INJECTED_ENVELOPE_BYTES
