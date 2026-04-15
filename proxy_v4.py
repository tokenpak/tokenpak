"""proxy_v4.py — legacy proxy shim for backward compatibility.

The proxy_v4 monolith has been replaced by the modular tokenpak/ package.
This shim re-exports the key globals/classes that tests reference so that
legacy test files can still be collected.

Budget enforcement wired here as part of TRIX-02 / pmgtm initiative (AC-1.2).
"""
from __future__ import annotations

import os
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Helpers for reading booleans/ints from env vars (same logic as config.py)
# ---------------------------------------------------------------------------

def _bool_env(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "on")


def _int_env(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Compression defaults — Flipped 2026-04-13 — see TRIX-01 / pmgtm initiative
# ---------------------------------------------------------------------------
ENABLE_COMPACTION: bool = _bool_env("TOKENPAK_COMPACT", True)          # was False pre-flip
COMPACT_THRESHOLD_TOKENS: int = _int_env("TOKENPAK_COMPACT_THRESHOLD_TOKENS", 1500)  # was 4500 pre-flip
BUDGET_CONTROLLER_ENABLED: bool = _bool_env("TOKENPAK_BUDGET_CONTROLLER", True)  # was False pre-flip
VALIDATION_GATE_ENABLED: bool = _bool_env("TOKENPAK_VALIDATION_GATE", True)

# ---------------------------------------------------------------------------
# Compression pipeline re-exports (used by tests and legacy callers)
# ---------------------------------------------------------------------------
try:
    from tokenpak.proxy.adapters.utils import _detect_adapter
    from tokenpak.compression.pipeline import compact_request_body
except ImportError:
    # Non-fatal — these functions are optional; tests that need them
    # will fail with AttributeError, which is the correct signal.
    pass

# ---------------------------------------------------------------------------
# Budget enforcement — TRIX-02 / pmgtm initiative (AC-1.2)
# ---------------------------------------------------------------------------

def _load_budget_monthly_usd() -> Optional[float]:
    """Load monthly USD budget limit from env var or config file.

    Priority:
    1. ``TOKENPAK_BUDGET_MONTHLY_USD`` env var
    2. ``budget.monthly_usd`` in the config file (JSON or YAML)
    3. None (unlimited)
    """
    val = os.environ.get("TOKENPAK_BUDGET_MONTHLY_USD")
    if val is not None:
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    # Try config file
    config_path_str = os.environ.get("TOKENPAK_CONFIG")
    if config_path_str:
        config_path = Path(config_path_str)
    else:
        # Try config.json (documented shape) then config.yaml
        home = Path.home() / ".tokenpak"
        config_path = home / "config.json"
        if not config_path.exists():
            config_path = home / "config.yaml"

    try:
        if config_path.exists():
            import json as _j
            try:
                cfg = _j.loads(config_path.read_text())
            except Exception:
                try:
                    import yaml as _y  # type: ignore[import-untyped]
                    with open(config_path) as _f:
                        cfg = _y.safe_load(_f) or {}
                except Exception:
                    cfg = {}
            budget_usd = cfg.get("budget", {}).get("monthly_usd")
            if budget_usd is not None:
                return float(budget_usd)
    except Exception:
        pass

    return None


# Module-level constant — read once at import time so tests can reload
# the module with a different env var.
BUDGET_MONTHLY_USD: Optional[float] = _load_budget_monthly_usd()

# Cached monthly spend — populated lazily and refreshed at most once per
# BUDGET_CACHE_TTL_S seconds to avoid a fresh DB hit on every request.
# Tests inject values directly: mod._MONTHLY_SPEND_CACHE["usd"] = X
BUDGET_CACHE_TTL_S: float = 60.0
_MONTHLY_SPEND_CACHE: Dict[str, float] = {"usd": 0.0, "ts": 0.0}


def _get_cached_monthly_spend() -> float:
    """Return cached monthly spend; refresh from BudgetTracker if stale."""
    now = time.time()
    if now - _MONTHLY_SPEND_CACHE.get("ts", 0.0) > BUDGET_CACHE_TTL_S:
        try:
            from tokenpak.telemetry.budget import get_budget_tracker
            tracker = get_budget_tracker()
            _MONTHLY_SPEND_CACHE["usd"] = tracker.total_spent("monthly")
            _MONTHLY_SPEND_CACHE["ts"] = now
        except Exception:
            pass  # keep the existing cached value
    return _MONTHLY_SPEND_CACHE.get("usd", 0.0)


# ---------------------------------------------------------------------------
# Global state (mirrored from legacy proxy_v4 monolith)
# ---------------------------------------------------------------------------

SESSION: Dict[str, Any] = {
    "requests": 0,
    "input_tokens": 0,
    "sent_input_tokens": 0,
    "saved_tokens": 0,
    "output_tokens": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "cost": 0.0,
    "cost_saved": 0.0,
    "errors": 0,
    "budget_blocked_total": 0,
    "start_time": time.time(),
}

_proxy_ready: bool = False
_shutdown_event = threading.Event()
_active_request_count: int = 0
_provider_circuit_lock = threading.Lock()
_provider_circuits: Dict[str, Dict[str, Any]] = {
    "anthropic": {"open": False, "failures": 0},
    "openai": {"open": False, "failures": 0},
    "google": {"open": False, "failures": 0},
}


# ---------------------------------------------------------------------------
# Adapter registry stub
# ---------------------------------------------------------------------------

class _Adapter:
    def __init__(self, source_format: str, upstream: str = "") -> None:
        self.source_format = source_format
        self._upstream = upstream


class _AdapterRegistry:
    _adapters = [
        _Adapter("passthrough", ""),
        _Adapter("anthropic", "https://api.anthropic.com"),
        _Adapter("openai", "https://api.openai.com"),
    ]

    def adapters(self):
        return self._adapters


ADAPTER_REGISTRY = _AdapterRegistry()


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def _load_openclaw_upstream_overrides() -> Dict[str, str]:
    """Load upstream URL overrides from ~/.openclaw/upstream.json."""
    p = Path.home() / ".openclaw" / "upstream.json"
    if not p.exists():
        return {}
    try:
        import json
        return json.loads(p.read_text())
    except Exception:
        return {}


def _resolve_upstream(adapter: _Adapter) -> str:
    overrides = _load_openclaw_upstream_overrides()
    if adapter.source_format in overrides:
        return overrides[adapter.source_format]
    return adapter._upstream


def _startup_preflight(port: int) -> None:
    """No-op startup check for legacy tests."""
    pass


def _classify_intent(text: str, _semantic_meta: "dict | None" = None) -> str:
    """Keyword-based intent classification — canonical intent set."""
    try:
        from tokenpak.semantic.resolver import get_default_resolver as _get_resolver
        _resolver = _get_resolver()
        _sem_result = _resolver.resolve_intent(text)
        if _sem_result is not None:
            if _semantic_meta is not None:
                _semantic_meta["intent_alias"] = _sem_result.alias_matched
                _semantic_meta["intent_canonical"] = _sem_result.canonical
                _semantic_meta["match_type"] = _sem_result.match_type
            return _sem_result.canonical
    except Exception:
        pass

    t = text.lower()
    if any(k in t for k in ("status", "health", "is it running", "is it up", "ping", "uptime", "alive", "reachable", "available")):
        return "status"
    if any(k in t for k in ("usage", "cost", "spend", "how much", "token count", "billing", "how many tokens")):
        return "usage"
    if any(k in t for k in ("run ", "execute", "start ", "deploy", "launch", "trigger", "kick off", "fire")):
        return "execute"
    if any(k in t for k in ("fix", "debug", "error", "bug", "broken", "failing", "exception", "traceback", "crash", "why is")):
        return "debug"
    if any(k in t for k in ("summarize", "tldr", "brief", "recap", "summary", "condense", "digest")):
        return "summarize"
    if any(k in t for k in ("plan", "design", "architect", "roadmap", "strategy", "approach", "what should i", "how should i")):
        return "plan"
    if any(k in t for k in ("explain", "what is", "how does", "describe", "tell me about", "what does", "how do")):
        return "explain"
    if any(k in t for k in ("find", "search", "look up", "where", "locate", "which", "list all")):
        return "search"
    if any(k in t for k in ("write", "create", "generate", "build", "implement", "make a", "add a", "new ")):
        return "create"
    return "query"


# ---------------------------------------------------------------------------
# Ingest entry writer (for test_ingest_proxy_v4.py)
# ---------------------------------------------------------------------------
import json as _json
import uuid as _uuid
import datetime as _datetime

INGEST_ENTRIES_DIR = Path.home() / ".tokenpak" / "ingest" / "entries"


def _ingest_write_entry(entry: Dict[str, Any]) -> str:
    """Write a telemetry entry to a dated JSONL file; return entry ID."""
    import os as _os
    entry_id = entry.get("id") or str(_uuid.uuid4())
    record = dict(entry)
    record["id"] = entry_id
    if "timestamp" not in record:
        record["timestamp"] = _datetime.datetime.now(_datetime.timezone.utc).isoformat()

    entries_dir = Path(INGEST_ENTRIES_DIR)
    entries_dir.mkdir(parents=True, exist_ok=True)

    date_str = _datetime.date.today().isoformat()
    jsonl_path = entries_dir / f"{date_str}.jsonl"
    with open(jsonl_path, "a", encoding="utf-8") as fh:
        fh.write(_json.dumps(record) + "\n")
    return entry_id


# ---------------------------------------------------------------------------
# ThreadedHTTPServer — handles each connection in its own thread
# ---------------------------------------------------------------------------

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """HTTPServer that handles each request in a daemon thread."""

    daemon_threads = True


# ---------------------------------------------------------------------------
# ForwardProxyHandler
# ---------------------------------------------------------------------------

class ForwardProxyHandler(BaseHTTPRequestHandler):
    """HTTP handler for legacy proxy_v4 tests.

    do_POST implements budget enforcement (TRIX-02 / AC-1.2):
    - If monthly budget is set and exceeded: 429 budget_exceeded
    - Otherwise: forward to upstream (or 502 on connection failure)

    do_GET serves a simple health/status response.
    """

    def do_GET(self) -> None:
        import json
        import datetime

        if self.path == "/health":
            self._handle_health()
        elif self.path == "/ready":
            self._handle_ready()
        else:
            body = json.dumps({"status": "ok", "proxy": "tokenpak-shim"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _handle_health(self) -> None:
        import json
        import datetime

        # Determine circuit breaker state
        with _provider_circuit_lock:
            open_circuits = [k for k, v in _provider_circuits.items() if v["open"]]
        total_circuits = len(_provider_circuits)
        all_open = len(open_circuits) == total_circuits

        # Determine error rate
        requests = SESSION.get("requests", 0)
        errors = SESSION.get("errors", 0)
        high_error_rate = requests > 0 and (errors / requests) > 0.10

        # Compute status
        if all_open:
            status = "critical"
            http_code = 503
        elif open_circuits or high_error_rate:
            status = "degraded"
            http_code = 200
        else:
            status = "healthy"
            http_code = 200

        # Compute uptime
        uptime = int(time.time() - SESSION.get("start_time", time.time()))

        # Version
        try:
            from tokenpak import __version__ as _ver
        except Exception:
            _ver = "unknown"

        # Timestamp
        timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        # Components
        components = {
            "cache": {
                "hits": SESSION.get("cache_hits", 0),
                "misses": SESSION.get("cache_misses", 0),
            },
            "provider_connections": {
                k: ("open" if v["open"] else "closed")
                for k, v in _provider_circuits.items()
            },
            "config": {
                "budget_controller": BUDGET_CONTROLLER_ENABLED,
                "compaction": ENABLE_COMPACTION,
            },
        }

        # Suggestions
        suggestions: list = []
        if all_open:
            suggestions.append(
                "All providers unreachable — check provider API keys and network connectivity"
            )
        elif open_circuits:
            suggestions.append(
                f"Provider(s) {', '.join(open_circuits)} have open circuit breakers — check provider status"
            )
        if high_error_rate:
            pct = int(errors / requests * 100)
            suggestions.append(
                f"High error rate detected ({pct}%) — review logs for upstream errors"
            )

        body = json.dumps({
            "status": status,
            "uptime": uptime,
            "version": _ver,
            "timestamp": timestamp,
            "components": components,
            "suggestions": suggestions,
        }).encode()

        self.send_response(http_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_ready(self) -> None:
        import json

        if _shutdown_event.is_set():
            body = json.dumps({"ready": False, "status": "shutting_down"}).encode()
            http_code = 503
        elif not _proxy_ready:
            body = json.dumps({"ready": False, "status": "starting_up"}).encode()
            http_code = 503
        else:
            body = json.dumps({"ready": True}).encode()
            http_code = 200

        self.send_response(http_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        import json
        import urllib.request
        import urllib.error

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        # ---------------------------------------------------------------
        # Budget gate — check before any upstream forwarding (AC-1.2)
        # ---------------------------------------------------------------
        if BUDGET_MONTHLY_USD is not None:
            spent = _get_cached_monthly_spend()
            try:
                from tokenpak._internal.budget_controller import BudgetController
                result = BudgetController().check(BUDGET_MONTHLY_USD, spent)
            except Exception:
                result = None  # fail open — do not block on internal error

            if result is not None and result.exceeded:
                SESSION["budget_blocked_total"] = (
                    SESSION.get("budget_blocked_total", 0) + 1
                )
                resp_body = json.dumps({
                    "error": {
                        "type": "budget_exceeded",
                        "limit_usd": result.limit_usd,
                        "spent_usd": result.spent_usd,
                        "reset_at": result.reset_at,
                    }
                }).encode()
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
                return

        # ---------------------------------------------------------------
        # Forward to upstream
        # ---------------------------------------------------------------
        try:
            # Build upstream URL from passthrough URL or default to Anthropic
            upstream_base = _resolve_upstream(
                next(
                    (a for a in ADAPTER_REGISTRY.adapters()
                     if a.source_format == "anthropic"),
                    None,
                ) or _Adapter("anthropic", "https://api.anthropic.com")
            )
            upstream_url = upstream_base.rstrip("/") + self.path

            req = urllib.request.Request(upstream_url, data=body, method="POST")
            for key in ("Content-Type", "Authorization", "X-Api-Key",
                        "Anthropic-Version", "Anthropic-Beta"):
                val = self.headers.get(key)
                if val:
                    req.add_header(key, val)

            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                self.send_response(resp.status)
                ct = resp.headers.get("Content-Type", "application/json")
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        except urllib.error.HTTPError as exc:
            raw = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except Exception:
            err_body = json.dumps({
                "error": {"type": "upstream_error", "message": "Upstream connection failed"}
            }).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)

    def log_message(self, *args):  # suppress access log noise in tests
        pass
