"""
tokenpak.proxy.fallback — API key pool, upstream routing, and key failover.

Extracted from runtime/proxy.py (L608-811) as part of TPK-RESTRUCTURE-002.
Circuit breakers, rate limiting, header sanitizing, and error helpers were
separated into proxy/circuit_breaker.py (TPK-RESTRUCTURE-003).
"""
import os
import re
import json
import socket
import threading
import time
from typing import Optional, Dict, Any
from urllib.parse import urlparse

from .config import _cfg, UPSTREAM_ROUTES, UPSTREAM_TIMEOUT
from .circuit_breaker import (  # noqa: F401 — re-exported for consumers
    OLLAMA_UPSTREAM,
    OLLAMA_CONNECT_TIMEOUT,
    _ollama_circuit,
    _ollama_circuit_lock,
    _provider_circuits,
    _provider_circuit_lock,
    _provider_for_url,
    _circuit_check,
    _circuit_record_failure,
    _circuit_record_success,
    _RATE_LIMIT_RPM,
    _rate_buckets,
    _rate_bucket_lock,
    _MAX_REQUEST_BYTES,
    _BLOCKED_FORWARD_HEADERS,
    _sanitize_headers,
    _suggest_model,
    _make_structured_error,
    _enrich_upstream_error,
    _rate_limit_check,
)

# ForwardAdapter import — used by _resolve_upstream
try:
    from tokenpak.proxy.adapters.registry import FormatAdapter
except ImportError:
    FormatAdapter = object  # type: ignore

# ---------------------------------------------------------------------------
# Key rotation configuration (env-driven)
# ---------------------------------------------------------------------------
_KEY_ROTATION_MODE: str = os.environ.get("TOKENPAK_KEY_ROTATION", "failover")
_KEY_COOLDOWN_429: float = float(os.environ.get("TOKENPAK_KEY_COOLDOWN_429", "60"))
_KEY_COOLDOWN_401: float = float(os.environ.get("TOKENPAK_KEY_COOLDOWN_401", "300"))


def _build_key_pool() -> list:
    candidates = [
        os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        os.environ.get("ANTHROPIC_OAUTH_TOKEN", "").strip(),
        os.environ.get("ANTHROPIC_OAUTH_TOKEN2", "").strip(),
    ]
    pool = [k for k in candidates if k]
    # Log count but never the keys themselves
    print(f"[key-pool] Found {len(pool)} Anthropic API key(s)", flush=True)
    return pool

_ANTHROPIC_KEY_POOL: list = _build_key_pool()


def _reload_config_from_env() -> str:
    """Hot-reload env vars on SIGHUP or POST /config/reload.

    Updates the key pool and UPSTREAM_TIMEOUT from the current environment
    without restarting the proxy. In-flight requests are not interrupted.
    Returns a human-readable summary string.
    """
    global _ANTHROPIC_KEY_POOL, UPSTREAM_TIMEOUT
    old_pool_size = len(_ANTHROPIC_KEY_POOL)
    _ANTHROPIC_KEY_POOL = _build_key_pool()
    new_pool_size = len(_ANTHROPIC_KEY_POOL)

    old_timeout = UPSTREAM_TIMEOUT
    UPSTREAM_TIMEOUT = _cfg("upstream.timeout", 90, "TOKENPAK_UPSTREAM_TIMEOUT", int)

    msg = (
        f"SIGHUP: config reloaded — "
        f"keys: {old_pool_size} → {new_pool_size}, "
        f"timeout: {old_timeout}s → {UPSTREAM_TIMEOUT}s"
    )
    print(f"[config] {msg}", flush=True)
    return msg


# Per-key cooldown state: {key_index: cooldown_until_timestamp}
_KEY_COOLDOWN_STATE: dict = {}
_KEY_COOLDOWN_LOCK = threading.Lock()
# Round-robin counter (only used in roundrobin mode)
_KEY_RR_INDEX: int = 0
_KEY_RR_LOCK = threading.Lock()


