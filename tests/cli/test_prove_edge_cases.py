"""Edge-case tests for the tokenpak prove command.

Covers PROVE-TEST-01 acceptance criteria:
  (a) missing provider config — unknown provider, missing API key
  (b) invalid model name — unrecognised model falls back gracefully
  (c) no providers available — empty matrix, unresolvable API key
  (d) CLI argument validation — bad scenario file, missing turns
  (e) graceful timeout/error handling — TimeoutException recorded in result

All tests are hermetic (no network, no subprocess) and use the actual
``tokenpak.prove`` module on the main branch.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import httpx
import pytest

from tokenpak.prove.adapter import (
    ArmConfig,
    ArmResult,
    TurnResult,
    _get_model_rates,
    _get_provider,
    _resolve_api_key,
    estimate_cost,
    list_providers,
    run_arm,
)
from tokenpak.prove.scenario import Scenario, _detect_provider, resolve_scenario

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_scenario(tmp_path: Path, name: str = "edge-test") -> Scenario:
    """Write a minimal valid .md scenario to *tmp_path* and return it."""
    md = dedent(f"""\
        ---
        name: {name}
        model: claude-sonnet-4-6
        provider: anthropic
        ---

        ## Turn 1: Basic test
        Hello, prove you exist.
    """)
    path = tmp_path / f"{name}.md"
    path.write_text(md)
    return Scenario.from_file(path)


def _minimal_arm(name: str = "test-arm") -> ArmConfig:
    """Return a minimal ArmConfig that resolves without network."""
    return ArmConfig(
        name=name,
        platform="api",
        provider="anthropic",
        model="claude-sonnet-4-6",
    )


# ---------------------------------------------------------------------------
# (a) Missing provider config
# ---------------------------------------------------------------------------


class TestMissingProviderConfig:
    """(a) Edge cases around absent or incomplete provider configuration."""

    def test_unknown_provider_returns_empty_dict(self) -> None:
        """_get_provider() returns {} for an unregistered provider name."""
        result = _get_provider("provider-that-does-not-exist-xyz")
        assert result == {}

    def test_resolve_api_key_returns_empty_when_env_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_resolve_api_key() returns '' when no env var and no fallback."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # Point Claude creds file somewhere non-existent so fallback is skipped
        with patch("tokenpak.prove.adapter.Path.home", return_value=Path("/nonexistent/home")):
            key = _resolve_api_key("anthropic", "ANTHROPIC_API_KEY")
        assert key == ""

    def test_execute_turn_api_records_missing_key_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """run_arm with no API key must record an error in TurnResult, not raise."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        # Patch _resolve_api_key to always return ""
        with patch("tokenpak.prove.adapter._resolve_api_key", return_value=""):
            scenario = _minimal_scenario(tmp_path)
            arm = _minimal_arm()
            result = run_arm(
                cfg=arm,
                turns=scenario.turns,
                system=scenario.system,
                max_tokens=64,
            )

        # run_arm should complete without raising; error recorded in a turn result
        assert isinstance(result, ArmResult)
        errors = [t.error for t in result.turns if t.error]
        assert errors, "Expected at least one TurnResult with an error"
        assert any("API key" in e or "No API key" in e or "key" in e.lower() for e in errors)

    def test_list_providers_returns_builtin_providers(self) -> None:
        """list_providers() returns at least the built-in anthropic + openai entries."""
        providers = list_providers()
        names = {p["name"] for p in providers}
        assert "anthropic" in names
        assert "openai" in names


# ---------------------------------------------------------------------------
# (b) Invalid model name
# ---------------------------------------------------------------------------


class TestInvalidModelName:
    """(b) Edge cases around unrecognised model names."""

    def test_unknown_model_gets_default_rates(self) -> None:
        """_get_model_rates() returns a non-empty default for an unknown model."""
        rates = _get_model_rates("anthropic", "claude-not-a-real-model-9999")
        assert isinstance(rates, dict)
        assert "input" in rates and "output" in rates
        assert rates["input"] > 0

    def test_unknown_provider_model_gets_default_rates(self) -> None:
        """_get_model_rates() is robust even when the provider is also unknown."""
        rates = _get_model_rates("provider-xyz", "model-abc")
        assert isinstance(rates, dict)
        assert rates["input"] > 0

    def test_estimate_cost_unknown_model_uses_defaults(self) -> None:
        """estimate_cost() completes without error for unrecognised model names."""
        cost = estimate_cost("anthropic", "model-xyz-1", 1000, 500, 0)
        assert cost >= 0.0

    def test_detect_provider_openai_prefix(self) -> None:
        """_detect_provider returns 'openai' for gpt- and o-prefix models."""
        assert _detect_provider("gpt-4o") == "openai"
        assert _detect_provider("o3-mini") == "openai"

    def test_detect_provider_anthropic_fallback(self) -> None:
        """_detect_provider returns 'anthropic' for unknown model prefixes."""
        assert _detect_provider("claude-sonnet-4-6") == "anthropic"
        assert _detect_provider("unknown-model-xyz") == "anthropic"

    def test_arm_config_resolves_unknown_model_without_raising(self) -> None:
        """ArmConfig.resolve() does not raise for unknown model names."""
        cfg = ArmConfig(
            name="bad-model-arm",
            platform="api",
            provider="anthropic",
            model="claude-nonexistent-9999",
        )
        resolved = cfg.resolve()
        assert resolved.model == "claude-nonexistent-9999"


