# SPDX-License-Identifier: Apache-2.0
"""Interactive A/B test command — ``tokenpak test``.

Launches an arrow-key picker that auto-detects available platforms,
providers, and models, then runs a 5-turn test with live parallel
windows and a comparison report in the triggering terminal.

Auto-detection:
  - Platforms:  checks binaries (claude, codex) + proxy health
  - Providers:  checks env vars (ANTHROPIC_API_KEY, etc.)
  - Models:     lists from detected provider's pricing table

Only options the user actually has set up are shown.
"""

from __future__ import annotations

__all__ = (
    "run",
    "run_test",
)


import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional, TypedDict

import httpx

from tokenpak._formatting.picker import pick as _shared_pick

if TYPE_CHECKING:
    from tokenpak.prove.adapter import ArmConfig, ArmResult


class Scenario(TypedDict):
    """One built-in comparison scenario."""

    name: str
    label: str
    system: str
    turns: list[tuple[str, str]]


_TEST_HEADER = "\n  \033[1mtokenpak test\033[0m\n"


def _pick(title: str, options: list[tuple[str, str]], subtitle: str = "") -> Optional[str]:
    """Thin wrapper around the shared picker with test-command branding."""
    return _shared_pick(title, options, subtitle=subtitle, header=_TEST_HEADER)


# ═══════════════════════════════════════════════════════════════════════
# Auto-detection — checks all auth sources, not just env vars
# ═══════════════════════════════════════════════════════════════════════

# Typed caches keep each detection result from being confused with another.
_proxy_detection_cache: tuple[bool, list[str]] | None = None
_claude_auth_cache: bool | None = None
_codex_auth_cache: bool | None = None


def _detect_proxy() -> tuple[bool, list[str]]:
    """Check proxy status and which providers it routes.

    Returns (is_running, list_of_provider_names).
    """
    global _proxy_detection_cache
    if _proxy_detection_cache is not None:
        return _proxy_detection_cache

    proxy_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
    try:
        resp = httpx.get(f"{proxy_url}/health", timeout=2.0)
        if resp.status_code != 200:
            _proxy_detection_cache = (False, [])
            return False, []
        health = resp.json()
        if not isinstance(health, dict):
            _proxy_detection_cache = (False, [])
            return False, []
        # Providers live below the canonical circuit-breaker envelope.  The
        # envelope's metadata keys (enabled/any_open) are not providers.
        breakers = health.get("circuit_breakers")
        provider_states = breakers.get("providers") if isinstance(breakers, dict) else None
        providers = (
            [str(provider) for provider in provider_states]
            if isinstance(provider_states, dict)
            else []
        )
        _proxy_detection_cache = (True, providers)
        return True, providers
    except Exception:
        _proxy_detection_cache = (False, [])
        return False, []


def _detect_claude_code_auth() -> bool:
    """Check if Claude Code is authenticated."""
    global _claude_auth_cache
    if _claude_auth_cache is not None:
        return _claude_auth_cache

    creds = Path.home() / ".claude" / ".credentials.json"
    if creds.exists():
        try:
            import json as _json

            data = _json.loads(creds.read_text())
            if not isinstance(data, dict):
                raise ValueError("Claude credentials are not a JSON object")
            # Has OAuth or API key config
            ok = bool(data.get("claudeAiOauth") or data.get("apiKey"))
            _claude_auth_cache = ok
            return ok
        except Exception:
            pass
    _claude_auth_cache = False
    return False


def _detect_codex_auth() -> bool:
    """Check if Codex is authenticated."""
    global _codex_auth_cache
    if _codex_auth_cache is not None:
        return _codex_auth_cache

    auth_file = Path.home() / ".codex" / "auth.json"
    if auth_file.exists():
        try:
            import json as _json

            data = _json.loads(auth_file.read_text())
            if not isinstance(data, dict):
                raise ValueError("Codex credentials are not a JSON object")
            # Has API key or OAuth tokens
            ok = bool(data.get("OPENAI_API_KEY") or data.get("tokens"))
            _codex_auth_cache = ok
            return ok
        except Exception:
            pass
    _codex_auth_cache = False
    return False


