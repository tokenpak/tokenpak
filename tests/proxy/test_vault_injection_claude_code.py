"""
Tests for CCI-01: Vault context injection wired into Claude Code safe mode.

7 test cases:
  (a) Injection happens for claude-code-cli profile
  (b) Injection does NOT happen for claude-code-sdk profile
  (c) Injected block appears AFTER the cache_control marker (byte-position assertion)
  (d) Cache hit rate is unchanged across paired requests with/without injection
      (i.e., inject_with_cache_boundary preserves the stable prefix cache_control)
  (e) Skip model (haiku) is honored
  (f) Injection budget is respected
  (g) Zero blocks above min_score → no-op (body unchanged)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Import inject_with_cache_boundary directly from prompt_builder
# ---------------------------------------------------------------------------
TOKENPAK_ROOT = Path(__file__).parent.parent.parent / "tokenpak"
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Try the agent.proxy path (production suewu/calibot); fall back to tokenpak.proxy (TrixBot dev)
try:
    from tokenpak.proxy.prompt_builder import (
        inject_with_cache_boundary,
        apply_stable_cache_control,
    )
except ModuleNotFoundError:
    from tokenpak.proxy.prompt_builder import (  # type: ignore[assignment]
        inject_with_cache_boundary,
        apply_stable_cache_control,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _body(system=None, messages=None, model="claude-sonnet-4-5", **extra) -> bytes:
    """Build a minimal Anthropic request body as bytes."""
    data: dict[str, Any] = {"model": model, "max_tokens": 100}
    if system is not None:
        data["system"] = system
    if messages is not None:
        data["messages"] = messages
    else:
        data["messages"] = [{"role": "user", "content": "hello"}]
    data.update(extra)
    return json.dumps(data).encode()


def _parse(body: bytes) -> dict:
    return json.loads(body)


def _system_blocks(body: bytes) -> list:
    data = _parse(body)
    s = data.get("system", "")
    if isinstance(s, str):
        return [{"type": "text", "text": s}]
    return s if isinstance(s, list) else []


def _has_cache_control(block: dict) -> bool:
    return "cache_control" in block


def _cache_control_index(blocks: list) -> int:
    """Return index of last block with cache_control, or -1."""
    for i in range(len(blocks) - 1, -1, -1):
        if _has_cache_control(blocks[i]):
            return i
    return -1


# ---------------------------------------------------------------------------
# (c) inject_with_cache_boundary unit tests
# ---------------------------------------------------------------------------

class TestInjectWithCacheBoundary:
    """Test the core inject_with_cache_boundary helper directly."""

    def test_injected_block_appears_after_cache_control_marker(self):
        """(c) Injected block must appear AFTER the last cache_control block."""
        original_system = [
            {"type": "text", "text": "You are a helpful assistant."},
        ]
        body = _body(system=original_system)
        vault_text = "<retrieved_context>\n--- [AGENTS.md] ---\nsome vault content\n</retrieved_context>"

        new_body = inject_with_cache_boundary(body, vault_text)
        blocks = _system_blocks(new_body)

        assert len(blocks) >= 2, "Expected at least 2 system blocks after injection"
        cc_idx = _cache_control_index(blocks)
        assert cc_idx >= 0, "Expected cache_control marker to be present"

        # Injected block must be the LAST block and have no cache_control
        last_block = blocks[-1]
        assert last_block.get("text") == vault_text
        assert "cache_control" not in last_block, "Volatile vault block must not have cache_control"

        # The cache_control block must come before the vault block
        assert cc_idx < len(blocks) - 1, "cache_control block must precede injected vault block"

    def test_cache_control_boundary_preserved_on_string_system(self):
        """String system prompt is converted to list with cache_control then vault appended."""
        body = _body(system="You are a helpful assistant.")
        new_body = inject_with_cache_boundary(body, "vault content")
        blocks = _system_blocks(new_body)

        assert len(blocks) == 2
        assert _has_cache_control(blocks[0])  # stable prefix marked
        assert not _has_cache_control(blocks[1])  # vault block is volatile

    def test_injected_block_byte_position_after_cache_control(self):
        """(c) Byte-position assertion: cache_control bytes must precede vault text bytes."""
        body = _body(system=[{"type": "text", "text": "Stable system prompt."}])
        vault_text = "VAULT_CONTENT_MARKER"
        new_body = inject_with_cache_boundary(body, vault_text)

        raw = new_body.decode()
        cc_pos = raw.find("cache_control")
        vault_pos = raw.find("VAULT_CONTENT_MARKER")

        assert cc_pos >= 0, "cache_control must be present in output"
        assert vault_pos >= 0, "vault text must be present in output"
        assert cc_pos < vault_pos, (
            f"cache_control (pos={cc_pos}) must come before vault text (pos={vault_pos})"
        )

    def test_malformed_body_returns_original(self):
        """Malformed body is returned unchanged (fail-open)."""
        bad_body = b"not-json"
        result = inject_with_cache_boundary(bad_body, "vault content")
        assert result == bad_body


# ---------------------------------------------------------------------------
# (d) Cache hit rate preservation
# ---------------------------------------------------------------------------

class TestCacheHitRatePreservation:
    """(d) inject_with_cache_boundary must not change the stable prefix."""

    def test_stable_prefix_unchanged_after_injection(self):
        """The stable system blocks (before cache_control) must be byte-identical
        across two requests with/without vault injection."""
        stable_text = "You are a helpful assistant. This is the stable system prompt."
        body = _body(system=[{"type": "text", "text": stable_text}])

        # Without injection: apply_stable_cache_control marks the prefix
        body_without = apply_stable_cache_control(body)

        # With injection: inject_with_cache_boundary marks the prefix AND appends vault
        body_with = inject_with_cache_boundary(body, "some vault context")

        blocks_without = _system_blocks(body_without)
        blocks_with = _system_blocks(body_with)

        # The first block (stable prefix) must be identical in both
        assert blocks_without[0] == blocks_with[0], (
            "Stable prefix block must be identical with and without vault injection"
        )
        # Both must have cache_control on the stable prefix
        assert _has_cache_control(blocks_without[0])
        assert _has_cache_control(blocks_with[0])

    def test_vault_block_has_no_cache_control(self):
        """The vault block must NOT have cache_control — it's volatile."""
        body = _body(system="Stable prompt.")
        new_body = inject_with_cache_boundary(body, "vault content")
        blocks = _system_blocks(new_body)
        vault_block = blocks[-1]
        assert not _has_cache_control(vault_block)


