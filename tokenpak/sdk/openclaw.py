"""tokenpak.sdk.openclaw — OpenClaw gateway adapter.

Supports two execution backends:

  **api** (default): OpenClaw → tokenpak proxy → Anthropic API
      Standard HTTP forwarding with full pipeline (compression, caching, dedup).

  **claude_code**: OpenClaw → tokenpak proxy → claude -p --resume
      Routes through Claude Code for tool use, CLAUDE.md context, subscription
      billing, and persistent multi-turn sessions via --resume.

The backend is selected by the ``X-TokenPak-Backend: claude-code`` header
on the incoming request. OpenClaw configures this per-provider in its
config (e.g. ``tokenpak-claude-code`` provider vs ``tokenpak-anthropic``).

Session mapping:
  Each OpenClaw session ID (from ``X-OpenClaw-Session`` header or message
  metadata) maps to a Claude Code session UUID. The mapping persists in
  ``~/.tokenpak/openclaw_sessions.json`` so conversations survive restarts.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from tokenpak.sdk.base import TokenPakAdapter

_SESSION_MAP_PATH = Path.home() / ".tokenpak" / "openclaw_sessions.json"


def _find_claude_binary() -> Optional[str]:
    """Locate the Claude Code CLI.

    The tokenpak proxy runs under systemd user units whose PATH is often
    ``/usr/bin:/bin`` — it does NOT include npm/pip-user install dirs.
    Mirrors the discovery walk in
    ``~/vault/06_RUNTIME/scripts/agent-claude-worker.sh`` so fleet hosts
    stay consistent without per-host env tweaks.

    Override: ``TOKENPAK_CLAUDE_BIN`` env var (absolute path) wins.
    """
    override = os.environ.get("TOKENPAK_CLAUDE_BIN", "").strip()
    if override and Path(override).is_file() and os.access(override, os.X_OK):
        return override

    # Fleet-standard candidate dirs, in precedence order. Matches
    # agent-claude-worker.sh's discover_bin() walk.
    import shutil as _shutil
    home = Path.home()
    candidates = [
        home / ".npm-global" / "bin" / "claude",
        home / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/usr/bin/claude"),
        home / "bin" / "claude",
    ]
    for p in candidates:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)

    # Last resort: PATH lookup (honors whatever env systemd gave us)
    found = _shutil.which("claude")
    if found:
        return found
    return None
_SESSION_MAP: Optional[dict] = None


def _load_session_map() -> dict:
    global _SESSION_MAP
    if _SESSION_MAP is not None:
        return _SESSION_MAP
    if _SESSION_MAP_PATH.exists():
        try:
            _SESSION_MAP = json.loads(_SESSION_MAP_PATH.read_text())
        except Exception:
            _SESSION_MAP = {}
    else:
        _SESSION_MAP = {}
    return _SESSION_MAP


def _save_session_map() -> None:
    if _SESSION_MAP is None:
        return
    _SESSION_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SESSION_MAP_PATH.write_text(json.dumps(_SESSION_MAP, indent=2))


def _get_claude_session(openclaw_session: str) -> tuple[str, bool]:
    """Map an OpenClaw session ID to a Claude Code session UUID.

    Returns (claude_session_uuid, is_new).
    """
    smap = _load_session_map()
    if openclaw_session in smap:
        return smap[openclaw_session], False
    # New session
    claude_id = str(uuid.uuid4())
    smap[openclaw_session] = claude_id
    _save_session_map()
    return claude_id, True


def execute_via_claude_code(
    openclaw_session: str,
    messages: list[dict[str, Any]],
    model: str = "claude-sonnet-4-6",
    system: str = "",
    max_tokens: int = 4096,
    workspace: str = "",
) -> dict[str, Any]:
    """Execute a request through Claude Code via ``tokenpak claude -p --resume``.

    This is called by the proxy when ``X-TokenPak-Backend: claude-code`` is set.

    Args:
        openclaw_session: OpenClaw's session/conversation ID.
        messages: Anthropic-format messages array.
        model: Model to use.
        system: System prompt (if any).
        max_tokens: Max output tokens.

    Returns:
        Anthropic-format response dict with usage metrics.
    """
    claude_session, is_new = _get_claude_session(openclaw_session)

    # Extract the latest user message (Claude Code maintains history via --resume)
    latest_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):
                latest_msg = " ".join(
                    b.get("text", "") for b in content if b.get("type") == "text"
                )
            else:
                latest_msg = str(content)
            break

    if not latest_msg:
        return _error_response("No user message found in request")

    # Build the command — use `claude` directly (not `tokenpak claude`)
    # to avoid the companion launcher re-setting ANTHROPIC_BASE_URL to the proxy.
    claude_bin = _find_claude_binary()
    if claude_bin is None:
        return _error_response(
            "claude binary not found — install Claude Code CLI or set "
            "TOKENPAK_CLAUDE_BIN to its absolute path"
        )
    cmd = [claude_bin]
    cmd.extend(["--model", model])

    if is_new:
        cmd.extend(["--session-id", claude_session])
    else:
        cmd.extend(["--resume", claude_session])

    cmd.extend(["--output-format", "json"])
    cmd.append("-p")
    # Message goes via stdin — avoids OS arg size limit (~128KB)

    # Execute — use a clean env that points directly at Anthropic API,
    # NOT back through the proxy (which would create a request loop).
    _env = os.environ.copy()
    _env.pop("ANTHROPIC_BASE_URL", None)  # remove proxy redirect
    _env["DISABLE_PROMPT_CACHING"] = "1"  # avoid cache overhead for short msgs
    # Bare mode: strip Claude Code native context (CLAUDE.md, auto memory,
    # prompt history, permissions) — OpenClaw injects its own.
    _env["TOKENPAK_COMPANION_BARE"] = "1"

    # Working directory: use explicit workspace, or resolve from OpenClaw default
    _cwd = workspace or os.environ.get(
        "OPENCLAW_WORKSPACE",
        str(Path.home() / ".openclaw" / "workspace"),
    )
    if not Path(_cwd).is_dir():
        _cwd = str(Path.home())  # fallback to home if workspace doesn't exist

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            input=latest_msg,
            capture_output=True,
            text=True,
            timeout=300,
            env=_env,
            cwd=_cwd,
        )
    except subprocess.TimeoutExpired:
        return _error_response("Claude Code session timed out (300s)")
    except FileNotFoundError:
        return _error_response("tokenpak or claude command not found")

    elapsed = time.monotonic() - t0

    if proc.returncode != 0 and not proc.stdout.strip():
        error_msg = proc.stderr.strip()[:300] or f"Exit code {proc.returncode}"
        return _error_response(error_msg)

    # Parse Claude Code JSON output
    output = proc.stdout.strip()
    if output.startswith("{"):
        try:
            data = json.loads(output)
            # Convert Claude Code JSON to Anthropic API response format
            return _format_anthropic_response(data, model, elapsed)
        except json.JSONDecodeError:
            pass

    # Plain text fallback
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": output}],
        "model": model,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": len(latest_msg) // 4,
            "output_tokens": len(output) // 4,
        },
    }


def _format_anthropic_response(data: dict, model: str, elapsed: float) -> dict:
    """Convert Claude Code --output-format json to Anthropic API format."""
    result_text = data.get("result", "")
    usage = data.get("usage", {}) or {}
    cost = data.get("cost_usd", 0)

    # If the JSON already has Anthropic-format fields, pass through
    if data.get("type") == "message":
        return data

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": result_text}],
        "model": model,
        "stop_reason": data.get("stop_reason", "end_turn"),
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        },
    }


def _error_response(message: str) -> dict:
    """Build an Anthropic-format error response."""
    return {
        "type": "error",
        "error": {
            "type": "api_error",
            "message": message,
        },
    }


class OpenClawAdapter(TokenPakAdapter):
    """Adapter for OpenClaw gateway environments.

    Supports ``backend="api"`` (default HTTP forwarding) and
    ``backend="claude_code"`` (route through Claude Code CLI).
    """

    provider_name = "openclaw"

    def __init__(self, base_url: str = "", api_key: str = "openclaw") -> None:
        url = base_url or os.environ.get("OPENCLAW_GATEWAY_URL", "http://localhost:18789")
        super().__init__(base_url=url, api_key=api_key)

    def prepare_request(self, request: dict) -> dict:
        return request

    def parse_response(self, response: dict) -> dict:
        return response

    def extract_tokens(self, response: dict) -> dict:
        usage = response.get("usage", {})
        return {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read": usage.get("cache_read_input_tokens", 0),
            "cache_write": usage.get("cache_creation_input_tokens", 0),
            "total": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        }

    def send(self, prepared_request: dict) -> dict:
        """Send via HTTP (standard path). For claude_code backend,
        the proxy calls execute_via_claude_code() directly."""
        import httpx
        headers = {"content-type": "application/json"}
        resp = httpx.post(
            f"{self.base_url}/v1/messages",
            json=prepared_request,
            headers=headers,
            timeout=120.0,
        )
        return resp.json()


# ═══════════════════════════════════════════════════════════════════════
# Setup — configure openclaw.json to route through tokenpak
# ═══════════════════════════════════════════════════════════════════════


def discover_openclaw_configs() -> list[Path]:
    """Find every openclaw.json on this host.

    Precedence (highest first — each layer short-circuits if it produces
    at least one valid path):

      1. ``OPENCLAW_CONFIG_PATH`` env var (what systemd units set per
         instance — e.g. ``/home/sue/.openclaw-governor/openclaw.json``
         for the governor). Honored as a single-target override.
      2. Glob ``$HOME/.openclaw*/openclaw.json`` — picks up ``main``,
         ``governor``, and any future siblings without code changes.
      3. Legacy singleton ``$HOME/.openclaw/openclaw.json`` — safety net
         in case neither env var nor glob matched.

    Returns an empty list when nothing exists (caller handles).
    """
    env_path = os.environ.get("OPENCLAW_CONFIG_PATH")
    if env_path:
        p = Path(env_path).expanduser()
        if p.is_file():
            return [p]
        # Env var set but file missing — surface nothing, let caller report.
        return []

    home = Path.home()
    hits: list[Path] = []
    seen: set[Path] = set()
    for p in sorted(home.glob(".openclaw*/openclaw.json")):
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        hits.append(p)

    if hits:
        return hits

    legacy = home / ".openclaw" / "openclaw.json"
    return [legacy] if legacy.is_file() else []


def _fetch_models_from_proxy(
    proxy_url: str,
    provider: str,
) -> list[dict] | None:
    """Query /tpk/v1/models?provider=<provider> for the living model list.

    Returns a list of openclaw-shaped dicts ({id, name, cost}) or None
    when the proxy isn't reachable (caller falls back to the static
    template list).
    """
    try:
        import urllib.request
        url = proxy_url.rstrip("/") + f"/tpk/v1/models?provider={provider}"
        with urllib.request.urlopen(url, timeout=3.0) as resp:
            if resp.status != 200:
                return None
            import json as _json
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    out: list[dict] = []
    for m in data.get("models", []):
        mid = m.get("id") or ""
        if not mid:
            continue
        # Display name: humanize "claude-opus-4-7" -> "Opus 4.7" for anthropic
        display = _humanize_model_name(mid, provider)
        out.append({
            "id": mid,
            "name": display,
            "cost": {
                "input": float(m.get("input_per_mtok", 0) or 0),
                "output": float(m.get("output_per_mtok", 0) or 0),
                "cacheRead": float(m.get("cache_read_per_mtok", 0) or 0),
                "cacheWrite": float(m.get("cache_write_per_mtok", 0) or 0),
            },
        })
    # Reverse-alpha so the newest naming (opus-4-7) floats above older (opus-4-6)
    out.sort(key=lambda m: m["id"], reverse=True)
    return out


def _humanize_model_name(model_id: str, provider: str) -> str:
    """Convert 'claude-opus-4-7' → 'Opus 4.7', 'gemini-2.5-pro' → 'Gemini 2.5 Pro'."""
    if provider == "anthropic":
        # claude-opus-4-7 -> ["claude", "opus", "4", "7"] -> "Opus 4.7"
        parts = model_id.replace("claude-", "").split("-")
        if len(parts) >= 3 and parts[0] in ("opus", "sonnet", "haiku"):
            tier = parts[0].title()
            ver_parts = parts[1:]
            # Handle dated versions like "4-5-20251022"
            if len(ver_parts) > 2 and ver_parts[-1].isdigit() and len(ver_parts[-1]) == 8:
                ver_parts = ver_parts[:-1]
            return f"{tier} {'.'.join(ver_parts)}"
    if provider == "google":
        # gemini-2.5-pro -> "Gemini 2.5 Pro"
        return " ".join(p.title() if not p[0:1].isdigit() else p for p in model_id.split("-"))
    return model_id


# Provider templates — static fallback when the proxy REST API is
# unreachable. Dynamic path (_fetch_models_from_proxy) is preferred so
# models Anthropic ships after this code was written (opus-4-7,
# sonnet-4-7, etc.) auto-appear without a tokenpak release.
_PROVIDER_TEMPLATES: dict[str, dict] = {
    "tokenpak-anthropic": {
        "baseUrl": "http://localhost:8766",
        "api": "anthropic-messages",
        "models": [
            {"id": "claude-opus-4-6", "name": "Opus 4.6",
             "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}},
            {"id": "claude-sonnet-4-6", "name": "Sonnet 4.6",
             "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}},
            {"id": "claude-haiku-4-5", "name": "Haiku 4.5",
             "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}},
        ],
    },
    # Note: tokenpak-openai-codex is NOT templated here — it uses
    # openai-codex-responses format with custom model IDs (gpt-5.x-codex)
    # that the user configures. We only update its baseUrl, never inject
    # models because chat-completions models break codex-responses format.
    "tokenpak-gemini": {
        "baseUrl": "http://localhost:8766",
        "api": "google-generative-ai",
        "models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash",
             "cost": {"input": 0, "output": 0}},
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro",
             "cost": {"input": 0, "output": 0}},
        ],
    },
    # tokenpak-claude-code is NOT in this static template — its models
    # are synced dynamically from the anthropic provider at setup time.
    # See _build_claude_code_provider() below.
}


def detect_openclaw() -> bool:
    """True when at least one openclaw.json is present on this host."""
    return bool(discover_openclaw_configs())


def _build_claude_code_provider(
    providers: dict, proxy_url: str, result: dict,
) -> None:
    """Build tokenpak-claude-code by syncing models from anthropic provider.

    Copies all models from tokenpak-anthropic (or anthropic), appends
    "(Claude Code)" to display names, and sets the X-TokenPak-Backend header.
    Automatically picks up new models when the anthropic provider is updated.
    """
    name = "tokenpak-claude-code"
    source = providers.get("tokenpak-anthropic") or providers.get("anthropic") or {}
    source_models = source.get("models", [])

    if not source_models:
        return

    models = []
    for m in source_models:
        cc = dict(m)
        orig_name = cc.get("name", cc["id"])
        if "(Claude Code)" not in orig_name:
            cc["name"] = f"{orig_name} (Claude Code)"
        cc["cost"] = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
        models.append(cc)

    existing = providers.get(name, {})
    existing_ids = {m["id"] for m in existing.get("models", [])}
    want_ids = {m["id"] for m in models}

    if (existing.get("baseUrl") == proxy_url
            and existing.get("headers", {}).get("X-TokenPak-Backend") == "claude-code"
            and existing_ids == want_ids):
        return  # already up to date

    providers[name] = {
        "baseUrl": proxy_url,
        "api": "anthropic-messages",
        "headers": {"X-TokenPak-Backend": "claude-code"},
        "models": models,
    }

    if name in existing_ids:
        result["providers_updated"].append(name)
    else:
        result["providers_added"].append(name)


def _setup_single_openclaw(
    config_path: Path, proxy_url: str,
) -> dict[str, Any]:
    """Configure a single openclaw.json. Returns a result dict.

    Factored out of setup_openclaw() so the multi-config orchestrator
    can iterate every discovered install.
    """
    result: dict[str, Any] = {
        "path": str(config_path),
        "providers_added": [],
        "providers_updated": [],
        "claude_code_backend": False,
    }

    if not config_path.exists():
        return {**result, "error": f"OpenClaw config not found at {config_path}"}

    config = json.loads(config_path.read_text())

    # Ensure models.providers exists
    if "models" not in config:
        config["models"] = {"mode": "merge", "providers": {}}
    if "providers" not in config["models"]:
        config["models"]["providers"] = {}

    providers = config["models"]["providers"]

    # Per-provider which "family" to query from /tpk/v1/models
    _PROVIDER_FAMILY = {
        "tokenpak-anthropic": "anthropic",
        "tokenpak-gemini": "google",
    }

    # Add/update tokenpak providers. Prefer the proxy's live model list
    # over the static template so newly-released models (opus-4-7, etc.)
    # flow into the OpenClaw selector automatically.
    for name, template in _PROVIDER_TEMPLATES.items():
        live_models = None
        family = _PROVIDER_FAMILY.get(name)
        if family:
            live_models = _fetch_models_from_proxy(proxy_url, family)

        models_to_use = live_models if live_models else template["models"]

        if name in providers:
            # Existing provider — refresh baseUrl + sync models
            providers[name]["baseUrl"] = proxy_url
            existing_ids = {m.get("id") for m in providers[name].get("models", [])}
            new_ids = {m["id"] for m in models_to_use}
            if existing_ids != new_ids:
                providers[name]["models"] = models_to_use
            result["providers_updated"].append(name)
        else:
            # New provider — add with live (or template fallback) models
            provider_data = dict(template)
            provider_data["baseUrl"] = proxy_url
            provider_data["models"] = models_to_use
            providers[name] = provider_data
            result["providers_added"].append(name)

    result["models_source"] = "live-proxy-registry" if live_models is not None else "static-template"

    # Also update baseUrl for any tokenpak-* providers NOT in templates
    # (user-created ones like tokenpak-ollama-redpc)
    for name in list(providers.keys()):
        if name.startswith("tokenpak-") and name not in _PROVIDER_TEMPLATES:
            if providers[name].get("baseUrl", "").startswith("http://localhost"):
                providers[name]["baseUrl"] = proxy_url

    # Build tokenpak-claude-code dynamically from anthropic models
    _build_claude_code_provider(providers, proxy_url, result)

    # Ensure auth profiles exist for tokenpak providers
    if "auth" not in config:
        config["auth"] = {"profiles": {}, "order": {}}
    auth = config["auth"]
    if "profiles" not in auth:
        auth["profiles"] = {}
    if "order" not in auth:
        auth["order"] = {}

    # Auth profile set = every tokenpak-* provider we just
    # templated + the dynamically-built tokenpak-claude-code (when
    # present). Keeping the list derived from `providers` avoids a
    # hardcoded enum and honors the always-build-dynamic rule.
    auth_targets = [n for n in _PROVIDER_TEMPLATES]
    if "tokenpak-claude-code" in providers:
        auth_targets.append("tokenpak-claude-code")

    for name in auth_targets:
        profile_key = f"{name}:manual"
        if profile_key not in auth["profiles"]:
            auth["profiles"][profile_key] = {
                "provider": name,
                "mode": "oauth",
            }
        if name not in auth.get("order", {}):
            auth["order"][name] = [profile_key]

    # Check if claude-code backend is configured
    if "tokenpak-claude-code" in providers:
        result["claude_code_backend"] = True

    # Atomic write
    tmp = config_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, indent=2))
    os.replace(tmp, config_path)

    return result


def setup_openclaw(
    proxy_url: str = "http://localhost:8766",
    config_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Configure OpenClaw to route through tokenpak.

    Adds/updates tokenpak-* provider entries in every openclaw.json on
    the host. Preserves all existing non-tokenpak configuration and is
    idempotent — safe to run repeatedly.

    Args:
        proxy_url: TokenPak proxy URL (default: http://localhost:8766).
        config_path: Optional explicit openclaw.json to target. When
            None (the default), iterates every install returned by
            ``discover_openclaw_configs()`` so sibling instances like
            a governor at ``~/.openclaw-governor/`` stay in sync.

    Returns:
        {
          "configs": [
            {"path": str, "providers_added": [...], "providers_updated": [...],
             "claude_code_backend": bool, "models_source": str},
            ...
          ],
          "total_added": int,
          "total_updated": int,
        }
        Per-config entries may carry an "error" key if that instance
        couldn't be updated.
    """
    if config_path is not None:
        targets = [Path(config_path).expanduser()]
    else:
        targets = discover_openclaw_configs()

    if not targets:
        return {"error": "No OpenClaw install detected on this host"}

    per_config: list[dict[str, Any]] = [
        _setup_single_openclaw(t, proxy_url) for t in targets
    ]

    total_added = sum(len(c.get("providers_added", [])) for c in per_config)
    total_updated = sum(len(c.get("providers_updated", [])) for c in per_config)

    return {
        "configs": per_config,
        "total_added": total_added,
        "total_updated": total_updated,
    }


__all__ = [
    "OpenClawAdapter",
    "execute_via_claude_code",
    "detect_openclaw",
    "discover_openclaw_configs",
    "setup_openclaw",
]