# ---------------------------------------------------------------------------
# (c) No providers available
# ---------------------------------------------------------------------------


class TestNoProvidersAvailable:
    """(c) Edge cases when no valid provider / API key is available."""

    def test_run_arm_with_unknown_platform_records_error(self, tmp_path: Path) -> None:
        """run_arm with an unsupported platform value records an error, not raises."""
        scenario = _minimal_scenario(tmp_path)
        arm = ArmConfig(
            name="bad-platform-arm",
            platform="nonexistent-platform",
            provider="anthropic",
            model="claude-sonnet-4-6",
        )
        result = run_arm(
            cfg=arm,
            turns=scenario.turns,
            system=scenario.system,
            max_tokens=64,
        )
        assert isinstance(result, ArmResult)
        assert result.error  # top-level error, not just turn-level

    def test_run_arm_empty_turns_returns_arm_result(self, tmp_path: Path) -> None:
        """run_arm with zero turns completes cleanly (zero cost, no errors)."""
        arm = _minimal_arm()
        result = run_arm(
            cfg=arm,
            turns=[],
            system="system prompt",
            max_tokens=64,
        )
        assert isinstance(result, ArmResult)
        assert result.total_cost_usd == 0.0
        assert not result.error

    def test_resolve_scenario_raises_for_missing_scenario(self, tmp_path: Path) -> None:
        """resolve_scenario raises FileNotFoundError for a name that doesn't exist."""
        # Patch user dir to our empty tmp_path so only tmp_path is checked
        with (
            patch("tokenpak.prove.scenario._USER_DIR", tmp_path),
            patch("tokenpak.prove.scenario._BUILTIN_DIR", tmp_path),
        ):
            with pytest.raises(FileNotFoundError, match="not found"):
                resolve_scenario("scenario-that-definitely-does-not-exist")


# ---------------------------------------------------------------------------
# (d) CLI argument / scenario validation
# ---------------------------------------------------------------------------


class TestCliArgumentValidation:
    """(d) Edge cases around malformed or invalid CLI inputs and scenario files."""

    def test_scenario_from_file_raises_without_turns(self, tmp_path: Path) -> None:
        """A scenario file with no ## Turn headings raises ValueError."""
        bad_md = dedent("""\
            ---
            name: No Turns
            model: claude-sonnet-4-6
            ---

            This file has no turn headings at all.
        """)
        path = tmp_path / "no_turns.md"
        path.write_text(bad_md)

        with pytest.raises(ValueError, match="No turns"):
            Scenario.from_file(path)

    def test_scenario_accepts_turns_without_numbers(self, tmp_path: Path) -> None:
        """Turns without 'Turn N' prefix are accepted as numbered sequentially."""
        md = dedent("""\
            ---
            name: Free Heading Turns
            ---

            ## Exploration step
            Describe the codebase.

            ## Implementation step
            Add a feature.
        """)
        path = tmp_path / "free_heading.md"
        path.write_text(md)
        scenario = Scenario.from_file(path)
        assert len(scenario.turns) == 2
        assert scenario.turns[0].number == 1
        assert scenario.turns[1].number == 2

    def test_scenario_matrix_parsed_correctly(self, tmp_path: Path) -> None:
        """A scenario with a ``matrix:`` block produces the correct arm count."""
        md = dedent("""\
            ---
            name: Matrix Scenario
            matrix:
              - name: Sonnet Direct
                platform: api
                provider: anthropic
                model: claude-sonnet-4-6
              - name: Sonnet Proxy
                platform: proxy
                provider: anthropic
                model: claude-sonnet-4-6
            ---

            ## Turn 1: Test
            Run the matrix.
        """)
        path = tmp_path / "matrix.md"
        path.write_text(md)
        scenario = Scenario.from_file(path)
        assert len(scenario.matrix) == 2
        assert scenario.matrix[0]["name"] == "Sonnet Direct"
        assert scenario.matrix[1]["platform"] == "proxy"

    def test_scenario_from_file_infers_provider_from_gpt_model(self, tmp_path: Path) -> None:
        """A scenario with a gpt- model name auto-infers provider='openai'."""
        md = dedent("""\
            ---
            name: OpenAI Scenario
            model: gpt-4o
            ---

            ## Turn 1: Test
            Hello GPT.
        """)
        path = tmp_path / "openai.md"
        path.write_text(md)
        scenario = Scenario.from_file(path)
        assert scenario.provider == "openai"

    def test_scenario_from_file_default_provider_is_anthropic(self, tmp_path: Path) -> None:
        """When model prefix is unknown, provider defaults to 'anthropic'."""
        md = dedent("""\
            ---
            name: Unknown Model
            model: some-unknown-model
            ---

            ## Turn 1
            What are you?
        """)
        path = tmp_path / "unknown_model.md"
        path.write_text(md)
        scenario = Scenario.from_file(path)
        assert scenario.provider == "anthropic"


