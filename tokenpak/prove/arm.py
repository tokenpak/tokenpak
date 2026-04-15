# SPDX-License-Identifier: Apache-2.0
"""Execute one arm of a prove run — a multi-turn conversation against an API.

Each arm runs the same scenario (same user prompts) and collects per-turn
metrics: input tokens, output tokens, cached tokens, latency, and cost.

Two modes:
  - **direct**: hits the provider API with no proxy
  - **proxied**: routes through the tokenpak proxy (all optimizations active)

Uses httpx for HTTP with SSE streaming for live display.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Optional

import httpx

from .scenario import Scenario, Turn


# ── Per-turn and per-arm results ────────────────────────────────────────


@dataclass
class TurnResult:
    """Metrics from one turn of the conversation."""

    turn_number: int
    label: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    response_text: str = ""
    error: str = ""


@dataclass
class ArmResult:
    """Aggregate results from one arm of the prove run."""

    arm_name: str  # "direct" or "tokenpak"
    model: str = ""
    provider: str = ""
    turns: list[TurnResult] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cost_usd: float = 0.0
    total_latency_s: float = 0.0
    error: str = ""

    def finalize(self) -> None:
        """Roll up per-turn metrics into totals."""
        self.total_input_tokens = sum(t.input_tokens for t in self.turns)
        self.total_output_tokens = sum(t.output_tokens for t in self.turns)
        self.total_cache_read_tokens = sum(t.cache_read_tokens for t in self.turns)
        self.total_cost_usd = sum(t.cost_usd for t in self.turns)
        self.total_latency_s = sum(t.latency_s for t in self.turns)


# ── Provider API helpers ────────────────────────────────────────────────


_ANTHROPIC_API = "https://api.anthropic.com"
_OPENAI_API = "https://api.openai.com"


def _get_api_key(provider: str) -> str:
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        return key
    else:
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        return key


def _get_base_url(provider: str, proxied: bool) -> str:
    if proxied:
        proxy_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
        return proxy_url
    if provider == "anthropic":
        return _ANTHROPIC_API
    return _OPENAI_API


# ── Cost estimation (mirrors budget tracker) ────────────────────────────

_RATES: dict[str, dict[str, float]] = {
    "claude-opus-4-6":   {"input": 15.0, "output": 75.0, "cached": 1.50},
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0, "cached": 0.30},
    "claude-sonnet-4-5": {"input": 3.0,  "output": 15.0, "cached": 0.30},
    "claude-haiku-4-5":  {"input": 0.80, "output": 4.0,  "cached": 0.08},
    "gpt-4o":            {"input": 2.50, "output": 10.0, "cached": 1.25},
    "gpt-4o-mini":       {"input": 0.15, "output": 0.60, "cached": 0.075},
    "o3":                {"input": 10.0, "output": 40.0, "cached": 5.0},
    "o3-mini":           {"input": 1.10, "output": 4.40, "cached": 0.55},
    "o4-mini":           {"input": 1.10, "output": 4.40, "cached": 0.55},
    "o1":                {"input": 15.0, "output": 60.0, "cached": 7.50},
    "gpt-4.1":           {"input": 2.0,  "output": 8.0,  "cached": 0.50},
    "gpt-4.1-mini":      {"input": 0.40, "output": 1.60, "cached": 0.10},
}


def _estimate_cost(model: str, input_tok: int, output_tok: int,
                   cache_read_tok: int = 0) -> float:
    rates = _RATES.get(model)
    if not rates:
        # Prefix match
        for k, v in _RATES.items():
            if model.startswith(k):
                rates = v
                break
    if not rates:
        rates = _RATES.get("claude-sonnet-4-6", {"input": 3.0, "output": 15.0, "cached": 0.30})

    fresh_input = max(0, input_tok - cache_read_tok)
    return (
        fresh_input * rates["input"] / 1_000_000
        + cache_read_tok * rates["cached"] / 1_000_000
        + output_tok * rates["output"] / 1_000_000
    )


# ── Streaming SSE parsers ───────────────────────────────────────────────


def _run_anthropic_turn(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int,
    log_file: Optional[IO],
) -> TurnResult:
    """Execute one Anthropic API turn with streaming."""
    result = TurnResult(turn_number=0, label="")
    t0 = time.monotonic()

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "stream": True,
    }

    url = f"{base_url}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    text_parts: list[str] = []

    try:
        with client.stream("POST", url, headers=headers, json=body, timeout=120.0) as resp:
            if resp.status_code != 200:
                error_body = resp.read().decode(errors="replace")
                result.error = f"HTTP {resp.status_code}: {error_body[:200]}"
                result.latency_s = time.monotonic() - t0
                return result

            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    evt = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                evt_type = evt.get("type", "")

                if evt_type == "message_start":
                    usage = evt.get("message", {}).get("usage", {})
                    result.input_tokens = usage.get("input_tokens", 0)
                    result.cache_read_tokens = usage.get("cache_read_input_tokens", 0)
                    result.cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)

                elif evt_type == "content_block_delta":
                    text = evt.get("delta", {}).get("text", "")
                    if text:
                        text_parts.append(text)
                        if log_file:
                            log_file.write(text)
                            log_file.flush()

                elif evt_type == "message_delta":
                    usage = evt.get("usage", {})
                    result.output_tokens = usage.get("output_tokens", 0)

    except httpx.TimeoutException:
        result.error = "Request timed out"
    except Exception as e:
        result.error = str(e)

    result.latency_s = time.monotonic() - t0
    result.response_text = "".join(text_parts)
    result.cost_usd = _estimate_cost(
        model, result.input_tokens, result.output_tokens,
        result.cache_read_tokens,
    )
    return result


def _run_openai_turn(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int,
    log_file: Optional[IO],
) -> TurnResult:
    """Execute one OpenAI API turn with streaming."""
    result = TurnResult(turn_number=0, label="")
    t0 = time.monotonic()

    full_messages = [{"role": "system", "content": system}] + messages
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": full_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    url = f"{base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }

    text_parts: list[str] = []

    try:
        with client.stream("POST", url, headers=headers, json=body, timeout=120.0) as resp:
            if resp.status_code != 200:
                error_body = resp.read().decode(errors="replace")
                result.error = f"HTTP {resp.status_code}: {error_body[:200]}"
                result.latency_s = time.monotonic() - t0
                return result

            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    evt = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = evt.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        text_parts.append(text)
                        if log_file:
                            log_file.write(text)
                            log_file.flush()

                usage = evt.get("usage")
                if usage:
                    result.input_tokens = usage.get("prompt_tokens", 0)
                    result.output_tokens = usage.get("completion_tokens", 0)
                    cached = usage.get("prompt_tokens_details", {})
                    result.cache_read_tokens = cached.get("cached_tokens", 0)

    except httpx.TimeoutException:
        result.error = "Request timed out"
    except Exception as e:
        result.error = str(e)

    result.latency_s = time.monotonic() - t0
    result.response_text = "".join(text_parts)
    result.cost_usd = _estimate_cost(
        model, result.input_tokens, result.output_tokens,
        result.cache_read_tokens,
    )
    return result


# ── Main arm executor ───────────────────────────────────────────────────


def run_arm(
    scenario: Scenario,
    proxied: bool,
    log_path: Optional[Path] = None,
    on_turn_complete: Optional[callable] = None,
) -> ArmResult:
    """Run all turns of a scenario through one arm.

    Args:
        scenario: The parsed scenario to execute.
        proxied: If True, route through tokenpak proxy. If False, direct API.
        log_path: If set, write live conversation log to this file.
        on_turn_complete: Callback(turn_number, turn_result) after each turn.

    Returns:
        ArmResult with per-turn and aggregate metrics.
    """
    arm_name = "tokenpak" if proxied else "direct"
    result = ArmResult(arm_name=arm_name, model=scenario.model, provider=scenario.provider)

    try:
        api_key = _get_api_key(scenario.provider)
    except RuntimeError as e:
        result.error = str(e)
        return result

    base_url = _get_base_url(scenario.provider, proxied)

    # Determine the API function
    run_turn_fn = _run_anthropic_turn if scenario.provider == "anthropic" else _run_openai_turn

    log_file = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "w")

    try:
        # Write arm header to log
        if log_file:
            arm_label = "ARM B: With TokenPak" if proxied else "ARM A: Direct API"
            log_file.write(f"\n{'=' * 60}\n")
            log_file.write(f"  {arm_label}  |  {scenario.model}\n")
            log_file.write(f"{'=' * 60}\n\n")
            log_file.flush()

        # Conversation history (multi-turn)
        messages: list[dict] = []

        client = httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))

        for turn in scenario.turns:
            # Add user message to history
            messages.append({"role": "user", "content": turn.prompt})

            # Write turn header to log
            if log_file:
                log_file.write(f"\n{'─' * 60}\n")
                log_file.write(f"  Turn {turn.number}: {turn.label}\n")
                log_file.write(f"{'─' * 60}\n\n")
                log_file.write(f"[User]\n{turn.prompt}\n\n[Assistant]\n")
                log_file.flush()

            # Execute the turn
            turn_result = run_turn_fn(
                client=client,
                base_url=base_url,
                api_key=api_key,
                model=scenario.model,
                system=scenario.system,
                messages=messages,
                max_tokens=scenario.max_tokens,
                log_file=log_file,
            )
            turn_result.turn_number = turn.number
            turn_result.label = turn.label

            # Add assistant response to history for next turn
            if turn_result.response_text:
                messages.append({"role": "assistant", "content": turn_result.response_text})

            # Write turn metrics to log
            if log_file:
                log_file.write(f"\n\n")
                if turn_result.error:
                    log_file.write(f"  ERROR: {turn_result.error}\n")
                else:
                    cache_str = ""
                    if turn_result.cache_read_tokens:
                        cache_str = f" ({turn_result.cache_read_tokens:,} cached)"
                    log_file.write(
                        f"  {turn_result.input_tokens:,} input{cache_str}"
                        f" / {turn_result.output_tokens:,} output"
                        f" / {turn_result.latency_s:.1f}s"
                        f" / ${turn_result.cost_usd:.4f}\n"
                    )
                log_file.flush()

            result.turns.append(turn_result)

            if on_turn_complete:
                on_turn_complete(turn.number, turn_result)

            # Bail on error
            if turn_result.error:
                result.error = f"Turn {turn.number} failed: {turn_result.error}"
                break

        client.close()

    finally:
        if log_file:
            log_file.write(f"\n{'=' * 60}\n")
            log_file.write("  COMPLETE\n")
            log_file.write(f"{'=' * 60}\n")
            log_file.flush()
            log_file.close()

    result.finalize()
    return result
