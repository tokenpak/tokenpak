# SPDX-License-Identifier: Apache-2.0
"""Conformance coverage for deterministic, bounded companion injections."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokenpak.companion import launcher
from tokenpak.companion.capsules.builder import CapsuleBuilder, _wrap_capsule
from tokenpak.companion.codex import agents_md
from tokenpak.companion.config import CompanionConfig
from tokenpak.companion.mcp import server
from tokenpak.companion.mcp.tools import CompanionState

MAX_UNIT_ENVELOPE_BYTES = 256
TURNS = 5


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
@pytest.mark.parametrize("agents_style", ["lean", "standard"])
def test_session_surfaces_are_byte_stable(monkeypatch, tmp_path, profile, agents_style):
    first = (
        _rendered_system_prompt(tmp_path, profile, "first"),
        _serialized_tools_list(monkeypatch, profile),
        agents_md.generate_agents_md(agents_style).encode(),
    )
    second = (
        _rendered_system_prompt(tmp_path, profile, "second"),
        _serialized_tools_list(monkeypatch, profile),
        agents_md.generate_agents_md(agents_style).encode(),
    )
    assert second == first


def test_pak_envelope_unit_shape_is_stable():
    previous = ""
    sizes: list[int] = []
    for turn in range(TURNS):
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
    assert max(sizes) < MAX_UNIT_ENVELOPE_BYTES


def test_injected_envelopes_replace_not_accumulate_across_sequential_turns():
    """Drive N sequential turns through ``CapsuleBuilder.process`` — the real
    per-turn injection path, in the production topology where the client
    resends its own original history each turn.

    Invariants: each eligible historical block carries exactly ONE envelope
    per turn (no accumulation, no nesting); the envelope for unchanged
    history is byte-identical across turns (stable capsule id); per-turn
    injected size is bounded by a scenario constant.

    A separate regression below covers the other production topology, where
    a returned Pak envelope itself re-enters ``process`` on later turns.
    """
    builder = CapsuleBuilder(enabled=True, min_block_chars=64, hot_window=2)
    base_block = "historical analysis of the migration plan " * 20
    history: list[dict] = [
        {"role": "user", "content": base_block},
        {"role": "assistant", "content": "ack"},
    ]
    size_bound = len(base_block.encode()) + 160
    previous_envelope: str | None = None
    for turn in range(TURNS):
        history.append({"role": "user", "content": f"follow-up question {turn}"})
        history.append({"role": "assistant", "content": f"short answer {turn}"})
        body = json.dumps({"messages": history}, ensure_ascii=False).encode()
        out, _stats = builder.process(body)
        data = json.loads(out)
        serialized = json.dumps(data, ensure_ascii=False)
        assert serialized.count("[PAK ") == 1, "exactly one envelope per eligible block"
        assert serialized.count("[/PAK]") == 1
        wrapped = next(
            m["content"]
            for m in data["messages"]
            if isinstance(m.get("content"), str) and "[PAK " in m["content"]
        )
        assert len(wrapped.encode()) <= size_bound
        if previous_envelope is not None:
            assert wrapped == previous_envelope, (
                "unchanged history must re-derive the identical envelope, not grow"
            )
        previous_envelope = wrapped


def test_reingested_pak_envelope_is_byte_identical_at_every_turn():
    """Regression: a resent Pak tool result must never be wrapped again."""
    builder = CapsuleBuilder(enabled=True, min_block_chars=64, hot_window=2)
    original = "historical migration analysis " * 24
    envelope = _wrap_capsule(original, "migration analysis summary")
    history: list[dict] = [
        {"role": "user", "content": envelope},
        {"role": "user", "content": "initial follow-up"},
        {"role": "assistant", "content": "initial answer"},
    ]
    body = json.dumps({"messages": history}, ensure_ascii=False).encode()

    for turn in range(TURNS):
        out, stats = builder.process(body)
        serialized = out.decode()

        assert out == body
        assert stats["blocks_capsulized"] == 0
        assert stats["chars_out"] == stats["chars_in"]
        assert serialized.count("[PAK ") == 1
        assert serialized.count("[/PAK]") == 1
        assert json.loads(out)["messages"][0]["content"] == envelope

        history.extend(
            [
                {"role": "user", "content": f"follow-up question {turn}"},
                {"role": "assistant", "content": f"short answer {turn}"},
            ]
        )
        body = json.dumps({"messages": history}, ensure_ascii=False).encode()