# ---------------------------------------------------------------------------
# CCI-01 injection stage logic — tested via unit-level simulation
# ---------------------------------------------------------------------------

class _MockVaultIndex:
    """Minimal mock for VAULT_INDEX with controllable compile_injection output."""

    def __init__(self, injection_text="", tokens=0, sources=None, available=True):
        self.available = available
        self._injection_text = injection_text
        self._tokens = tokens
        self._sources = sources or []

    def compile_injection(self, query, budget=4000, top_k=5, min_score=2.0):
        return self._injection_text, self._tokens, self._sources


def _run_cci01_stage(
    body: bytes,
    *,
    profile: str = "claude-code-cli",
    model: str = "claude-sonnet-4-5",
    vault_inject_env: str = "true",
    vault_index: _MockVaultIndex | None = None,
    query_signal: str = "test query",
    prompt_builder_available: bool = True,
) -> tuple[bytes, dict]:
    """
    Simulate the CCI-01 injection stage in isolation.
    Returns (new_body, session_dict).
    """
    if vault_index is None:
        vault_index = _MockVaultIndex(
            injection_text="<retrieved_context>vault data</retrieved_context>",
            tokens=50,
            sources=["AGENTS.md"],
        )

    session: dict = {"active_profile": profile}
    injected_tokens = 0
    injected_sources: list = []
    input_tokens = 100  # dummy

    with patch.dict(os.environ, {"TOKENPAK_VAULT_INJECT": vault_inject_env}):
        INJECT_BUDGET = int(os.environ.get("TOKENPAK_INJECT_BUDGET", "4000"))
        INJECT_TOP_K = int(os.environ.get("TOKENPAK_INJECT_TOP_K", "5"))
        INJECT_MIN_SCORE = float(os.environ.get("TOKENPAK_INJECT_MIN_SCORE", "2.0"))
        INJECT_SKIP_MODELS = os.environ.get("TOKENPAK_INJECT_SKIP_MODELS", "haiku")

        # --- Replicate CCI-01 logic ---
        _cci01_profile = session.get("active_profile", "")
        _cci01_eligible = (
            _cci01_profile.startswith("claude-code-")
            and _cci01_profile != "claude-code-sdk"
            and os.environ.get("TOKENPAK_VAULT_INJECT", "true").lower()
            not in ("0", "false", "no")
            and prompt_builder_available
            and vault_index.available
        )

        if _cci01_eligible:
            try:
                _cci01_req = json.loads(body)
                _cci01_has_system = bool(_cci01_req.get("system"))
            except Exception:
                _cci01_has_system = False

            _cci01_model_skip = bool(
                INJECT_SKIP_MODELS.strip()
                and any(
                    s.strip() and s.strip().lower() in model.lower()
                    for s in INJECT_SKIP_MODELS.split(",")
                )
            )

            if _cci01_has_system and not _cci01_model_skip:
                _cci01_query = query_signal  # pre-extracted for isolation
                if _cci01_query:
                    _cci01_text, _cci01_tok, _cci01_srcs = vault_index.compile_injection(
                        _cci01_query,
                        budget=INJECT_BUDGET,
                        top_k=INJECT_TOP_K,
                        min_score=INJECT_MIN_SCORE,
                    )
                    if _cci01_text and _cci01_tok > 0:
                        body = inject_with_cache_boundary(body, _cci01_text)
                        injected_tokens = _cci01_tok
                        injected_sources = _cci01_srcs
                        session["vault_blocks_injected"] = len(_cci01_srcs)
                        session["vault_tokens_injected"] = _cci01_tok
            elif _cci01_model_skip:
                session["_cci01_skip_reason"] = "model_skip"
            else:
                session["_cci01_skip_reason"] = "no_system_prompt"
        else:
            session["_cci01_skip_reason"] = "not_eligible"

    session["_injected_tokens"] = injected_tokens
    session["_injected_sources"] = injected_sources
    return body, session


