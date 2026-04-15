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
# Built-in 5-turn test scenarios
# ═══════════════════════════════════════════════════════════════════════

_SCENARIOS: dict[str, dict] = {
    "coding": {
        "name": "Coding — Build a Utility Library",
        "label": "Coding  (write a Python utility library — 5 turns)",
        "system": "You are a senior Python engineer. Write clean, typed, tested code.",
        "turns": [
            ("Design", (
                "Design a Python `RateLimiter` class using the token bucket algorithm.\n"
                "Requirements:\n"
                "- Per-key rate limiting (e.g., per user ID)\n"
                "- Configurable rate (tokens/sec) and burst size\n"
                "- Thread-safe operation\n"
                "- Clean public API with type hints\n\n"
                "Show the class interface, explain your design choices, and describe "
                "the algorithm. Include usage examples."
            )),
            ("Implement", (
                "Now write the full implementation of the RateLimiter class you designed.\n"
                "Include:\n"
                "- Complete `__init__`, `acquire`, `try_acquire`, and `reset` methods\n"
                "- Thread safety with `threading.Lock`\n"
                "- Proper token refill calculation based on elapsed time\n"
                "- Docstrings for all public methods"
            )),
            ("Error handling", (
                "Add robust error handling and edge cases to the RateLimiter:\n"
                "- Custom `RateLimitExceeded` exception with retry-after info\n"
                "- `wait_for_token()` async method that blocks until a token is available\n"
                "- Graceful handling of clock skew / time going backwards\n"
                "- Input validation on rate, burst, and key parameters\n"
                "- A decorator `@rate_limit(rate, burst)` for easy function wrapping"
            )),
            ("Testing", (
                "Write comprehensive pytest tests for the RateLimiter:\n"
                "- Test basic acquire/deny cycle\n"
                "- Test burst handling (burst allows N immediate tokens)\n"
                "- Test per-key isolation (different keys don't interfere)\n"
                "- Test token refill over time (mock time.monotonic)\n"
                "- Test thread safety with concurrent access\n"
                "- Test the decorator\n"
                "- Test edge cases: zero rate, very large burst, negative values\n"
                "- At least 15 test functions"
            )),
            ("Optimize", (
                "Review the RateLimiter implementation for performance:\n"
                "- Profile the hot path (acquire/try_acquire) and identify bottlenecks\n"
                "- Reduce lock contention for the per-key case\n"
                "- Add a cleanup mechanism for expired keys (memory leak prevention)\n"
                "- Add metrics: total_acquired, total_denied, avg_wait_time\n"
                "- Write a benchmark comparing throughput with 1, 10, 100 concurrent keys"
            )),
        ],
    },
    "planning": {
        "name": "Planning — System Architecture",
        "label": "Planning  (design a system architecture — 5 turns)",
        "system": "You are a senior systems architect. Be thorough and precise.",
        "turns": [
            ("Requirements", (
                "Analyze the requirements for a real-time notification system that:\n"
                "- Delivers notifications to 1M+ daily active users\n"
                "- Supports push (mobile), email, in-app, and SMS channels\n"
                "- Allows user-configurable preferences per channel\n"
                "- Handles rate limiting per user and per channel\n"
                "- Supports templated messages with i18n\n"
                "- Provides delivery tracking and retry logic\n\n"
                "Identify functional requirements, non-functional requirements, "
                "and key constraints."
            )),
            ("Architecture", (
                "Design the high-level system architecture:\n"
                "- Component diagram showing all services and their interactions\n"
                "- Message flow from trigger to delivery for each channel\n"
                "- Queue topology (what gets queued where, fan-out strategy)\n"
                "- Storage design (what DB for preferences, templates, delivery log)\n"
                "- Explain your technology choices and trade-offs"
            )),
            ("Data models", (
                "Define the data models in detail:\n"
                "- User preferences schema (channels, quiet hours, frequency caps)\n"
                "- Notification template schema (with i18n support)\n"
                "- Delivery event schema (tracking status, retries, timestamps)\n"
                "- Rate limit state schema\n"
                "- Show the schemas as Python dataclasses or SQL CREATE TABLE statements"
            )),
            ("API design", (
                "Design the API surface:\n"
                "- REST endpoints for notification management (send, batch, status)\n"
                "- REST endpoints for user preferences (get, update)\n"
                "- Webhook endpoint for delivery status callbacks\n"
                "- Internal gRPC service definitions for inter-service communication\n"
                "- Show request/response examples for each endpoint"
            )),
            ("Deployment", (
                "Design the deployment and operations strategy:\n"
                "- Kubernetes deployment architecture (replicas, HPA, resource limits)\n"
                "- Observability: metrics to track, alerts to set, dashboards to build\n"
                "- Failure modes and mitigation (what happens when each component fails)\n"
                "- Capacity planning: calculate resources for 1M DAU target\n"
                "- Rollout strategy for the initial launch"
            )),
        ],
    },
    "codebase": {
        "name": "Large Codebase — Deep File Analysis",
        "label": "Codebase  (analyze and refactor large code — 5 turns)",
        "system": "You are a senior engineer doing a code review and refactor.",
        "turns": [
            ("Analyze", (
                "You're reviewing a legacy Python web application. Here's the main "
                "request handler module (simulated). Analyze this code:\n\n"
                "```python\n"
                "import json, os, re, hashlib, hmac, time, logging, sqlite3\n"
                "from http.server import BaseHTTPRequestHandler\n"
                "from urllib.parse import urlparse, parse_qs\n"
                "from datetime import datetime, timedelta\n\n"
                "logger = logging.getLogger(__name__)\n"
                "DB_PATH = os.environ.get('DB_PATH', '/tmp/app.db')\n"
                "SECRET = os.environ.get('APP_SECRET', 'changeme')\n"
                "RATE_LIMIT = int(os.environ.get('RATE_LIMIT', '100'))\n"
                "rate_limits = {}  # global mutable state\n"
                "sessions = {}    # global mutable state\n\n"
                "class RequestHandler(BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        path = urlparse(self.path).path\n"
                "        params = parse_qs(urlparse(self.path).query)\n"
                "        if path == '/api/users':\n"
                "            conn = sqlite3.connect(DB_PATH)\n"
                "            users = conn.execute('SELECT * FROM users WHERE active=1 '\n"
                "                + ('AND role=' + params['role'][0] if 'role' in params else '')\n"
                "            ).fetchall()\n"
                "            conn.close()\n"
                "            self.send_response(200)\n"
                "            self.send_header('Content-Type', 'application/json')\n"
                "            self.end_headers()\n"
                "            self.wfile.write(json.dumps(users).encode())\n"
                "        elif path == '/api/sessions':\n"
                "            token = self.headers.get('Authorization', '').replace('Bearer ', '')\n"
                "            if token in sessions:\n"
                "                self.send_response(200)\n"
                "                self.wfile.write(json.dumps(sessions[token]).encode())\n"
                "            else:\n"
                "                self.send_response(401)\n"
                "                self.wfile.write(b'unauthorized')\n"
                "    def do_POST(self):\n"
                "        length = int(self.headers.get('Content-Length', 0))\n"
                "        body = json.loads(self.rfile.read(length))\n"
                "        path = urlparse(self.path).path\n"
                "        if path == '/api/login':\n"
                "            conn = sqlite3.connect(DB_PATH)\n"
                "            user = conn.execute(\n"
                "                f\"SELECT * FROM users WHERE email='{body['email']}' \"\n"
                "                f\"AND password='{hashlib.md5(body['password'].encode()).hexdigest()}'\"\n"
                "            ).fetchone()\n"
                "            conn.close()\n"
                "            if user:\n"
                "                token = hashlib.sha256(os.urandom(32)).hexdigest()\n"
                "                sessions[token] = {'user_id': user[0], 'created': time.time()}\n"
                "                self.send_response(200)\n"
                "                self.wfile.write(json.dumps({'token': token}).encode())\n"
                "            else:\n"
                "                self.send_response(401)\n"
                "```\n\n"
                "Identify all security vulnerabilities, code smells, and architectural "
                "problems. Categorize by severity (critical, high, medium, low)."
            )),
            ("Security fixes", (
                "Fix all the critical and high severity security issues you identified:\n"
                "- SQL injection in the users query and login query\n"
                "- MD5 password hashing (replace with bcrypt/argon2)\n"
                "- Hardcoded default secret\n"
                "- Missing rate limiting on login\n"
                "- Missing CSRF/CORS protections\n"
                "- Session fixation vulnerabilities\n\n"
                "Show the refactored code with all security fixes applied. "
                "Explain each fix."
            )),
            ("Architecture refactor", (
                "Refactor the handler from a monolithic class into a clean architecture:\n"
                "- Separate routing from business logic\n"
                "- Extract a proper database layer (connection pooling, parameterized queries)\n"
                "- Extract an authentication middleware\n"
                "- Extract a rate limiting middleware\n"
                "- Add proper error handling with consistent error responses\n"
                "- Add request validation\n\n"
                "Show the refactored module structure and all new files."
            )),
            ("Add tests", (
                "Write tests for the refactored code:\n"
                "- Unit tests for the auth module (login, token validation, session expiry)\n"
                "- Unit tests for the rate limiter\n"
                "- Integration tests for the API endpoints\n"
                "- Security regression tests (verify SQL injection is fixed, etc.)\n"
                "- Use pytest fixtures, parametrize where appropriate\n"
                "- At least 15 test functions"
            )),
            ("Documentation", (
                "Write developer documentation for the refactored system:\n"
                "- Architecture overview (module structure, data flow)\n"
                "- API reference (all endpoints with request/response examples)\n"
                "- Security model (how auth works, session management, rate limiting)\n"
                "- Deployment guide (env vars, database setup, production checklist)\n"
                "- Migration guide (how to upgrade from the old handler)"
            )),
        ],
    },
}