def _detect_platforms() -> list[tuple[str, str]]:
    """Detect available platforms by checking binaries, proxy, and auth."""
    platforms = []

    proxy_running, proxy_providers = _detect_proxy()

    # API (direct) — needs env var keys
    has_any_key = any(
        os.environ.get(k)
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "XAI_API_KEY")
    )
    if has_any_key:
        platforms.append(("api", "API Direct  (env API keys)"))

    # API via proxy — proxy handles keys
    if proxy_running:
        providers_str = ", ".join(proxy_providers) if proxy_providers else "configured"
        platforms.append(("proxy", f"API via TokenPak Proxy  ({providers_str})"))

    # Claude Code — needs binary + auth
    if shutil.which("claude") and _detect_claude_code_auth():
        platforms.append(("claude-code", "Claude Code  (authenticated)"))

    # Codex — needs binary + auth
    if shutil.which("codex") and _detect_codex_auth():
        platforms.append(("codex", "Codex  (authenticated)"))

    return platforms


def _detect_providers() -> list[tuple[str, str]]:
    """Detect available providers from all auth sources.

    Checks (in order): proxy circuit_breakers, Claude Code auth,
    Codex auth, env vars, user providers.yaml.
    """
    found: dict[str, str] = {}  # provider_id → detection reason

    # 1. Proxy — knows which providers are routed
    proxy_running, proxy_providers = _detect_proxy()
    if proxy_running:
        _LABELS = {
            "anthropic": "Anthropic",
            "openai": "OpenAI",
            "google": "Google (Gemini)",
            "xai": "xAI (Grok)",
        }
        for p in proxy_providers:
            if p not in found:
                found[p] = "via proxy"

    # 2. Claude Code auth → Anthropic
    if _detect_claude_code_auth() and "anthropic" not in found:
        found["anthropic"] = "via Claude Code"

    # 3. Codex auth → OpenAI
    if _detect_codex_auth() and "openai" not in found:
        found["openai"] = "via Codex"

    # 4. Env vars (direct API keys)
    _ENV_CHECKS = [
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("google", "GOOGLE_API_KEY"),
        ("xai", "XAI_API_KEY"),
    ]
    for pid, env_var in _ENV_CHECKS:
        if os.environ.get(env_var) and pid not in found:
            found[pid] = f"{env_var}"

    # 5. User providers.yaml
    user_cfg = Path.home() / ".tokenpak" / "prove" / "providers.yaml"
    if user_cfg.exists():
        try:
            import yaml

            data = yaml.safe_load(user_cfg.read_text()) or {}
            for name, cfg in data.get("providers", {}).items():
                env_key = cfg.get("api_key_env", "")
                if env_key and os.environ.get(env_key) and name not in found:
                    found[name] = env_key
        except Exception:
            pass

    _LABELS = {
        "anthropic": "Anthropic",
        "openai": "OpenAI",
        "google": "Google (Gemini)",
        "xai": "xAI (Grok)",
    }

    return [(pid, f"{_LABELS.get(pid, pid)}  ({reason})") for pid, reason in found.items()]


def _detect_proxy_running() -> bool:
    """Quick check — is the proxy alive?"""
    running, _ = _detect_proxy()
    return running


def _get_models(provider: str) -> list[tuple[str, str]]:
    """Get available models for a provider with pricing info."""
    from tokenpak.prove.adapter import _get_provider

    reg = _get_provider(provider)
    models = reg.get("models", {})
    result = []
    for name, rates in models.items():
        price = f"${rates['input']}/{rates['output']} per 1M tokens"
        result.append((name, f"{name}  ({price})"))
    return result


# ═══════════════════════════════════════════════════════════════════════
# Built-in 10-turn test scenarios — lightweight prompts for fast turns
# that build up cached context to demonstrate multi-turn savings.
#
# How the comparison works:
#   Arm 1 (Claude Code):    claude -p → direct to Anthropic
#   Arm 2 (w/ TokenPak):    tokenpak claude -p → auto-routes through
#                           tokenpak proxy (compression + caching + dedup)
#                           + companion MCP tools available
#
# Design principles:
#   - Prompts are 200-400 chars each (substantial but not bloated)
#   - Repeated references to the same code/specs → proxy compression
#   - Re-stating requirements and context → dedup opportunities
#   - 10 turns builds growing conversation → cache savings compound
# ═══════════════════════════════════════════════════════════════════════

