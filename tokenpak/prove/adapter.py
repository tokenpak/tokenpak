# SPDX-License-Identifier: Apache-2.0
"""Pluggable adapter system for prove runs.

An adapter encodes how to execute a turn against a specific
(platform, provider, model) combination and extract metrics from it.

Three axes, all user-extensible:

  **Provider format** — how to build the HTTP request body and parse
  the streaming response for a given LLM provider's API shape:
    - ``anthropic``:  /v1/messages, SSE with message_start/delta
    - ``openai``:     /v1/chat/completions, SSE with choices[].delta
                      (also covers xAI/Grok, Together, Fireworks, etc.)
    - ``google``:     /v1beta/models/…:streamGenerateContent

  **Platform** — how the request reaches the provider:
    - ``api``:   direct HTTPS to provider
    - ``proxy``: HTTPS through the tokenpak proxy (compression, caching active)
    - ``cli``:   subprocess (``claude -p``, ``codex exec``, etc.)

  **Model + pricing** — resolved from the built-in table or user config.

Users register custom providers in ``~/.tokenpak/prove/providers.yaml``::

    providers:
      grok:
        format: openai            # reuse OpenAI-compatible format
        base_url: https://api.x.ai/v1
        api_key_env: XAI_API_KEY
        models:
          grok-3:       { input: 3.0, output: 15.0, cached: 0.30 }
          grok-3-mini:  { input: 0.30, output: 0.50, cached: 0.03 }
      google:
        format: google
        base_url: https://generativelanguage.googleapis.com/v1beta
        api_key_env: GOOGLE_API_KEY
        models:
          gemini-2.5-pro:   { input: 1.25, output: 10.0, cached: 0.3125 }
          gemini-2.5-flash: { input: 0.15, output: 0.60, cached: 0.0375 }
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Optional

import httpx
import yaml


# ── Data classes ────────────────────────────────────────────────────────


@dataclass
class TurnResult:
    """Metrics from one turn of the conversation."""

    turn_number: int = 0
    label: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    response_text: str = ""
    error: str = ""


@dataclass
class ArmConfig:
    """Configuration for one arm of a prove run."""

    name: str
    platform: str              # api | proxy | cli
    provider: str              # anthropic | openai | google | grok | …
    model: str                 # claude-sonnet-4-6 | gpt-4o | …
    via_tokenpak: bool = False  # shorthand: True sets platform=proxy

    # Overrides (optional — resolved from registry if blank)
    base_url: str = ""
    api_key_env: str = ""
    format: str = ""           # anthropic | openai | google (auto-detected)
    cli_command: str = ""      # for platform=cli, e.g. "claude -p" or "codex exec"

    def resolve(self) -> "ArmConfig":
        """Fill in blanks from the provider registry."""
        if self.via_tokenpak and self.platform == "api":
            self.platform = "proxy"

        reg = _get_provider(self.provider)

        if not self.format:
            self.format = reg.get("format", self.provider)
        if not self.base_url:
            if self.platform == "proxy":
                self.base_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
            else:
                self.base_url = reg.get("base_url", "")
        if not self.api_key_env:
            self.api_key_env = reg.get("api_key_env", "")
        if not self.cli_command and self.platform == "cli":
            self.cli_command = reg.get("cli_command", "")

        return self


@dataclass
class ArmResult:
    """Aggregate results from one arm."""

    arm_name: str
    platform: str = ""
    provider: str = ""
    model: str = ""
    via_tokenpak: bool = False
    turns: list[TurnResult] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cost_usd: float = 0.0
    total_latency_s: float = 0.0
    error: str = ""

    def finalize(self) -> None:
        self.total_input_tokens = sum(t.input_tokens for t in self.turns)
        self.total_output_tokens = sum(t.output_tokens for t in self.turns)
        self.total_cache_read_tokens = sum(t.cache_read_tokens for t in self.turns)
        self.total_cost_usd = sum(t.cost_usd for t in self.turns)
        self.total_latency_s = sum(t.latency_s for t in self.turns)


# ═══════════════════════════════════════════════════════════════════════
# Provider registry
# ═══════════════════════════════════════════════════════════════════════

_BUILTIN_PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "format": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key_env": "ANTHROPIC_API_KEY",
        "cli_command": "claude -p",
        "models": {
            "claude-opus-4-6":   {"input": 15.0, "output": 75.0, "cached": 1.50},
            "claude-opus-4-5":   {"input": 15.0, "output": 75.0, "cached": 1.50},
            "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0, "cached": 0.30},
            "claude-sonnet-4-5": {"input": 3.0,  "output": 15.0, "cached": 0.30},
            "claude-haiku-4-5":  {"input": 0.80, "output": 4.0,  "cached": 0.08},
        },
    },
    "openai": {
        "format": "openai",
        "base_url": "https://api.openai.com",
        "api_key_env": "OPENAI_API_KEY",
        "cli_command": "codex exec",
        "models": {
            "gpt-4o":       {"input": 2.50, "output": 10.0, "cached": 1.25},
            "gpt-4o-mini":  {"input": 0.15, "output": 0.60, "cached": 0.075},
            "o3":           {"input": 10.0, "output": 40.0, "cached": 5.0},
            "o3-mini":      {"input": 1.10, "output": 4.40, "cached": 0.55},
            "o4-mini":      {"input": 1.10, "output": 4.40, "cached": 0.55},
            "o1":           {"input": 15.0, "output": 60.0, "cached": 7.50},
            "gpt-4.1":      {"input": 2.0,  "output": 8.0,  "cached": 0.50},
            "gpt-4.1-mini": {"input": 0.40, "output": 1.60, "cached": 0.10},
            "gpt-4.1-nano": {"input": 0.10, "output": 0.40, "cached": 0.025},
        },
    },
    "google": {
        "format": "google",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key_env": "GOOGLE_API_KEY",
        "models": {
            "gemini-2.5-pro":   {"input": 1.25, "output": 10.0, "cached": 0.3125},
            "gemini-2.5-flash": {"input": 0.15, "output": 0.60, "cached": 0.0375},
            "gemini-2.0-flash": {"input": 0.075,"output": 0.30, "cached": 0.01875},
        },
    },
    "xai": {
        "format": "openai",
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
        "models": {
            "grok-3":      {"input": 3.0,  "output": 15.0, "cached": 0.30},
            "grok-3-mini": {"input": 0.30, "output": 0.50, "cached": 0.03},
        },
    },
}

_USER_CONFIG_PATH = Path.home() / ".tokenpak" / "prove" / "providers.yaml"
_user_providers: Optional[dict] = None


def _load_user_providers() -> dict:
    global _user_providers
    if _user_providers is not None:
        return _user_providers
    _user_providers = {}
    if _USER_CONFIG_PATH.exists():
        try:
            data = yaml.safe_load(_USER_CONFIG_PATH.read_text()) or {}
            _user_providers = data.get("providers", {})
        except Exception:
            pass
    return _user_providers


def _get_provider(name: str) -> dict:
    """Look up provider config by name (user overrides built-in)."""
    user = _load_user_providers()
    if name in user:
        merged = {**_BUILTIN_PROVIDERS.get(name, {}), **user[name]}
        if "models" in user[name] and name in _BUILTIN_PROVIDERS:
            merged["models"] = {**_BUILTIN_PROVIDERS[name].get("models", {}), **user[name]["models"]}
        return merged
    if name in _BUILTIN_PROVIDERS:
        return _BUILTIN_PROVIDERS[name]
    return {}


def _get_model_rates(provider: str, model: str) -> dict[str, float]:
    """Get pricing rates for a model, with prefix fallback."""
    reg = _get_provider(provider)
    models = reg.get("models", {})
    if model in models:
        return models[model]
    for key, rates in models.items():
        if model.startswith(key):
            return rates
    return {"input": 3.0, "output": 15.0, "cached": 0.30}


def list_providers() -> list[dict]:
    """List all available providers with their models."""
    result = []
    seen = set()
    for source, providers in [("user", _load_user_providers()), ("built-in", _BUILTIN_PROVIDERS)]:
        for name, cfg in providers.items():
            if name in seen:
                continue
            seen.add(name)
            merged = _get_provider(name)
            result.append({
                "name": name,
                "format": merged.get("format", name),
                "base_url": merged.get("base_url", ""),
                "models": list(merged.get("models", {}).keys()),
                "source": source,
            })
    return result


# ═══════════════════════════════════════════════════════════════════════
# Cost estimation
# ═══════════════════════════════════════════════════════════════════════


def estimate_cost(provider: str, model: str, input_tok: int,
                  output_tok: int, cache_read_tok: int = 0) -> float:
    rates = _get_model_rates(provider, model)
    fresh = max(0, input_tok - cache_read_tok)
    return (
        fresh * rates["input"] / 1_000_000
        + cache_read_tok * rates["cached"] / 1_000_000
        + output_tok * rates["output"] / 1_000_000
    )


# ═══════════════════════════════════════════════════════════════════════
# Provider formats — request building + response parsing
# ═══════════════════════════════════════════════════════════════════════

_MAX_RETRIES = 3
_RETRY_BACKOFF = [10, 30, 60]  # seconds between retries for 429/529


def _run_turn_anthropic(
    client: httpx.Client, base_url: str, api_key: str,
    model: str, system: str, messages: list[dict],
    max_tokens: int, log_file: Optional[IO],
) -> TurnResult:
    result = TurnResult()
    t0 = time.monotonic()
    body = {"model": model, "max_tokens": max_tokens, "system": system,
            "messages": messages, "stream": True}
    url = f"{base_url}/v1/messages"
    headers = {"anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    parts: list[str] = []
    last_error = ""
    for attempt in range(_MAX_RETRIES + 1):
        parts.clear()
        last_error = ""
        try:
            with client.stream("POST", url, headers=headers, json=body, timeout=180.0) as resp:
                if resp.status_code in (429, 529) and attempt < _MAX_RETRIES:
                    resp.read()
                    wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    import sys as _sys
                    print(f"      rate limited, retrying in {wait}s...", file=_sys.stderr)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    result.error = f"HTTP {resp.status_code}: {resp.read().decode(errors='replace')[:200]}"
                    result.latency_s = time.monotonic() - t0
                    return result
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    d = line[6:]
                    if d == "[DONE]":
                        break
                    try:
                        evt = json.loads(d)
                    except json.JSONDecodeError:
                        continue
                    t = evt.get("type", "")
                    if t == "message_start":
                        u = evt.get("message", {}).get("usage", {})
                        result.input_tokens = u.get("input_tokens", 0)
                        result.cache_read_tokens = u.get("cache_read_input_tokens", 0)
                        result.cache_creation_tokens = u.get("cache_creation_input_tokens", 0)
                    elif t == "content_block_delta":
                        txt = evt.get("delta", {}).get("text", "")
                        if txt:
                            parts.append(txt)
                            if log_file:
                                log_file.write(txt); log_file.flush()
                    elif t == "message_delta":
                        result.output_tokens = evt.get("usage", {}).get("output_tokens", 0)
            break  # success
        except httpx.TimeoutException:
            last_error = "Request timed out"
        except Exception as e:
            last_error = str(e)
    if last_error:
        result.error = last_error
    result.latency_s = time.monotonic() - t0
    result.response_text = "".join(parts)
    return result


def _run_turn_openai(
    client: httpx.Client, base_url: str, api_key: str,
    model: str, system: str, messages: list[dict],
    max_tokens: int, log_file: Optional[IO],
) -> TurnResult:
    result = TurnResult()
    t0 = time.monotonic()
    full_msgs = [{"role": "system", "content": system}] + messages
    body = {"model": model, "max_tokens": max_tokens, "messages": full_msgs,
            "stream": True, "stream_options": {"include_usage": True}}
    url = f"{base_url}/v1/chat/completions"
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    parts: list[str] = []
    last_error = ""
    for attempt in range(_MAX_RETRIES + 1):
        parts.clear()
        last_error = ""
        try:
            with client.stream("POST", url, headers=headers, json=body, timeout=180.0) as resp:
                if resp.status_code == 429 and attempt < _MAX_RETRIES:
                    resp.read()
                    wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    import sys as _sys
                    print(f"      rate limited, retrying in {wait}s...", file=_sys.stderr)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    result.error = f"HTTP {resp.status_code}: {resp.read().decode(errors='replace')[:200]}"
                    result.latency_s = time.monotonic() - t0
                    return result
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    d = line[6:].strip()
                    if d == "[DONE]":
                        break
                    try:
                        evt = json.loads(d)
                    except json.JSONDecodeError:
                        continue
                    choices = evt.get("choices", [])
                    if choices:
                        txt = choices[0].get("delta", {}).get("content", "")
                        if txt:
                            parts.append(txt)
                            if log_file:
                                log_file.write(txt); log_file.flush()
                    usage = evt.get("usage")
                    if usage:
                        result.input_tokens = usage.get("prompt_tokens", 0)
                        result.output_tokens = usage.get("completion_tokens", 0)
                        det = usage.get("prompt_tokens_details") or {}
                        result.cache_read_tokens = det.get("cached_tokens", 0)
            break  # success
        except httpx.TimeoutException:
            last_error = "Request timed out"
        except Exception as e:
            last_error = str(e)
    if last_error:
        result.error = last_error
    result.latency_s = time.monotonic() - t0
    result.response_text = "".join(parts)
    return result


def _run_turn_google(
    client: httpx.Client, base_url: str, api_key: str,
    model: str, system: str, messages: list[dict],
    max_tokens: int, log_file: Optional[IO],
) -> TurnResult:
    """Google Gemini generateContent (streaming)."""
    result = TurnResult()
    t0 = time.monotonic()
    # Convert messages to Gemini contents format
    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    url = f"{base_url}/models/{model}:streamGenerateContent?alt=sse"
    if api_key:
        url += f"&key={api_key}"
    headers = {"content-type": "application/json"}
    parts: list[str] = []
    try:
        with client.stream("POST", url, headers=headers, json=body, timeout=120.0) as resp:
            if resp.status_code != 200:
                result.error = f"HTTP {resp.status_code}: {resp.read().decode(errors='replace')[:200]}"
                result.latency_s = time.monotonic() - t0
                return result
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    evt = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                for cand in evt.get("candidates", []):
                    for part in cand.get("content", {}).get("parts", []):
                        txt = part.get("text", "")
                        if txt:
                            parts.append(txt)
                            if log_file:
                                log_file.write(txt); log_file.flush()
                usage = evt.get("usageMetadata")
                if usage:
                    result.input_tokens = usage.get("promptTokenCount", 0)
                    result.output_tokens = usage.get("candidatesTokenCount", 0)
                    result.cache_read_tokens = usage.get("cachedContentTokenCount", 0)
    except httpx.TimeoutException:
        result.error = "Request timed out"
    except Exception as e:
        result.error = str(e)
    result.latency_s = time.monotonic() - t0
    result.response_text = "".join(parts)
    return result


_FORMAT_DISPATCH = {
    "anthropic": _run_turn_anthropic,
    "openai": _run_turn_openai,
    "google": _run_turn_google,
}


# ═══════════════════════════════════════════════════════════════════════
# Platform executors
# ═══════════════════════════════════════════════════════════════════════


def _resolve_api_key(provider: str, api_key_env: str) -> str:
    """Resolve API key from env vars, then CLI OAuth tokens as fallback."""
    # 1. Env var
    key = os.environ.get(api_key_env, "").strip()
    if key:
        return key

    # 2. Claude CLI OAuth token (~/.claude/.credentials.json)
    if provider == "anthropic":
        try:
            creds_path = Path.home() / ".claude" / ".credentials.json"
            if creds_path.exists():
                data = json.loads(creds_path.read_text())
                token = data.get("claudeAiOauth", {}).get("accessToken", "")
                if token:
                    return token
        except Exception:
            pass

    # 3. Codex OAuth token (~/.codex/auth.json)
    if provider == "openai":
        try:
            auth_path = Path.home() / ".codex" / "auth.json"
            if auth_path.exists():
                data = json.loads(auth_path.read_text())
                token = data.get("tokens", {}).get("access_token", "")
                if token:
                    return token
        except Exception:
            pass

    return ""


def _execute_turn_api(
    cfg: ArmConfig, system: str, messages: list[dict],
    max_tokens: int, log_file: Optional[IO], client: httpx.Client,
) -> TurnResult:
    """Execute via direct HTTP API call (bypasses proxy)."""
    api_key = _resolve_api_key(cfg.provider, cfg.api_key_env)
    if not api_key:
        return TurnResult(error=f"No API key found for {cfg.provider}")
    run_fn = _FORMAT_DISPATCH.get(cfg.format)
    if not run_fn:
        return TurnResult(error=f"Unknown format: {cfg.format}")
    return run_fn(client, cfg.base_url, api_key, cfg.model, system,
                  messages, max_tokens, log_file)


def _execute_turn_proxy(
    cfg: ArmConfig, system: str, messages: list[dict],
    max_tokens: int, log_file: Optional[IO], client: httpx.Client,
) -> TurnResult:
    """Execute via tokenpak proxy — same format, different base URL.

    If no env API key is set, send with empty key so the proxy injects
    its own credentials (from key pool, Claude CLI OAuth, or Codex OAuth).
    Sending a fake key would cause the proxy to pass it through as-is,
    which the upstream provider rejects.
    """
    api_key = os.environ.get(cfg.api_key_env, "")
    run_fn = _FORMAT_DISPATCH.get(cfg.format)
    if not run_fn:
        return TurnResult(error=f"Unknown format: {cfg.format}")
    return run_fn(client, cfg.base_url, api_key, cfg.model, system,
                  messages, max_tokens, log_file)


def _execute_turn_cli(
    cfg: ArmConfig, system: str, messages: list[dict],
    max_tokens: int, log_file: Optional[IO], client: httpx.Client,
) -> TurnResult:
    """Execute via CLI subprocess (claude -p, codex exec, etc.).

    CLI platforms only support the latest user message (not full multi-turn
    history), so we concatenate the conversation into a single prompt.
    """
    result = TurnResult()
    t0 = time.monotonic()

    # Build a single prompt from conversation history
    prompt_parts = []
    if system:
        prompt_parts.append(f"[System: {system}]")
    for m in messages:
        role = m["role"].capitalize()
        prompt_parts.append(f"[{role}]\n{m['content']}")
    full_prompt = "\n\n".join(prompt_parts)

    cmd = cfg.cli_command
    if not cmd:
        result.error = f"No cli_command configured for provider {cfg.provider}"
        return result

    # Parse command + add prompt
    cmd_parts = cmd.split()
    cmd_parts.append(full_prompt)

    try:
        proc = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=300,
        )
        result.response_text = proc.stdout.strip()
        if proc.returncode != 0 and not result.response_text:
            result.error = proc.stderr.strip()[:200] or f"Exit code {proc.returncode}"

        if log_file:
            log_file.write(result.response_text)
            log_file.flush()

        # CLI platforms don't report token counts — estimate from text
        result.input_tokens = len(full_prompt) // 4
        result.output_tokens = len(result.response_text) // 4

    except subprocess.TimeoutExpired:
        result.error = "CLI command timed out"
    except FileNotFoundError:
        result.error = f"Command not found: {cmd_parts[0]}"
    except Exception as e:
        result.error = str(e)

    result.latency_s = time.monotonic() - t0
    return result


_PLATFORM_DISPATCH = {
    "api": _execute_turn_api,
    "proxy": _execute_turn_proxy,
    "cli": _execute_turn_cli,
}


# ═══════════════════════════════════════════════════════════════════════
# Public API — execute a full arm
# ═══════════════════════════════════════════════════════════════════════


def run_arm(
    cfg: ArmConfig,
    turns: list,
    system: str,
    max_tokens: int,
    log_path: Optional[Path] = None,
    on_turn_complete: Optional[callable] = None,
) -> ArmResult:
    """Run all turns through one arm using the adapter system.

    Args:
        cfg: Resolved ArmConfig (platform + provider + model).
        turns: List of Turn objects from the scenario.
        system: System prompt.
        max_tokens: Max output tokens per turn.
        log_path: Live log file for display.
        on_turn_complete: Callback(turn_number, TurnResult).

    Returns:
        ArmResult with per-turn and aggregate metrics.
    """
    cfg = cfg.resolve()

    result = ArmResult(
        arm_name=cfg.name,
        platform=cfg.platform,
        provider=cfg.provider,
        model=cfg.model,
        via_tokenpak=cfg.via_tokenpak,
    )

    executor = _PLATFORM_DISPATCH.get(cfg.platform)
    if not executor:
        result.error = f"Unknown platform: {cfg.platform}"
        return result

    log_file = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "w")

    try:
        if log_file:
            tp_label = " + TokenPak" if cfg.via_tokenpak else ""
            log_file.write(f"\n{'=' * 60}\n")
            log_file.write(f"  {cfg.name}  |  {cfg.platform}/{cfg.provider}/{cfg.model}{tp_label}\n")
            log_file.write(f"{'=' * 60}\n\n")
            log_file.flush()

        messages: list[dict] = []
        client = httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))

        for turn in turns:
            messages.append({"role": "user", "content": turn.prompt})

            if log_file:
                log_file.write(f"\n{'─' * 60}\n")
                log_file.write(f"  Turn {turn.number}: {turn.label}\n")
                log_file.write(f"{'─' * 60}\n\n[User]\n{turn.prompt}\n\n[Assistant]\n")
                log_file.flush()

            turn_result = executor(cfg, system, messages, max_tokens, log_file, client)
            turn_result.turn_number = turn.number
            turn_result.label = turn.label
            turn_result.cost_usd = estimate_cost(
                cfg.provider, cfg.model,
                turn_result.input_tokens, turn_result.output_tokens,
                turn_result.cache_read_tokens,
            )

            if turn_result.response_text:
                messages.append({"role": "assistant", "content": turn_result.response_text})

            if log_file:
                log_file.write("\n\n")
                if turn_result.error:
                    log_file.write(f"  ERROR: {turn_result.error}\n")
                else:
                    cache = f" ({turn_result.cache_read_tokens:,} cached)" if turn_result.cache_read_tokens else ""
                    log_file.write(
                        f"  {turn_result.input_tokens:,} input{cache}"
                        f" / {turn_result.output_tokens:,} output"
                        f" / {turn_result.latency_s:.1f}s"
                        f" / ${turn_result.cost_usd:.4f}\n"
                    )
                log_file.flush()

            result.turns.append(turn_result)
            if on_turn_complete:
                on_turn_complete(turn.number, turn_result)
            if turn_result.error:
                result.error = f"Turn {turn.number}: {turn_result.error}"
                break

        client.close()

    finally:
        if log_file:
            log_file.write(f"\n{'=' * 60}\n  COMPLETE\n{'=' * 60}\n")
            log_file.flush()
            log_file.close()

    result.finalize()
    return result