# ═══════════════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════════════


def _map_platform_to_adapter(platform: str, provider: str, model: str):
    """Map user-facing platform to adapter ArmConfigs (direct + tokenpak)."""
    from tokenpak.prove.adapter import ArmConfig

    proxy_available = _detect_proxy_running()

    if platform == "api":
        arms = [
            ArmConfig(name="Direct API", platform="api",
                      provider=provider, model=model),
        ]
        if proxy_available:
            arms.append(ArmConfig(
                name="TokenPak Proxy", platform="proxy",
                provider=provider, model=model, via_tokenpak=True,
            ))
        return arms

    elif platform == "proxy":
        # Proxy-only: user selected proxy platform (may not have direct key)
        arms = [
            ArmConfig(name="TokenPak Proxy", platform="proxy",
                      provider=provider, model=model, via_tokenpak=True),
        ]
        # If they also have the direct API key, add a baseline arm
        from tokenpak.prove.adapter import _get_provider
        reg = _get_provider(provider)
        key_env = reg.get("api_key_env", "")
        if key_env and os.environ.get(key_env):
            arms.insert(0, ArmConfig(
                name="Direct API", platform="api",
                provider=provider, model=model,
            ))
        return arms

    elif platform == "claude-code":
        arms = [
            ArmConfig(name="Claude Code", platform="cli",
                      provider="anthropic", model=model,
                      cli_command="claude -p"),
        ]
        if shutil.which("tokenpak"):
            arms.append(ArmConfig(
                name="Claude Code + TokenPak", platform="cli",
                provider="anthropic", model=model,
                cli_command="tokenpak claude -p",
                via_tokenpak=True,
            ))
        return arms

    elif platform == "codex":
        arms = [
            ArmConfig(name="Codex", platform="cli",
                      provider="openai", model=model,
                      cli_command="codex exec"),
        ]
        if shutil.which("tokenpak"):
            arms.append(ArmConfig(
                name="Codex + TokenPak", platform="cli",
                provider="openai", model=model,
                cli_command="tokenpak codex exec",  # future
                via_tokenpak=True,
            ))
        return arms

    return []


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

    # ── Launch live display ─────────────────────────────────
    if n_arms >= 2:
        log_a = log_dir / f"{proof_id}_1.log"
        log_b = log_dir / f"{proof_id}_2.log"
        from tokenpak.prove.display import LiveDisplay
        display = LiveDisplay(log_a, log_b)
        attach_info = display.start()
        print(f"\n  Live view: {attach_info}")
    else:
        display = None

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

    if display and display._method == "tmux":
        print(f"\n  Live windows still open for review.")
        print(f"  Close with: tmux kill-session -t tokenpak-prove")
    elif display and display._method == "terminal":
        print(f"\n  Live windows still open for review.")

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

    confirm_options = [
        ("start", "Start test"),
        ("cancel", "Cancel"),
    ]

    # Build confirmation screen manually
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