_SCENARIOS: dict[str, Scenario] = {
    "coding": {
        "name": "Coding — Config Parser",
        "label": "Coding  (build a config parser — 10 turns)",
        "system": "You are a Python engineer. Keep responses concise (under 200 words). Show code when asked. Use type hints.",
        "turns": [
            (
                "Design",
                (
                    "I need a Python `ConfigManager` class that loads TOML config files, "
                    "supports dotted key access like 'database.host', and can write changes "
                    "back atomically. What should the public API look like? Show the class "
                    "signature with method names and type signatures — no implementation yet."
                ),
            ),
            (
                "Init",
                (
                    "For the `ConfigManager` class with dotted key access and atomic writes "
                    "that we just designed: write the `__init__` method. It should accept a "
                    "file path, load and parse the TOML file, and raise `FileNotFoundError` "
                    "with a clear message if the file doesn't exist."
                ),
            ),
            (
                "Get method",
                (
                    "For our `ConfigManager` class: write the `get(key, default=None)` method. "
                    "It must support dotted keys like 'database.host' by walking nested dicts. "
                    "Return the default if any part of the key path is missing. Include the "
                    "type signature with generics."
                ),
            ),
            (
                "Set method",
                (
                    "For our `ConfigManager` class with dotted key support: write the "
                    "`set(key, value)` method. It should support dotted keys like 'database.host' "
                    "and automatically create intermediate nested dicts when they don't exist. "
                    "Raise `TypeError` if an intermediate key exists but isn't a dict."
                ),
            ),
            (
                "Save",
                (
                    "For our `ConfigManager` class that supports dotted key access: write the "
                    "`save()` method. It must write the config back to the TOML file atomically "
                    "using a temporary file and `os.replace()`. Preserve the original file on "
                    "write failure. Include error handling."
                ),
            ),
            (
                "Validate",
                (
                    "For our `ConfigManager` class with get/set/save: add a `validate(schema)` "
                    "method. The schema is a dict mapping dotted key paths to their expected "
                    "Python types, like `{'database.host': str, 'database.port': int}`. "
                    "Return a list of `(key, expected_type, actual_type)` tuples for violations."
                ),
            ),
            (
                "Env override",
                (
                    "For our `ConfigManager` class with get/set/save/validate: add environment "
                    "variable override support. The `get()` method should first check for an "
                    "env var like `CONFIG_DATABASE_HOST` (uppercase, dots→underscores) before "
                    "falling back to the TOML value. Show the updated `get()` method."
                ),
            ),
            (
                "Test get/set",
                (
                    "For our `ConfigManager` class with dotted key access and env overrides: "
                    "write 4 pytest test functions covering: basic get, dotted key get, "
                    "get with env var override, and set creating nested keys. Use `tmp_path` "
                    "fixture for the TOML file."
                ),
            ),
            (
                "Test save",
                (
                    "For our `ConfigManager` class with atomic save via `os.replace()`: write "
                    "3 pytest tests covering: basic save round-trip (load→set→save→reload), "
                    "save atomicity (file intact after simulated write failure), and validate "
                    "catching a type mismatch."
                ),
            ),
            (
                "Docstring",
                (
                    "For our complete `ConfigManager` class with get/set/save/validate and env "
                    "override support: write a comprehensive module-level docstring covering "
                    "what it does, all public methods with one-line descriptions, the env var "
                    "override convention, and a 5-line usage example."
                ),
            ),
        ],
    },
    "planning": {
        "name": "Planning — API Design",
        "label": "Planning  (design a REST API — 10 turns)",
        "system": "You are a backend architect. Keep responses concise (under 200 words). Be precise and specific.",
        "turns": [
            (
                "Resources",
                (
                    "We're building a bookmark manager REST API. Users can save URLs, organize "
                    "them into collections, and tag them for search. What are the core resources "
                    "and their relationships? List each resource with its key fields and how "
                    "they relate to each other."
                ),
            ),
            (
                "Bookmark CRUD",
                (
                    "For our bookmark manager API with bookmarks, collections, and tags: define "
                    "the CRUD endpoints for the Bookmark resource. Show HTTP method, path, "
                    "request body fields, and response status codes for each endpoint. Bookmarks "
                    "have: url, title, description, collection_id, and tags."
                ),
            ),
            (
                "Collections",
                (
                    "For our bookmark manager API where bookmarks belong to collections: define "
                    "the CRUD endpoints for collections. A collection has a name, description, "
                    "and optional parent_id for nesting. Include an endpoint to list all bookmarks "
                    "in a collection. Show method, path, and key fields."
                ),
            ),
            (
                "Tags & search",
                (
                    "For our bookmark manager API with bookmarks, collections, and tags: design "
                    "the tagging and search endpoints. Tags are simple strings attached to "
                    "bookmarks (many-to-many). Include: add/remove tags, list all tags, search "
                    "bookmarks by tag/title/url with query parameters."
                ),
            ),
            (
                "Auth",
                (
                    "For our bookmark manager API with bookmarks, collections, and tags: how "
                    "should authentication work? We need user isolation (each user sees only "
                    "their own bookmarks). Describe the auth approach: token format, header "
                    "convention, how to get a token, and token expiry strategy."
                ),
            ),
            (
                "Errors",
                (
                    "For our bookmark manager API with auth, bookmarks, collections, and tags: "
                    "define the error response format. All errors should use a consistent JSON "
                    "shape. Show examples for: 401 unauthorized, 404 bookmark not found, and "
                    "422 validation error (missing required field 'url')."
                ),
            ),
            (
                "Pagination",
                (
                    "For our bookmark manager API: how should list endpoints handle pagination? "
                    "We have list-bookmarks, list-collections, list-tags, and search endpoints "
                    "that all need it. Define the query parameters and the response envelope "
                    "with pagination metadata."
                ),
            ),
            (
                "Rate limits",
                (
                    "For our bookmark manager API with auth, CRUD, search, and pagination: "
                    "what rate limits should we enforce? Consider read endpoints (list/get), "
                    "write endpoints (create/update/delete), and the search endpoint separately. "
                    "Give specific numbers per minute and explain the rationale."
                ),
            ),
            (
                "Import/export",
                (
                    "For our bookmark manager API with bookmarks, collections, and tags: design "
                    "a bulk import endpoint that accepts a Netscape bookmark HTML file (the "
                    "standard browser export format) and a bulk export endpoint that produces "
                    "one. Show the endpoints, content types, and key behaviors."
                ),
            ),
            (
                "Summary",
                (
                    "For our complete bookmark manager API with bookmarks, collections, tags, "
                    "search, auth, pagination, rate limits, and import/export: write a one-paragraph "
                    "API overview suitable for developer docs. Cover scope, auth model, key "
                    "design choices, and rate limit policy."
                ),
            ),
        ],
    },
    "codebase": {
        "name": "Codebase — Code Review",
        "label": "Codebase  (review and fix code — 10 turns)",
        "system": "You are a code reviewer. Keep responses concise (under 200 words). Show code when fixing issues.",
        "turns": [
            (
                "Review",
                (
                    "Review this Python function and list the top 5 issues (bugs, style, "
                    "performance, safety):\n```python\ndef process_users(data):\n"
                    "    result = []\n    for item in data:\n"
                    "        if item['status'] == 'active':\n"
                    "            user = {'name': item['name'], 'email': item['email'],\n"
                    "                    'score': item['score'] * 1.1}\n"
                    "            result.append(user)\n"
                    "    result = sorted(result, key=lambda x: x['score'], reverse=True)\n"
                    "    return result[:10]\n```"
                ),
            ),
            (
                "Type hints",
                (
                    "For the `process_users` function that filters active users, applies a "
                    "1.1x score multiplier, sorts by score, and returns the top 10: add proper "
                    "type hints. Define a TypedDict for the input items and the output items. "
                    "Show the full rewritten function signature."
                ),
            ),
            (
                "Comprehension",
                (
                    "For the `process_users` function that filters active users, computes "
                    "score * 1.1, sorts descending, and returns top 10: rewrite the filter + "
                    "transform as a list comprehension. Keep the sort and slice separate. "
                    "Is this actually faster than the loop? One sentence."
                ),
            ),
            (
                "Edge cases",
                (
                    "For our `process_users` function that filters by status='active' and "
                    "accesses item['score'] and item['email']: what happens if data is None? "
                    "If an item is missing the 'score' key? If 'email' contains None? Add "
                    "defensive handling for each case. Show the updated code."
                ),
            ),
            (
                "Extract scoring",
                (
                    "For our `process_users` function that multiplies score by 1.1: the "
                    "multiplier should be configurable, not hardcoded. Extract it into a "
                    "parameter with a default value. Also, should the scoring logic be its "
                    "own function? Show the refactored code."
                ),
            ),
            (
                "Naming",
                (
                    "For our `process_users` function that filters active users, applies "
                    "score multiplier, sorts by score, and returns top N: the name "
                    "'process_users' is vague. Suggest a better function name and better "
                    "parameter names. Explain your reasoning in one sentence each."
                ),
            ),
            (
                "Docstring",
                (
                    "For our renamed and improved function (formerly `process_users`) that "
                    "filters active users, applies a configurable score multiplier, sorts "
                    "descending by score, and returns the top N: write a Google-style docstring "
                    "with Args, Returns, and Raises sections."
                ),
            ),
            (
                "Test happy",
                (
                    "For our improved `process_users` function with type hints, configurable "
                    "multiplier, top-N, and defensive handling: write 3 pytest tests covering "
                    "normal input (verify filtering + sort + limit), custom multiplier value, "
                    "and the sort order (highest score first)."
                ),
            ),
            (
                "Test edge",
                (
                    "For our improved `process_users` function with defensive handling for "
                    "missing keys and None values: write 3 pytest tests covering empty input "
                    "list, an item missing the 'score' key, and an item with status != 'active' "
                    "being excluded from results."
                ),
            ),
            (
                "Final",
                (
                    "Show the final, complete version of the function (formerly `process_users`) "
                    "with all improvements applied: type hints, TypedDicts, configurable multiplier, "
                    "top-N parameter, defensive handling, better name, and docstring. Just the "
                    "code, no explanation."
                ),
            ),
        ],
    },
}