def _key_is_available(idx: int) -> bool:
    """Return True if key at idx is not in cooldown."""
    with _KEY_COOLDOWN_LOCK:
        until = _KEY_COOLDOWN_STATE.get(idx, 0)
    return time.time() >= until


def _cool_down_key(idx: int, duration: float, reason: str) -> None:
    """Set cooldown on a key."""
    with _KEY_COOLDOWN_LOCK:
        _KEY_COOLDOWN_STATE[idx] = time.time() + duration
    key_hint = (_ANTHROPIC_KEY_POOL[idx][:8] + "...") if _ANTHROPIC_KEY_POOL else "?"
    print(f"[key-pool] Key #{idx} ({key_hint}) cooling down for {duration}s — reason: {reason}", flush=True)


def _get_next_key(exclude_idx: Optional[int] = None) -> tuple:
    """
    Return (key, index) for the next available key.
    In failover mode: always start from idx 0, skip cooled-down.
    In roundrobin mode: start from next round-robin index.
    Returns (None, -1) if no keys available.
    """
    global _KEY_RR_INDEX
    if not _ANTHROPIC_KEY_POOL:
        return None, -1

    if _KEY_ROTATION_MODE == "roundrobin":
        with _KEY_RR_LOCK:
            start = _KEY_RR_INDEX
            for i in range(len(_ANTHROPIC_KEY_POOL)):
                idx = (start + i) % len(_ANTHROPIC_KEY_POOL)
                if idx != exclude_idx and _key_is_available(idx):
                    _KEY_RR_INDEX = (idx + 1) % len(_ANTHROPIC_KEY_POOL)
                    return _ANTHROPIC_KEY_POOL[idx], idx
    else:
        # failover: try in order, skip excluded and cooled-down
        for idx, key in enumerate(_ANTHROPIC_KEY_POOL):
            if idx != exclude_idx and _key_is_available(idx):
                return key, idx

    return None, -1


def _strip_empty_text_blocks(body_bytes):
    """Remove empty text blocks from system/messages — Anthropic rejects them."""
    try:
        data = json.loads(body_bytes)
        changed = False
        # Clean system blocks
        system = data.get("system")
        if isinstance(system, list):
            cleaned = [b for b in system if not (isinstance(b, dict) and b.get("type") == "text" and not b.get("text", "").strip())]
            if len(cleaned) != len(system):
                data["system"] = cleaned
                changed = True
        # Clean message content blocks
        for msg in data.get("messages", []):
            content = msg.get("content")
            if isinstance(content, list):
                cleaned = [p for p in content if not (isinstance(p, dict) and p.get("type") == "text" and not p.get("text", "").strip())]
                if len(cleaned) != len(content):
                    # Ensure at least one content block remains
                    if cleaned:
                        msg["content"] = cleaned
                    else:
                        msg["content"] = [{"type": "text", "text": " "}]
                    changed = True
            elif isinstance(content, str) and not content.strip():
                msg["content"] = " "
                changed = True
        if changed:
            return json.dumps(data).encode()
        return body_bytes
    except Exception:
        return body_bytes


def _cap_cache_control_blocks(body_bytes, max_blocks=4):
    """Anthropic allows max 4 cache_control blocks. Strip extras (including tools)."""
    try:
        body = json.loads(body_bytes)
    except Exception:
        return body_bytes
    locations = []
    system = body.get("system", [])
    if isinstance(system, list):
        for i, block in enumerate(system):
            if isinstance(block, dict) and "cache_control" in block:
                locations.append(("system", i))
    tools = body.get("tools", [])
    if isinstance(tools, list):
        for i, tool in enumerate(tools):
            if isinstance(tool, dict) and "cache_control" in tool:
                locations.append(("tools", i))
    for mi, msg in enumerate(body.get("messages", [])):
        c = msg.get("content", [])
        if isinstance(c, list):
            for ci, block in enumerate(c):
                if isinstance(block, dict) and "cache_control" in block:
                    locations.append(("messages", mi, ci))
    if len(locations) <= max_blocks:
        return body_bytes
    to_remove = locations[:-max_blocks]
    for loc in to_remove:
        if loc[0] == "system":
            body["system"][loc[1]].pop("cache_control", None)
        elif loc[0] == "tools":
            body["tools"][loc[1]].pop("cache_control", None)
        else:
            body["messages"][loc[1]]["content"][loc[2]].pop("cache_control", None)
    print(
        f"  🔧 Capped cache_control: {len(locations)} -> {max_blocks} (removed from: {[l[0] for l in to_remove]})"
    )
    return json.dumps(body).encode()