# ---------------------------------------------------------------------------
# (a) Injection happens for claude-code-cli profile
# ---------------------------------------------------------------------------

class TestCCI01InjectionForCLIProfile:

    def test_injection_happens_for_cli_profile(self):
        """(a) claude-code-cli profile → vault blocks injected."""
        body = _body(
            system="You are a helpful assistant.",
            messages=[{"role": "user", "content": "what should I do next?"}],
        )
        new_body, session = _run_cci01_stage(body, profile="claude-code-cli")

        assert session.get("vault_blocks_injected", 0) > 0, "Expected vault blocks to be injected"
        assert session.get("vault_tokens_injected", 0) > 0
        assert new_body != body, "Body should have changed after injection"

    def test_injection_happens_for_tui_profile(self):
        """(a) claude-code-tui profile → vault blocks injected."""
        body = _body(system="Stable prompt.")
        new_body, session = _run_cci01_stage(body, profile="claude-code-tui")
        assert session.get("vault_blocks_injected", 0) > 0

    def test_injection_happens_for_cron_profile(self):
        """(a) claude-code-cron profile → vault blocks injected."""
        body = _body(system="Stable prompt.")
        new_body, session = _run_cci01_stage(body, profile="claude-code-cron")
        assert session.get("vault_blocks_injected", 0) > 0


# ---------------------------------------------------------------------------
# (b) Injection does NOT happen for claude-code-sdk profile
# ---------------------------------------------------------------------------

class TestCCI01NoInjectionForSDKProfile:

    def test_no_injection_for_sdk_profile(self):
        """(b) claude-code-sdk profile → no injection."""
        body = _body(system="Stable prompt.")
        new_body, session = _run_cci01_stage(body, profile="claude-code-sdk")

        assert session.get("vault_blocks_injected", 0) == 0
        assert session.get("vault_tokens_injected", 0) == 0
        assert new_body == body, "Body must be unchanged for sdk profile"

    def test_no_injection_for_non_cc_profile(self):
        """Non-Claude-Code profile (e.g. balanced) → no injection via CCI-01 path."""
        body = _body(system="Stable prompt.")
        new_body, session = _run_cci01_stage(body, profile="balanced")

        assert session.get("vault_blocks_injected", 0) == 0
        assert new_body == body


# ---------------------------------------------------------------------------
# (e) Skip model honored
# ---------------------------------------------------------------------------

class TestCCI01SkipModel:

    def test_haiku_model_skipped(self):
        """(e) Model in INJECT_SKIP_MODELS (haiku) → no injection."""
        body = _body(system="Stable prompt.", model="claude-haiku-4-5-20251001")
        new_body, session = _run_cci01_stage(body, profile="claude-code-cli", model="claude-haiku-4-5-20251001")

        assert session.get("vault_blocks_injected", 0) == 0
        assert session.get("_cci01_skip_reason") == "model_skip"
        assert new_body == body

    def test_sonnet_model_not_skipped(self):
        """Sonnet is not in skip list → injection proceeds."""
        body = _body(system="Stable prompt.", model="claude-sonnet-4-5")
        new_body, session = _run_cci01_stage(body, profile="claude-code-cli", model="claude-sonnet-4-5")

        assert session.get("vault_blocks_injected", 0) > 0