# ═══════════════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════════════


def _map_platform_to_adapter(platform: str, provider: str, model: str) -> list[ArmConfig]:
    """Map user-facing platform to adapter ArmConfigs.

    Each platform uses its native execution method:
      - claude-code: `claude -p` vs `tokenpak claude -p` (CLI subprocess,
        uses Claude Code's own OAuth billing — no API rate limit conflict)
      - codex: `codex exec` vs `tokenpak codex exec`
      - api/proxy: direct HTTP vs proxy HTTP (raw API key route)
    """
    from tokenpak.prove.adapter import ArmConfig, _get_provider, _resolve_api_key

    proxy_available = _detect_proxy_running()

    if platform == "claude-code":
        # Claude Code uses its own OAuth billing (not raw API rate limits).
        # The comparison: native claude vs claude with tokenpak companion.
        arms = [
            ArmConfig(
                name="Claude Code",
                platform="cli",
                provider="anthropic",
                model=model,
                cli_command="claude -p",
            ),
            ArmConfig(
                name="w/ TokenPak",
                platform="cli",
                provider="anthropic",
                model=model,
                cli_command="tokenpak claude -p",
                via_tokenpak=True,
            ),
        ]
        return arms

    elif platform == "codex":
        arms = [
            ArmConfig(
                name="Codex",
                platform="cli",
                provider="openai",
                model=model,
                cli_command="codex exec",
            ),
            ArmConfig(
                name="w/ TokenPak",
                platform="cli",
                provider="openai",
                model=model,
                cli_command="tokenpak codex exec",
                via_tokenpak=True,
            ),
        ]
        return arms

    elif platform in ("api", "proxy"):
        # API route: direct HTTP vs through proxy
        arms = []
        reg = _get_provider(provider)
        key_env = reg.get("api_key_env", "")
        has_key = bool(_resolve_api_key(provider, key_env))

        if has_key:
            arms.append(
                ArmConfig(
                    name="Direct",
                    platform="api",
                    provider=provider,
                    model=model,
                    base_url=reg.get("base_url", ""),
                )
            )
        if proxy_available:
            arms.append(
                ArmConfig(
                    name="w/ TokenPak",
                    platform="proxy",
                    provider=provider,
                    model=model,
                    via_tokenpak=True,
                )
            )
        return arms

    return []