def _resolve_upstream(adapter: FormatAdapter) -> str:
    mapped = UPSTREAM_ROUTES.get(adapter.source_format)
    if mapped:
        return mapped

    # Hard fail for passthrough: unknown/undetected providers must be explicitly routed.
    if adapter.source_format == "passthrough":
        raise ValueError(
            "No upstream route mapping for passthrough requests. "
            "Configure models.providers tokenpak-* source providers or set "
            "TOKENPAK_UPSTREAM_PASSTHROUGH."
        )

    return adapter.get_default_upstream()


def _extract_host(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.hostname:
            return parsed.hostname
        return parsed.netloc.split(":")[0]
    except Exception:
        return ""


INTERCEPT_HOSTS = {
    host for host in (_extract_host(url) for url in UPSTREAM_ROUTES.values()) if host
}


# ---------------------------------------------------------------------------
# FallbackChain — OOP wrapper around key pool + upstream failover (FIN-07)
# ---------------------------------------------------------------------------


class FallbackChain:
    """
    Convenience class that encapsulates the key-pool failover logic.

    Wraps the module-level ``_build_key_pool``, ``_get_next_key``,
    ``_cool_down_key``, and upstream-resolution helpers into a single
    object for consumers that prefer an OOP interface.

    Example::

        chain = FallbackChain()
        idx, key = chain.next_key()
        if not chain.send(idx, ...):
            chain.cool_down(idx, 120, "429 rate-limited")
            idx2, key2 = chain.next_key(exclude=idx)
    """

    def __init__(self):
        self._pool = _build_key_pool()

    @property
    def pool_size(self) -> int:
        return len(self._pool)

    def next_key(self, exclude: Optional[int] = None):
        """Return ``(index, key)`` for the next available API key."""
        return _get_next_key(exclude_idx=exclude)

    def cool_down(self, idx: int, duration: float, reason: str) -> None:
        """Mark key *idx* as temporarily unavailable."""
        _cool_down_key(idx, duration, reason)

    def is_available(self, idx: int) -> bool:
        return _key_is_available(idx)

    def resolve_upstream(self, adapter) -> str:
        return _resolve_upstream(adapter)

    def reload_config(self) -> str:
        return _reload_config_from_env()

    def __repr__(self) -> str:
        return f"FallbackChain(pool_size={self.pool_size})"


# ---------------------------------------------------------------------------
# Exponential backoff — transferred from monolith (TPK-CONSOLIDATION-A2a)
# ---------------------------------------------------------------------------
import logging as _logging
import random as _random_module

_BACKOFF_BASE: float = float(os.environ.get("TOKENPAK_BACKOFF_BASE", "1.0"))
_BACKOFF_CAP: float = float(os.environ.get("TOKENPAK_BACKOFF_CAP", "32.0"))
_MAX_RETRIES: int = int(os.environ.get("TOKENPAK_MAX_RETRIES", "3"))

_backoff_logger = _logging.getLogger(__name__)


def _backoff_wait(attempt: int, base: float = _BACKOFF_BASE, cap: float = _BACKOFF_CAP) -> None:
    """Exponential backoff: base * 2^attempt with 25% jitter, capped at cap seconds."""
    wait = min(base * (2 ** attempt), cap)
    wait *= (1.0 + _random_module.uniform(0, 0.25))
    _backoff_logger.info("Rate limited — backoff %.1fs (attempt %d)", wait, attempt)
    time.sleep(wait)

