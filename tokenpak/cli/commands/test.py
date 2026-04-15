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

import hashlib
import os
import shutil
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path
from typing import Optional

import httpx


# ═══════════════════════════════════════════════════════════════════════
# Arrow-key interactive picker
# ═══════════════════════════════════════════════════════════════════════


def _getch() -> str:
    """Read a single keypress, returning a named action."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                return {"A": "up", "B": "down"}.get(ch3, "")
        if ch in ("\r", "\n"):
            return "enter"
        if ch in ("q", "\x03"):  # q or Ctrl-C
            return "quit"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _pick(title: str, options: list[tuple[str, str]],
          subtitle: str = "") -> Optional[str]:
    """Arrow-key single-select picker.

    Args:
        title: Heading text.
        options: List of (value, display_label) tuples.
        subtitle: Optional line below title.

    Returns:
        Selected value, or None if user quits.
    """
    if not options:
        return None
    idx = 0
    while True:
        sys.stdout.write("\033[2J\033[H")  # clear screen
        sys.stdout.write(f"\n  \033[1mtokenpak test\033[0m\n\n")
        sys.stdout.write(f"  {title}\n")
        if subtitle:
            sys.stdout.write(f"  \033[2m{subtitle}\033[0m\n")
        sys.stdout.write("\n")
        for i, (_, label) in enumerate(options):
            if i == idx:
                sys.stdout.write(f"  \033[36m> {label}\033[0m\n")
            else:
                sys.stdout.write(f"    {label}\n")
        sys.stdout.write(f"\n  \033[2m[arrows] navigate  [enter] select  [q] quit\033[0m\n")
        sys.stdout.flush()

        key = _getch()
        if key == "up":
            idx = (idx - 1) % len(options)
        elif key == "down":
            idx = (idx + 1) % len(options)
        elif key == "enter":
            return options[idx][0]
        elif key == "quit":
            return None


# ═══════════════════════════════════════════════════════════════════════
# Auto-detection — checks all auth sources, not just env vars
# ═══════════════════════════════════════════════════════════════════════

# Cache so detection runs once per session, not per picker screen
_detection_cache: dict = {}


def _detect_proxy() -> tuple[bool, list[str]]:
    """Check proxy status and which providers it routes.

    Returns (is_running, list_of_provider_names).
    """
    if "proxy" in _detection_cache:
        return _detection_cache["proxy"]

    proxy_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
    try:
        resp = httpx.get(f"{proxy_url}/health", timeout=2.0)
        if resp.status_code != 200:
            _detection_cache["proxy"] = (False, [])
            return False, []
        health = resp.json()
        # circuit_breakers keys are the active providers
        providers = list(health.get("circuit_breakers", {}).keys())
        _detection_cache["proxy"] = (True, providers)
        return True, providers
    except Exception:
        _detection_cache["proxy"] = (False, [])
        return False, []


def _detect_claude_code_auth() -> bool:
    """Check if Claude Code is authenticated."""
    if "claude_auth" in _detection_cache:
        return _detection_cache["claude_auth"]

    creds = Path.home() / ".claude" / ".credentials.json"
    if creds.exists():
        try:
            import json as _json
            data = _json.loads(creds.read_text())
            # Has OAuth or API key config
            ok = bool(data.get("claudeAiOauth") or data.get("apiKey"))
            _detection_cache["claude_auth"] = ok
            return ok
        except Exception:
            pass
    _detection_cache["claude_auth"] = False
    return False


def _detect_codex_auth() -> bool:
    """Check if Codex is authenticated."""
    if "codex_auth" in _detection_cache:
        return _detection_cache["codex_auth"]

    auth_file = Path.home() / ".codex" / "auth.json"
    if auth_file.exists():
        try:
            import json as _json
            data = _json.loads(auth_file.read_text())
            # Has API key or OAuth tokens
            ok = bool(data.get("OPENAI_API_KEY") or data.get("tokens"))
            _detection_cache["codex_auth"] = ok
            return ok
        except Exception:
            pass
    _detection_cache["codex_auth"] = False
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
        _LABELS = {"anthropic": "Anthropic", "openai": "OpenAI",
                    "google": "Google (Gemini)", "xai": "xAI (Grok)"}
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
        ("openai",    "OPENAI_API_KEY"),
        ("google",    "GOOGLE_API_KEY"),
        ("xai",       "XAI_API_KEY"),
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

    _LABELS = {"anthropic": "Anthropic", "openai": "OpenAI",
                "google": "Google (Gemini)", "xai": "xAI (Grok)"}

    return [
        (pid, f"{_LABELS.get(pid, pid)}  ({reason})")
        for pid, reason in found.items()
    ]


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
# ═══════════════════════════════════════════════════════════════════════

_SCENARIOS: dict[str, dict] = {
    "coding": {
        "name": "Coding — Config Parser",
        "label": "Coding  (build a config parser — 10 quick turns)",
        "system": "You are a Python engineer. Keep responses under 150 words. Code only when asked.",
        "turns": [
            ("Design",         "What are the 3 best approaches to parse TOML config files in Python? Just list them with one sentence each."),
            ("Pick",           "Let's go with approach 1. What would the class interface look like? Show just the class signature and method names, no implementation."),
            ("Init",           "Write the __init__ method. Accept a file path, load and parse the TOML. Handle FileNotFoundError."),
            ("Get",            "Write a get(key, default=None) method that supports dotted keys like 'database.host'. Keep it short."),
            ("Set",            "Write a set(key, value) method that also supports dotted keys. Create nested dicts as needed."),
            ("Save",           "Write a save() method that writes the config back to the TOML file atomically using a temp file."),
            ("Validate",       "Add a validate(schema) method that checks required keys exist. Schema is just a list of dotted key strings. Return missing keys."),
            ("Test get",       "Write 3 pytest test functions for the get() method: basic key, dotted key, missing key with default."),
            ("Test set",       "Write 3 pytest test functions for set(): basic key, dotted key creating nested dict, overwrite existing."),
            ("Summary",        "Summarize the full class in a docstring: what it does, all public methods, and a 3-line usage example."),
        ],
    },
    "planning": {
        "name": "Planning — API Design",
        "label": "Planning  (design a REST API — 10 quick turns)",
        "system": "You are a backend architect. Keep responses under 150 words. Be precise.",
        "turns": [
            ("Scope",          "We're building a bookmark manager API. What are the 5 core resources we need? Just list them."),
            ("Bookmark CRUD",  "Define the REST endpoints for the Bookmark resource. Just show method, path, and one-line description."),
            ("Collection",     "Define endpoints for organizing bookmarks into Collections (folders). Same format: method, path, description."),
            ("Tags",           "Define endpoints for a tagging system. Bookmarks can have multiple tags. Method, path, description."),
            ("Search",         "Design the search endpoint. What query parameters should it accept? List them with types."),
            ("Auth",           "How should we handle authentication? Describe the approach in 3 sentences. No code."),
            ("Errors",         "Define our error response format. Show one JSON example for a 404 and one for a 422 validation error."),
            ("Pagination",     "How should list endpoints handle pagination? Show the query params and response envelope format."),
            ("Rate limits",    "What rate limits should we set? Give specific numbers per endpoint category (read, write, search)."),
            ("Summary",        "Write a one-paragraph API overview suitable for the top of the docs. Cover scope, auth, and key design choices."),
        ],
    },
    "codebase": {
        "name": "Codebase — Code Review",
        "label": "Codebase  (review and fix code — 10 quick turns)",
        "system": "You are a code reviewer. Keep responses under 150 words unless showing code.",
        "turns": [
            ("Review",         "Review this function:\n```python\ndef process(data):\n    result = []\n    for item in data:\n        if item['status'] == 'active':\n            result.append({'name': item['name'], 'score': item['score'] * 1.1})\n    return sorted(result, key=lambda x: x['score'], reverse=True)\n```\nList 3 issues."),
            ("Fix types",      "Add type hints to the function. Show the rewritten signature and return type."),
            ("Fix perf",       "Rewrite it as a list comprehension. Is it actually faster? One sentence on why or why not."),
            ("Edge cases",     "What happens if data is empty? If an item is missing the 'score' key? Add defensive handling."),
            ("Naming",         "Suggest better names for the function and its parameter. Explain your reasoning in one sentence each."),
            ("Docstring",      "Write a docstring for the improved function. Include Args, Returns, and Raises sections."),
            ("Test happy",     "Write 2 pytest tests: one with normal input, one verifying the sort order."),
            ("Test edge",      "Write 2 pytest tests: empty list input, and an item missing the 'score' key."),
            ("Extract",        "Should we extract the scoring logic (score * 1.1) into its own function? Answer in 2 sentences."),
            ("Final",          "Show the final version of the function with all improvements applied. No explanation, just code."),
        ],
    },
}


# ═══════════════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════════════


def _map_platform_to_adapter(platform: str, provider: str, model: str):
    """Map user-facing platform to adapter ArmConfigs.

    Each platform uses its native execution method:
      - claude-code: `claude -p` vs `tokenpak claude -p` (CLI subprocess,
        uses Claude Code's own OAuth billing — no API rate limit conflict)
      - codex: `codex exec` vs `tokenpak codex exec`
      - api/proxy: direct HTTP vs proxy HTTP (raw API key route)
    """
    from tokenpak.prove.adapter import ArmConfig, _resolve_api_key, _get_provider

    proxy_available = _detect_proxy_running()

    if platform == "claude-code":
        # Claude Code uses its own OAuth billing (not raw API rate limits).
        # The comparison: native claude vs claude with tokenpak companion.
        arms = [
            ArmConfig(name="Claude Code", platform="cli",
                      provider="anthropic", model=model,
                      cli_command="claude -p"),
            ArmConfig(name="w/ TokenPak", platform="cli",
                      provider="anthropic", model=model,
                      cli_command="tokenpak claude -p",
                      via_tokenpak=True),
        ]
        return arms

    elif platform == "codex":
        arms = [
            ArmConfig(name="Codex", platform="cli",
                      provider="openai", model=model,
                      cli_command="codex exec"),
            ArmConfig(name="w/ TokenPak", platform="cli",
                      provider="openai", model=model,
                      cli_command="tokenpak codex exec",
                      via_tokenpak=True),
        ]
        return arms

    elif platform in ("api", "proxy"):
        # API route: direct HTTP vs through proxy
        arms = []
        reg = _get_provider(provider)
        key_env = reg.get("api_key_env", "")
        has_key = bool(_resolve_api_key(provider, key_env))

        if has_key:
            arms.append(ArmConfig(
                name="Direct", platform="api",
                provider=provider, model=model,
                base_url=reg.get("base_url", ""),
            ))
        if proxy_available:
            arms.append(ArmConfig(
                name="w/ TokenPak", platform="proxy",
                provider=provider, model=model, via_tokenpak=True,
            ))
        return arms

    return []


def _count_active_sessions() -> dict[str, int]:
    """Count active Claude/Codex sessions (excluding test subprocesses)."""
    counts: dict[str, int] = {}
    try:
        result = subprocess.run(
            ["pgrep", "-fa", "claude|codex"],
            capture_output=True, text=True, timeout=5,
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
    from tokenpak.prove.adapter import ArmConfig, ArmResult, TurnResult, run_arm

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
        print(f"    [{i+1}] {a.name}")
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
        lf = log_dir / f"{proof_id}_{i+1}.log"
        label = arms_cfg[i].name
        print(f"  Log [{label}]: {lf}")

    # Clean up live display (tmux pane auto-closes, terminal stays for review)
    if display and display._method == "tmux-split":
        display.stop()
    elif display and display._method == "terminal":
        print(f"\n  Live view window still open for review.")

    print()


def _format_report(results: list, scenario_name: str, proof_id: str) -> str:
    """Format comparison report inline."""
    from tokenpak.prove.reporter import format_matrix_report
    return format_matrix_report(results, scenario_name, proof_id)


# ═══════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════


def run(args=None) -> None:
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
    platform = _pick("Select platform:", platforms,
                      "Only platforms you have set up are shown.")
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
        provider = _pick("Select provider:", filtered,
                          "Only providers with API keys detected are shown.")
        if provider is None:
            _clear_exit()
            return

    # ── Step 4: Model picker ────────────────────────────────
    models = _get_models(provider)
    if not models:
        _clear_exit()
        print(f"  No models configured for provider '{provider}'.")
        return

    model = _pick("Select model:", models,
                   f"Provider: {provider}")
    if model is None:
        _clear_exit()
        return

    # ── Step 5: Test picker ─────────────────────────────────
    test_options = [
        (tid, info["label"]) for tid, info in _SCENARIOS.items()
    ]
    test_id = _pick("Select test:", test_options,
                     "Each test runs 5 turns to measure savings across a full session.")
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
    sys.stdout.write(f"\n  \033[1mtokenpak test\033[0m\n\n")
    sys.stdout.write(f"  Ready to run:\n\n")
    sys.stdout.write(f"    Test:       {scenario['name']}\n")
    sys.stdout.write(f"    Platform:   {platform}\n")
    sys.stdout.write(f"    Provider:   {provider}\n")
    sys.stdout.write(f"    Model:      {model}\n")
    sys.stdout.write(f"    Turns:      5\n")
    sys.stdout.write(f"    Proxy:      {proxy_status}\n")
    sys.stdout.write(f"    Arms:       {len(arms)}\n")
    for i, a in enumerate(arms):
        sys.stdout.write(f"      [{i+1}] {a.name}\n")
    if active_warning:
        sys.stdout.write(active_warning)
    sys.stdout.write(f"\n")
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