def _count_active_sessions() -> dict[str, int]:
    """Count active Claude/Codex sessions (excluding test subprocesses)."""
    counts: dict[str, int] = {}
    try:
        result = subprocess.run(
            ["pgrep", "-fa", "claude|codex"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            # Skip our own test subprocesses and grep noise
            if "pgrep" in line or "tokenpak/prove" in line:
                continue
            if "claude" in line.lower() and "claude -p" not in line:
                counts["claude"] = counts.get("claude", 0) + 1
            elif "codex" in line.lower():
                counts["codex"] = counts.get("codex", 0) + 1
    except Exception:
        pass
    return counts


def run_test(
    platform: str,
    provider: str,
    model: str,
    test_id: str,
) -> None:
    """Execute the test and display results."""
    from tokenpak.prove.adapter import TurnResult, run_arm

    scenario = _SCENARIOS[test_id]
    arms_cfg = _map_platform_to_adapter(platform, provider, model)

    if not arms_cfg:
        print("  No valid test configuration. Aborting.", file=sys.stderr)
        return

    proof_id = f"prf_{hashlib.sha1(f'{test_id}{time.time()}'.encode()).hexdigest()[:8]}"

    # Build Turn objects
    from tokenpak.prove.scenario import Turn

    turns = [
        Turn(number=i + 1, label=label, prompt=prompt)
        for i, (label, prompt) in enumerate(scenario["turns"])
    ]

    n_turns = len(turns)
    n_arms = len(arms_cfg)
    log_dir = Path.home() / ".tokenpak" / "test" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # ── Clear screen and show header ────────────────────────
    sys.stdout.write("\033[2J\033[H")
    print(f"\n  \033[1mtokenpak test\033[0m — {scenario['name']}\n")
    print(f"  Platform:  {platform}")
    print(f"  Provider:  {provider}")
    print(f"  Model:     {model}")
    print(f"  Turns:     {n_turns}")
    print(f"  Arms:      {n_arms}")
    for i, a in enumerate(arms_cfg):
        print(f"    [{i + 1}] {a.name}")
    print(f"  Proof:     {proof_id}")

    # ── Launch live display (automatic, no user action needed) ──
    display = None
    if n_arms >= 2:
        log_a = log_dir / f"{proof_id}_1.log"
        log_b = log_dir / f"{proof_id}_2.log"
        from tokenpak.prove.display import LiveDisplay

        display = LiveDisplay(log_a, log_b)
        display_msg = display.start()
        if display_msg:
            print(f"\n  {display_msg}")

    print()

    # ── Run arms ────────────────────────────────────────────
    results: list[ArmResult] = []

    for arm_idx, arm_cfg in enumerate(arms_cfg):
        arm_num = arm_idx + 1
        log_path = log_dir / f"{proof_id}_{arm_num}.log"

        print(f"  [{arm_num}/{n_arms}] Running {arm_cfg.name}...")

        def on_turn(turn_num: int, tr: TurnResult) -> None:
            if tr.error:
                print(f"    Turn {turn_num}/{n_turns} ERROR: {tr.error}")
            else:
                cache = f" ({tr.cache_read_tokens:,} cached)" if tr.cache_read_tokens else ""
                print(
                    f"    Turn {turn_num}/{n_turns} done  "
                    f"{tr.input_tokens:,} in{cache}"
                    f" / {tr.output_tokens:,} out"
                    f" / {tr.latency_s:.1f}s"
                    f" / ${tr.cost_usd:.4f}"
                )

        arm_result = run_arm(
            cfg=arm_cfg,
            turns=turns,
            system=scenario["system"],
            max_tokens=4096,
            log_path=log_path,
            on_turn_complete=on_turn,
        )
        results.append(arm_result)

        if arm_result.error and not arm_result.turns:
            print(f"    FAILED: {arm_result.error}")

        if arm_idx < n_arms - 1:
            print()
            time.sleep(2)

    # ── Report ──────────────────────────────────────────────
    report = _format_report(results, scenario["name"], proof_id)
    print(report)

    # ── Save ────────────────────────────────────────────────
    results_dir = Path.home() / ".tokenpak" / "test" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    from tokenpak.prove.reporter import save_result

    result_path = save_result(results, scenario["name"], proof_id, results_dir)
    print(f"  Saved: {result_path}")

    # Log file paths
    for i in range(n_arms):
        lf = log_dir / f"{proof_id}_{i + 1}.log"
        label = arms_cfg[i].name
        print(f"  Log [{label}]: {lf}")

    # Clean up live display (tmux pane auto-closes, terminal stays for review)
    if display and display._method == "tmux-split":
        display.stop()
    elif display and display._method == "terminal":
        print("\n  Live view window still open for review.")

    print()


def _format_report(results: list[ArmResult], scenario_name: str, proof_id: str) -> str:
    """Format comparison report inline."""
    from tokenpak.prove.reporter import format_matrix_report

    return format_matrix_report(results, scenario_name, proof_id)


# ═══════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════


def run(args: object | None = None) -> None:
    """Interactive test command — ``tokenpak test``."""

    # ── Step 1: Detect what's available ─────────────────────
    platforms = _detect_platforms()
    if not platforms:
        print("\n  No platforms available.")
        print("  Set an API key (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.) to get started.\n")
        return

    providers = _detect_providers()
    if not providers:
        print("\n  No providers detected.")
        print("  Set an API key to enable a provider:\n")
        print("    export ANTHROPIC_API_KEY=sk-...")
        print("    export OPENAI_API_KEY=sk-...")
        print("    export GOOGLE_API_KEY=...")
        print()
        return

    # ── Step 2: Platform picker ─────────────────────────────
    platform = _pick("Select platform:", platforms, "Only platforms you have set up are shown.")
    if platform is None:
        _clear_exit()
        return

    # ── Step 3: Provider picker ─────────────────────────────
    # Filter providers by platform compatibility
    if platform == "claude-code":
        filtered = [p for p in providers if p[0] == "anthropic"]
    elif platform == "codex":
        filtered = [p for p in providers if p[0] == "openai"]
    else:
        filtered = providers

    if not filtered:
        _clear_exit()
        print(f"  No compatible providers for platform '{platform}'.")
        return

    if len(filtered) == 1:
        provider = filtered[0][0]
    else:
        selected_provider = _pick(
            "Select provider:",
            filtered,
            "Only providers with API keys detected are shown.",
        )
        if selected_provider is None:
            _clear_exit()
            return
        provider = selected_provider

    # ── Step 4: Model picker ────────────────────────────────
    models = _get_models(provider)
    if not models:
        _clear_exit()
        print(f"  No models configured for provider '{provider}'.")
        return

    model = _pick("Select model:", models, f"Provider: {provider}")
    if model is None:
        _clear_exit()
        return

    # ── Step 5: Test picker ─────────────────────────────────
    test_options = [(tid, info["label"]) for tid, info in _SCENARIOS.items()]
    test_id = _pick(
        "Select test:",
        test_options,
        "Each test runs 5 turns to measure savings across a full session.",
    )
    if test_id is None:
        _clear_exit()
        return

    # ── Step 6: Confirmation ────────────────────────────────
    proxy_status = "running" if _detect_proxy_running() else "not running"
    scenario = _SCENARIOS[test_id]
    arms = _map_platform_to_adapter(platform, provider, model)

    # Detect active sessions that share the rate limit
    active = _count_active_sessions()
    active_warning = ""
    if provider == "anthropic" and active.get("claude", 0) > 0:
        n = active["claude"]
        active_warning = (
            f"\n  \033[33mWarning: {n} active Claude session(s) detected.\033[0m\n"
            f"  They share the same rate limit. The test may get throttled.\n"
            f"  For best results, run the test when no other sessions are active,\n"
            f"  or select a model with higher limits (e.g. haiku).\n"
        )
    elif provider == "openai" and active.get("codex", 0) > 0:
        n = active["codex"]
        active_warning = (
            f"\n  \033[33mWarning: {n} active Codex session(s) detected.\033[0m\n"
            f"  They share the same rate limit. The test may get throttled.\n"
        )

    confirm_options = [
        ("start", "Start test"),
        ("cancel", "Cancel"),
    ]

    # Build confirmation screen
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write("\n  \033[1mtokenpak test\033[0m\n\n")
    sys.stdout.write("  Ready to run:\n\n")
    sys.stdout.write(f"    Test:       {scenario['name']}\n")
    sys.stdout.write(f"    Platform:   {platform}\n")
    sys.stdout.write(f"    Provider:   {provider}\n")
    sys.stdout.write(f"    Model:      {model}\n")
    sys.stdout.write("    Turns:      5\n")
    sys.stdout.write(f"    Proxy:      {proxy_status}\n")
    sys.stdout.write(f"    Arms:       {len(arms)}\n")
    for i, a in enumerate(arms):
        sys.stdout.write(f"      [{i + 1}] {a.name}\n")
    if active_warning:
        sys.stdout.write(active_warning)
    sys.stdout.write("\n")
    sys.stdout.flush()

    confirm = _pick("", confirm_options)
    if confirm != "start":
        _clear_exit()
        return

    # ── Step 7: Run ─────────────────────────────────────────
    run_test(platform, provider, model, test_id)


def _clear_exit() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
