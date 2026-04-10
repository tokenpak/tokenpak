"""proxy_v4.py — legacy proxy shim for backward compatibility.

The proxy_v4 monolith has been replaced by the modular tokenpak/ package.
This shim re-exports the key globals/classes that tests reference so that
legacy test files can still be collected.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict

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
}

_proxy_ready: bool = False
_shutdown_event = threading.Event()
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
# ForwardProxyHandler stub
# ---------------------------------------------------------------------------

class ForwardProxyHandler(BaseHTTPRequestHandler):
    """Stub HTTP handler for legacy proxy_v4 tests."""

    def do_GET(self) -> None:
        import json
        body = json.dumps({"status": "ok", "proxy": "tokenpak-shim"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # suppress access log noise in tests
        pass