# ---------------------------------------------------------------------------
# (e) Graceful timeout / error handling
# ---------------------------------------------------------------------------


class TestGracefulTimeoutAndErrorHandling:
    """(e) Prove adapters catch timeouts and network errors without propagating."""

    def test_httpx_timeout_recorded_in_turn_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the HTTP call times out, the TurnResult records an error string
        and run_arm does NOT re-raise the exception.

        The format functions (_run_turn_anthropic etc.) catch TimeoutException
        internally and return TurnResult(error=...).  We simulate that by
        patching _FORMAT_DISPATCH (which holds function refs captured at
        import time) with a stub that returns the same error result shape.
        """
        import tokenpak.prove.adapter as _adapter

        def _timeout_stub(*args, **kwargs):
            # Mirrors what _run_turn_anthropic does on httpx.TimeoutException
            return TurnResult(error="Request timed out")

        with patch("tokenpak.prove.adapter._resolve_api_key", return_value="sk-fake"):
            with patch.dict(_adapter._FORMAT_DISPATCH, {"anthropic": _timeout_stub}):
                scenario = _minimal_scenario(tmp_path)
                arm = _minimal_arm()
                result = run_arm(
                    cfg=arm,
                    turns=scenario.turns,
                    system=scenario.system,
                    max_tokens=64,
                )

        assert isinstance(result, ArmResult)
        errors = [t.error for t in result.turns if t.error]
        assert errors, "Expected a timeout error recorded in turn result"
        assert any("timeout" in e.lower() or "timed" in e.lower() for e in errors)

    def test_generic_exception_in_turn_recorded_not_raised(self, tmp_path: Path) -> None:
        """A generic network error during a turn is captured in TurnResult.error."""
        import tokenpak.prove.adapter as _adapter

        def _error_stub(*args, **kwargs):
            # Mirrors what the format function does on a generic exception
            return TurnResult(error="unexpected network error")

        with patch("tokenpak.prove.adapter._resolve_api_key", return_value="sk-fake"):
            with patch.dict(_adapter._FORMAT_DISPATCH, {"anthropic": _error_stub}):
                scenario = _minimal_scenario(tmp_path)
                arm = _minimal_arm()
                result = run_arm(
                    cfg=arm,
                    turns=scenario.turns,
                    system=scenario.system,
                    max_tokens=64,
                )

        assert isinstance(result, ArmResult)
        errors = [t.error for t in result.turns if t.error]
        assert errors

    def test_arm_result_finalize_runs_after_errors(self, tmp_path: Path) -> None:
        """ArmResult.finalize() aggregates correctly even when some turns errored."""
        arm_result = ArmResult(
            arm_name="test",
            platform="api",
            provider="anthropic",
            model="claude-sonnet-4-6",
            via_tokenpak=False,
        )
        arm_result.turns.append(
            TurnResult(
                turn_number=1, label="ok turn", input_tokens=100, output_tokens=50, cost_usd=0.001
            )
        )
        arm_result.turns.append(
            TurnResult(turn_number=2, label="error turn", error="connection refused")
        )
        arm_result.finalize()

        assert arm_result.total_input_tokens == 100
        assert arm_result.total_output_tokens == 50
        assert arm_result.total_cost_usd == pytest.approx(0.001, abs=1e-6)

    def test_turn_error_aborts_remaining_turns_and_records_arm_error(self, tmp_path: Path) -> None:
        """run_arm stops at the first turn error and records it in ArmResult.error.

        This documents the deliberate design: a turn failure (including timeout)
        marks the run as errored and stops execution so the next arm can proceed
        rather than wasting tokens on a broken session.
        """
        import tokenpak.prove.adapter as _adapter

        def _always_timeout(*args, **kwargs):
            raise httpx.TimeoutException("first turn timeout")

        # Scenario with 2 turns — only turn 1 should execute
        md = dedent("""\
            ---
            name: two-turn
            ---

            ## Turn 1
            First prompt.

            ## Turn 2
            Second prompt.
        """)
        path = tmp_path / "two_turn.md"
        path.write_text(md)
        scenario = Scenario.from_file(path)

        def _always_error(*args, **kwargs):
            # Simulate a timeout that the format function already caught internally
            return TurnResult(error="Request timed out")

        with patch("tokenpak.prove.adapter._resolve_api_key", return_value="sk-fake"):
            with patch.dict(_adapter._FORMAT_DISPATCH, {"anthropic": _always_error}):
                arm = _minimal_arm("two-turn-arm")
                result = run_arm(
                    cfg=arm,
                    turns=scenario.turns,
                    system=scenario.system,
                    max_tokens=64,
                )

        # Turn 1 recorded with error, turn 2 never attempted (run stops on first error)
        assert len(result.turns) == 1
        assert result.turns[0].error
        # ArmResult.error mirrors the first turn error
        assert result.error
        assert "Turn 1" in result.error