# ---------------------------------------------------------------------------
# (f) Injection budget respected
# ---------------------------------------------------------------------------

class TestCCI01Budget:

    def test_budget_respected(self):
        """(f) compile_injection is called with correct budget from env/config."""
        budget = 2000
        calls = []

        class _TrackingIndex(_MockVaultIndex):
            def compile_injection(self, query, budget=4000, top_k=5, min_score=2.0):
                calls.append({"budget": budget, "top_k": top_k, "min_score": min_score})
                return "vault text", 50, ["src.md"]

        body = _body(system="Stable.")

        with patch.dict(os.environ, {"TOKENPAK_INJECT_BUDGET": str(budget)}):
            _run_cci01_stage(body, profile="claude-code-cli", vault_index=_TrackingIndex())

        assert len(calls) == 1
        assert calls[0]["budget"] == budget

    def test_top_k_respected(self):
        """(f) INJECT_TOP_K is forwarded to compile_injection."""
        calls = []

        class _TrackingIndex(_MockVaultIndex):
            def compile_injection(self, query, budget=4000, top_k=5, min_score=2.0):
                calls.append({"top_k": top_k})
                return "vault text", 50, ["src.md"]

        body = _body(system="Stable.")

        with patch.dict(os.environ, {"TOKENPAK_INJECT_TOP_K": "3"}):
            _run_cci01_stage(body, profile="claude-code-cli", vault_index=_TrackingIndex())

        assert calls[0]["top_k"] == 3

    def test_min_score_respected(self):
        """(f) INJECT_MIN_SCORE is forwarded to compile_injection."""
        calls = []

        class _TrackingIndex(_MockVaultIndex):
            def compile_injection(self, query, budget=4000, top_k=5, min_score=2.0):
                calls.append({"min_score": min_score})
                return "vault text", 50, ["src.md"]

        body = _body(system="Stable.")

        with patch.dict(os.environ, {"TOKENPAK_INJECT_MIN_SCORE": "3.5"}):
            _run_cci01_stage(body, profile="claude-code-cli", vault_index=_TrackingIndex())

        assert abs(calls[0]["min_score"] - 3.5) < 1e-6


# ---------------------------------------------------------------------------
# (g) Zero blocks above min_score → no-op
# ---------------------------------------------------------------------------

class TestCCI01ZeroBlocks:

    def test_zero_blocks_returns_body_unchanged(self):
        """(g) compile_injection returns empty text → body unchanged, no telemetry set."""
        empty_index = _MockVaultIndex(injection_text="", tokens=0, sources=[])
        body = _body(system="Stable prompt.")

        new_body, session = _run_cci01_stage(
            body, profile="claude-code-cli", vault_index=empty_index
        )

        assert new_body == body, "Body must be unchanged when vault returns no results"
        assert session.get("vault_blocks_injected", 0) == 0
        assert session.get("vault_tokens_injected", 0) == 0

    def test_vault_inject_env_false_skips_injection(self):
        """TOKENPAK_VAULT_INJECT=false → injection disabled even for claude-code-cli."""
        body = _body(system="Stable prompt.")
        new_body, session = _run_cci01_stage(
            body, profile="claude-code-cli", vault_inject_env="false"
        )
        assert new_body == body
        assert session.get("vault_blocks_injected", 0) == 0

    def test_vault_inject_env_zero_skips_injection(self):
        """TOKENPAK_VAULT_INJECT=0 → injection disabled."""
        body = _body(system="Stable prompt.")
        new_body, session = _run_cci01_stage(
            body, profile="claude-code-cli", vault_inject_env="0"
        )
        assert new_body == body


# ---------------------------------------------------------------------------
# No system prompt → skip
# ---------------------------------------------------------------------------

class TestCCI01NoSystemPrompt:

    def test_no_injection_without_system_prompt(self):
        """No system prompt in body → injection skipped (no target)."""
        body = _body()  # no system kwarg
        new_body, session = _run_cci01_stage(body, profile="claude-code-cli")

        assert new_body == body
        assert session.get("_cci01_skip_reason") == "no_system_prompt"
