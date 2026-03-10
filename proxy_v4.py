#!/usr/bin/env python3
"""
TokenPak Forward Proxy v4 — Two-Tier Context Injection

Changes from v3:
- Two-Tier Index: loads BOTH workspace + vault indexes
- Context Injection: BM25 search across vault index, inject relevant blocks
  into system prompt as supplementary context
- Style Contracts preserved from v3
- All v3 features maintained (compilation modes, protected content, etc.)

Env vars:
    TOKENPAK_PORT             (default: 8766)
    TOKENPAK_MODE             (default: hybrid) — strict|hybrid|aggressive
    TOKENPAK_COMPACT          (default: 1) — master on/off switch
    TOKENPAK_COMPACT_MAX_CHARS      (default: 120) — max chars for compressed text
    TOKENPAK_COMPACT_THRESHOLD_TOKENS (default: 4500) — skip compaction below this
    TOKENPAK_COMPACT_CACHE_SIZE     (default: 2000)
    TOKENPAK_DB               (default: .ocp/monitor.db)
    TOKENPAK_VAULT_INDEX      (default: ~/vault/.tokenpak) — path to shared vault index
    TOKENPAK_INJECT_BUDGET    (default: 4000) — max tokens to inject from vault
    TOKENPAK_INJECT_TOP_K     (default: 5) — max vault blocks to inject
    TOKENPAK_INJECT_MIN_SCORE (default: 2.0) — minimum BM25 score to include
    TOKENPAK_CAPSULE_BUILDER  (default: 0) — enable capsule builder stage (0|1)
    TOKENPAK_CAPSULE_MIN_CHARS (default: 400) — min chars for a block to be capsulised
    TOKENPAK_CAPSULE_HOT_WINDOW (default: 2) — trailing messages excluded from capsule compression
"""

import json
import time
import threading
import socket
import ssl
import signal
import os
import sys
import re
import gzip
import io
import math
import hashlib
import http.client
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional, Any, Mapping
from dataclasses import dataclass, field, asdict
from collections import deque
from urllib.parse import urlparse
import uuid

from tokenpak.proxy.adapters import build_default_registry
from tokenpak.proxy.adapters.base import FormatAdapter

# ---------------------------------------------------------------------------
# Feature imports — CANON dedup
# ---------------------------------------------------------------------------
try:
    import sys as _sys_canon
    import os as _os_canon
    _sys_canon.path.insert(0, _os_canon.path.expanduser("~/.openclaw/workspace/.ocp"))
    from canon_session import apply_canon_refs, get_session as get_canon_session
    CANON_AVAILABLE = True
except ImportError:
    CANON_AVAILABLE = False
    def apply_canon_refs(body, session_id=""):
        return body, 0, 0

# ---------------------------------------------------------------------------
# PromptBuilder — stable/volatile prefix split for cache efficiency
# ---------------------------------------------------------------------------
try:
    from tokenpak.agent.proxy.prompt_builder import (
        apply_stable_cache_control as _apply_stable_cache_control,
        inject_with_cache_boundary as _inject_with_cache_boundary,
    )
    PROMPT_BUILDER_AVAILABLE = True
except ImportError:
    PROMPT_BUILDER_AVAILABLE = False
    def _apply_stable_cache_control(body_bytes):
        return body_bytes
    def _inject_with_cache_boundary(body_bytes, volatile_text):
        return body_bytes

# ---------------------------------------------------------------------------
# Tool Schema Registry — normalizes tools array to byte-identical JSON
# Enables Anthropic prompt cache hits on repeated tool calls
# ---------------------------------------------------------------------------
try:
    from tokenpak.agent.proxy.tool_schema_registry import get_registry as _get_tool_registry
    TOOL_REGISTRY_AVAILABLE = True
except ImportError:
    TOOL_REGISTRY_AVAILABLE = False
    def _get_tool_registry():
        return None

# ---------------------------------------------------------------------------
# Pipeline Trace — captures per-request pipeline execution details
# ---------------------------------------------------------------------------
@dataclass
class StageTrace:
    """Trace for a single pipeline stage."""
    name: str  # capsule, segmentizer, recipe_engine, compaction, vault_injection, validation_gate
    enabled: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_delta: int = 0
    duration_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PipelineTrace:
    """Complete trace for a request through the pipeline."""
    request_id: str
    timestamp: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_saved: int = 0
    cost_saved: float = 0.0
    total_cost: float = 0.0
    duration_ms: float = 0.0
    stages: List[StageTrace] = field(default_factory=list)
    status: str = "pending"  # pending, complete, error

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stages"] = [s.to_dict() if hasattr(s, "to_dict") else s for s in self.stages]
        return d


class TraceStorage:
    """Thread-safe storage for recent pipeline traces."""

    def __init__(self, max_traces: int = 10):
        self._traces: deque = deque(maxlen=max_traces)
        self._lock = threading.Lock()
        self._by_id: Dict[str, PipelineTrace] = {}

    def store(self, trace: PipelineTrace):
        """Store a completed trace."""
        with self._lock:
            self._traces.append(trace)
            self._by_id[trace.request_id] = trace
            # Clean up old entries from _by_id
            if len(self._by_id) > len(self._traces) * 2:
                valid_ids = {t.request_id for t in self._traces}
                self._by_id = {k: v for k, v in self._by_id.items() if k in valid_ids}

    def get_last(self) -> Optional[PipelineTrace]:
        """Get the most recent trace."""
        with self._lock:
            return self._traces[-1] if self._traces else None

    def get_by_id(self, request_id: str) -> Optional[PipelineTrace]:
        """Get a specific trace by ID."""
        with self._lock:
            return self._by_id.get(request_id)

    def get_all(self) -> List[PipelineTrace]:
        """Get all stored traces."""
        with self._lock:
            return list(self._traces)


# Global trace storage
TRACE_STORAGE = TraceStorage(max_traces=10)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROXY_PORT = int(os.environ.get("TOKENPAK_PORT", 8766))
MONITOR_DB = os.environ.get("TOKENPAK_DB", str(Path(__file__).parent / "monitor.db"))
VAULT_SYNC_INTERVAL = 60
ENABLE_COMPACTION = os.environ.get("TOKENPAK_COMPACT", "1").lower() in ("1", "true", "yes", "on")
COMPACT_MAX_CHARS = int(os.environ.get("TOKENPAK_COMPACT_MAX_CHARS", "120"))
COMPACT_THRESHOLD_TOKENS = int(os.environ.get("TOKENPAK_COMPACT_THRESHOLD_TOKENS", "4500"))
COMPACT_CACHE_SIZE = int(os.environ.get("TOKENPAK_COMPACT_CACHE_SIZE", "2000"))
COMPILATION_MODE = os.environ.get("TOKENPAK_MODE", "hybrid").lower()  # strict|hybrid|aggressive

# Capsule Builder config (Phase 0.5 of request pipeline)
ENABLE_CAPSULE_BUILDER = os.environ.get("TOKENPAK_CAPSULE_BUILDER", "0").lower() in ("1", "true", "yes", "on")
CAPSULE_MIN_CHARS = int(os.environ.get("TOKENPAK_CAPSULE_MIN_CHARS", "400"))
CAPSULE_HOT_WINDOW = int(os.environ.get("TOKENPAK_CAPSULE_HOT_WINDOW", "2"))

# Router feature flag — DeterministicRouter integration
ROUTER_ENABLED: bool = os.environ.get("TOKENPAK_ROUTER_ENABLED", "1").lower() in ("1", "true", "yes", "on")

# Skeleton extraction — strip code bodies for vault-injected code blocks
SKELETON_ENABLED: bool = os.environ.get("TOKENPAK_SKELETON_ENABLED", "1").lower() in ("1", "true", "yes", "on")

# Shadow reader validation — coherence-check compressed output before sending
SHADOW_ENABLED: bool = os.environ.get("TOKENPAK_SHADOW_ENABLED", "1").lower() in ("1", "true", "yes", "on")

# Budget allocation — enforce per-bucket token limits in capsule assembly
BUDGET_TOTAL_TOKENS: int = int(os.environ.get("TOKENPAK_BUDGET_TOTAL", "12000"))

# Chat footer — inject stats into SSE stream (visible in chat)
CHAT_FOOTER_ENABLED: bool = os.environ.get("TOKENPAK_CHAT_FOOTER", "0").lower() in ("1", "true", "yes", "on")

# Fix #3: Configurable upstream timeout (default 300s)
UPSTREAM_TIMEOUT: int = int(os.environ.get("TOKENPAK_UPSTREAM_TIMEOUT", "300"))

# Fix #5: Strict validation mode — reject malformed requests (vs warn-and-forward)
STRICT_VALIDATION: bool = os.environ.get("TOKENPAK_STRICT_MODE", "0").lower() in ("1", "true", "yes", "on")

# Validation gate — pre-forward runtime guardrails for deterministic path
VALIDATION_GATE_ENABLED: bool = os.environ.get("TOKENPAK_VALIDATION_GATE", "1").lower() in ("1", "true", "yes", "on")
VALIDATION_GATE_BUDGET_CAP: int = int(os.environ.get("TOKENPAK_VALIDATION_GATE_BUDGET_CAP", "120000"))

# Two-Tier Index Config
VAULT_INDEX_PATH = os.environ.get("TOKENPAK_VAULT_INDEX", str(Path.home() / "vault" / ".tokenpak"))
INJECT_BUDGET = int(os.environ.get("TOKENPAK_INJECT_BUDGET", "4000"))  # raised to 4000 for cache stability
INJECT_TOP_K = int(os.environ.get("TOKENPAK_INJECT_TOP_K", "5"))
INJECT_MIN_SCORE = float(os.environ.get("TOKENPAK_INJECT_MIN_SCORE", "2.0"))
INJECT_SKIP_MODELS = os.environ.get("TOKENPAK_INJECT_SKIP_MODELS", "haiku")
INJECT_MIN_PROMPT = int(os.environ.get("TOKENPAK_INJECT_MIN_PROMPT", "1000"))
VAULT_INDEX_RELOAD_INTERVAL = 300  # reload vault index every 5 min

_COMPACT_CACHE = {}
_COMPACT_CACHE_ORDER = []

ADAPTER_REGISTRY = build_default_registry()


def _load_openclaw_upstream_overrides() -> Dict[str, str]:
    """
    Auto-discover upstream routes from openclaw.json tokenpak-* provider mirrors.
    Supports current OpenClaw shape at `models.providers` and legacy root `providers`.
    """
    cfg_path = Path.home() / ".openclaw" / "openclaw.json"
    if not cfg_path.exists():
        return {}

    try:
        cfg = json.loads(cfg_path.read_text())
    except Exception:
        return {}

    providers = None
    models = cfg.get("models")
    if isinstance(models, dict):
        model_providers = models.get("providers")
        if isinstance(model_providers, dict):
            providers = model_providers

    if providers is None:
        legacy_providers = cfg.get("providers")
        if isinstance(legacy_providers, dict):
            providers = legacy_providers

    if not isinstance(providers, dict):
        return {}

    aliases = {
        "anthropic": "anthropic-messages",
        "openai": "openai-chat",
        "openai-codex": "openai-responses",
        "google": "google-generative-ai",
        # P0 providers
        "openrouter": "openai-chat",
        "litellm": "openai-chat",
        "vercel-ai-gateway": "openai-chat",
        "kilocode": "openai-responses",
        "bedrock": "anthropic-messages",
    }

    overrides: Dict[str, str] = {}
    for name, entry in providers.items():
        if not isinstance(name, str) or not name.startswith("tokenpak-"):
            continue
        if not isinstance(entry, dict):
            continue
        source_provider = entry.get("source_provider") or name[len("tokenpak-"):]
        if not isinstance(source_provider, str):
            continue
        source_entry = providers.get(source_provider)
        if not isinstance(source_entry, dict):
            continue
        base_url = source_entry.get("base_url") or source_entry.get("baseUrl")
        if not isinstance(base_url, str) or not base_url:
            continue

        mapped = aliases.get(source_provider)
        if mapped:
            overrides[mapped] = base_url
            # OpenAI-compatible upstreams are usually shared for Chat + Responses.
            if mapped == "openai-chat":
                overrides.setdefault("openai-responses", base_url)

    return overrides


def _load_env_upstream_overrides() -> Dict[str, str]:
    """
    Read adapter upstream overrides from env:
      TOKENPAK_UPSTREAM_<SOURCE_FORMAT_IN_UPPERCASE_WITH_UNDERSCORES>
    """
    mapping: Dict[str, str] = {}
    for source_format in ADAPTER_REGISTRY.list_formats():
        key = "TOKENPAK_UPSTREAM_" + source_format.upper().replace("-", "_")
        value = os.environ.get(key, "").strip()
        if value:
            mapping[source_format] = value
    return mapping


def _build_upstream_routes() -> Dict[str, str]:
    routes = {
        adapter.source_format: adapter.get_default_upstream()
        for adapter in ADAPTER_REGISTRY.adapters()
    }
    routes.update(_load_openclaw_upstream_overrides())
    routes.update(_load_env_upstream_overrides())
    return routes


UPSTREAM_ROUTES = _build_upstream_routes()


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

# Ollama upstream routing — requests with /ollama-proxy/ prefix get forwarded here
OLLAMA_UPSTREAM = os.environ.get("TOKENPAK_OLLAMA_UPSTREAM", "http://100.80.241.118:11434")
OLLAMA_CONNECT_TIMEOUT = int(os.environ.get("TOKENPAK_OLLAMA_TIMEOUT", "20"))

# Circuit breaker for ollama upstream -- avoids repeated 2-min TCP hangs
_ollama_circuit = {
    "open": False,          # True = upstream known-dead, skip attempts
    "last_failure": 0.0,    # timestamp of last failure
    "cooldown": 120,        # seconds before retrying after failure
}
_ollama_circuit_lock = threading.Lock()

# Fix #5: Per-provider circuit breakers (Anthropic, OpenAI, Google)
_provider_circuits: dict = {
    "anthropic": {"failures": 0, "open": False, "last_failure": 0.0, "threshold": 5, "cooldown": 60},
    "openai":    {"failures": 0, "open": False, "last_failure": 0.0, "threshold": 5, "cooldown": 60},
    "google":    {"failures": 0, "open": False, "last_failure": 0.0, "threshold": 5, "cooldown": 60},
}
_provider_circuit_lock = threading.Lock()

def _provider_for_url(url: str) -> str:
    if "anthropic.com" in url:
        return "anthropic"
    if "openai.com" in url:
        return "openai"
    if "googleapis.com" in url:
        return "google"
    return ""

def _circuit_check(provider: str) -> bool:
    """Return True if circuit is OPEN (requests should be rejected)."""
    if not provider:
        return False
    with _provider_circuit_lock:
        cb = _provider_circuits.get(provider)
        if not cb:
            return False
        if cb["open"]:
            if time.time() - cb["last_failure"] > cb["cooldown"]:
                cb["open"] = False
                cb["failures"] = 0
                print(f"  ✅ Circuit breaker CLOSED for {provider} (cooldown expired)")
                return False
            return True
        return False

def _circuit_record_failure(provider: str):
    if not provider:
        return
    with _provider_circuit_lock:
        cb = _provider_circuits.get(provider)
        if not cb:
            return
        cb["failures"] += 1
        cb["last_failure"] = time.time()
        if cb["failures"] >= cb["threshold"]:
            cb["open"] = True
            print(f"  ⚡ Circuit breaker OPEN for {provider} after {cb['failures']} failures")

def _circuit_record_success(provider: str):
    if not provider:
        return
    with _provider_circuit_lock:
        cb = _provider_circuits.get(provider)
        if cb:
            cb["failures"] = 0
            cb["open"] = False

# Fix #7: Per-IP rate limiting — token bucket, 60 req/min per IP by default
_RATE_LIMIT_RPM = int(os.environ.get("TOKENPAK_RATE_LIMIT_RPM", "60"))
_rate_buckets: dict = {}
_rate_bucket_lock = threading.Lock()

def _rate_limit_check(client_ip: str) -> bool:
    """Return True if request is ALLOWED. False = throttle (429)."""
    if _RATE_LIMIT_RPM <= 0:
        return True  # disabled
    now = time.time()
    with _rate_bucket_lock:
        if client_ip not in _rate_buckets:
            _rate_buckets[client_ip] = {"tokens": float(_RATE_LIMIT_RPM), "last_refill": now}
        bucket = _rate_buckets[client_ip]
        elapsed = now - bucket["last_refill"]
        refill = elapsed * (_RATE_LIMIT_RPM / 60.0)
        bucket["tokens"] = min(float(_RATE_LIMIT_RPM), bucket["tokens"] + refill)
        bucket["last_refill"] = now
        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            return True
        return False


def _ollama_health_loop():
    """Background thread: ping ollama upstream every 30s.
    Pre-opens circuit if unreachable so requests fail instantly."""
    from urllib.parse import urlparse
    parsed = urlparse(OLLAMA_UPSTREAM)
    host = parsed.hostname
    port = parsed.port or 11434
    check_interval = 30  # seconds between checks
    
    # Initial check on startup
    time.sleep(0.5)  # let proxy finish starting
    
    while True:
        try:
            probe = socket.create_connection((host, port), timeout=5)
            probe.close()
            with _ollama_circuit_lock:
                was_open = _ollama_circuit["open"]
                _ollama_circuit["open"] = False
            if was_open:
                print(f"  \u2705 Ollama upstream {host}:{port} is back online")
        except (socket.timeout, OSError, ConnectionRefusedError):
            with _ollama_circuit_lock:
                was_open = _ollama_circuit["open"]
                _ollama_circuit["open"] = True
                _ollama_circuit["last_failure"] = time.time()
            if not was_open:
                print(f"  \u26a0\ufe0f Ollama upstream {host}:{port} unreachable — circuit opened")
        
        time.sleep(check_interval)


# Start health checker thread
_ollama_health_thread = threading.Thread(target=_ollama_health_loop, daemon=True)
_ollama_health_thread.start()

# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------
try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))
except ImportError:
    def count_tokens(text: str) -> int:
        return len(text) // 4


# ---------------------------------------------------------------------------
# Two-Tier Vault Index (Read-Only)
# ---------------------------------------------------------------------------
class VaultIndex:
    """
    Read-only BM25-searchable index loaded from .tokenpak/index.json + blocks/.
    Reloads periodically to pick up git-pulled changes.
    """

    def __init__(self, tokenpak_dir: str):
        self.tokenpak_dir = Path(tokenpak_dir)
        self.blocks: Dict[str, dict] = {}  # block_id -> {meta + content}
        self._last_loaded = 0
        self._last_mtime = 0
        self._lock = threading.Lock()
        # BM25 precomputed
        self._df: Dict[str, int] = {}
        self._block_tfs: Dict[str, Dict[str, int]] = {}
        self._avg_dl: float = 0
        self._doc_count: int = 0

    @property
    def available(self) -> bool:
        return len(self.blocks) > 0

    def maybe_reload(self):
        """Reload if index file changed or enough time passed."""
        now = time.time()
        if now - self._last_loaded < VAULT_INDEX_RELOAD_INTERVAL:
            return

        index_path = self.tokenpak_dir / "index.json"
        if not index_path.exists():
            return

        try:
            mtime = index_path.stat().st_mtime
            if mtime == self._last_mtime and self.blocks:
                self._last_loaded = now
                return
        except OSError:
            return

        self._load(index_path, mtime)
        self._last_loaded = now

    def _load(self, index_path: Path, mtime: float):
        """Load index + block contents, precompute BM25 stats."""
        try:
            data = json.loads(index_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠️ Vault index load error: {e}")
            return

        blocks_dir = self.tokenpak_dir / "blocks"
        new_blocks: Dict[str, dict] = {}

        raw_blocks = data.get("blocks", {})
        if isinstance(raw_blocks, dict):
            items = raw_blocks.items()
        else:
            return  # unexpected format

        for bid, bdata in items:
            content = ""
            content_file = blocks_dir / f"{bid}.txt"
            if content_file.exists():
                try:
                    content = content_file.read_text(errors="replace")
                except OSError:
                    continue

            new_blocks[bid] = {
                "block_id": bid,
                "source_path": bdata.get("source_path", bid),
                "risk_class": bdata.get("risk_class", "narrative"),
                "must_keep": bdata.get("must_keep", False),
                "raw_tokens": bdata.get("raw_tokens", 0),
                "content": content,
            }

        # Precompute BM25
        df: Dict[str, int] = {}
        block_tfs: Dict[str, Dict[str, int]] = {}
        total_dl = 0

        for bid, block in new_blocks.items():
            terms = _bm25_tokenize(block["content"])
            tf: Dict[str, int] = {}
            for t in terms:
                tf[t] = tf.get(t, 0) + 1
            block_tfs[bid] = tf
            total_dl += len(terms)
            for t in set(terms):
                df[t] = df.get(t, 0) + 1

        doc_count = len(new_blocks)
        avg_dl = total_dl / doc_count if doc_count > 0 else 0

        with self._lock:
            self.blocks = new_blocks
            self._df = df
            self._block_tfs = block_tfs
            self._avg_dl = avg_dl
            self._doc_count = doc_count
            self._last_mtime = mtime

        print(f"  📚 Vault index loaded: {doc_count} blocks, {sum(b['raw_tokens'] for b in new_blocks.values()):,} tokens")

    def search(self, query: str, top_k: int = 5, min_score: float = 2.0) -> List[Tuple[dict, float]]:
        """BM25 search across vault blocks. Returns [(block_dict, score), ...]."""
        query_terms = _bm25_tokenize(query)
        if not query_terms or not self.blocks:
            return []

        with self._lock:
            df = self._df
            block_tfs = self._block_tfs
            avg_dl = self._avg_dl
            doc_count = self._doc_count
            blocks = self.blocks

        k1 = 1.5
        b_param = 0.75
        scores: Dict[str, float] = {}

        for bid in blocks:
            tf = block_tfs.get(bid, {})
            dl = sum(tf.values())
            score = 0.0
            for qt in query_terms:
                if qt not in df:
                    continue
                idf = math.log((doc_count - df[qt] + 0.5) / (df[qt] + 0.5) + 1)
                term_freq = tf.get(qt, 0)
                if term_freq == 0:
                    continue
                numerator = term_freq * (k1 + 1)
                denominator = term_freq + k1 * (1 - b_param + b_param * dl / avg_dl)
                score += idf * numerator / denominator
            if score >= min_score:
                scores[bid] = score

        # Sort deterministically: score desc, then path asc, then block_id asc
        # This ensures byte-identical ordering for cache stability even on score ties
        ranked = sorted(
            scores.items(),
            key=lambda x: (-x[1], blocks[x[0]].get("source_path", ""), x[0]),
        )[:top_k]
        return [(blocks[bid], score) for bid, score in ranked]

    def compile_injection(self, query: str, budget: int = 4000, top_k: int = 5, min_score: float = 2.0) -> Tuple[str, int, List[str]]:
        """
        Search vault and compile injection text within budget.
        Returns (injection_text, tokens_used, source_refs).
        """
        results = self.search(query, top_k=top_k, min_score=min_score)
        if not results:
            return "", 0, []

        injection_parts = []
        tokens_used = 0
        source_refs = []

        for block, score in results:
            content = block["content"]
            block_tokens = block["raw_tokens"]

            # Budget check
            remaining = budget - tokens_used
            if remaining <= 100:
                break

            # Truncate if needed
            if block_tokens > remaining:
                # Rough char-to-token truncation
                char_limit = remaining * 4
                content = content[:char_limit].rsplit("\n", 1)[0]
                block_tokens = count_tokens(content)

            source_path = block["source_path"]
            injection_parts.append(f"--- [{source_path}] (relevance: {score:.1f}) ---\n{content}")
            tokens_used += block_tokens
            source_refs.append(source_path)

        if not injection_parts:
            return "", 0, []

        header = "\n\n## Retrieved Context\n"  # fixed header for cache stability
        injection_text = header + "\n\n".join(injection_parts)
        # Recount with header
        tokens_used = count_tokens(injection_text)

        return injection_text, tokens_used, source_refs


# BM25 tokenizer
def _bm25_tokenize(text: str) -> List[str]:
    return re.findall(r'[a-z0-9_]+', text.lower())


# Global vault index instance
VAULT_INDEX = VaultIndex(VAULT_INDEX_PATH)

# Global capsule builder instance
try:
    from tokenpak.capsule.builder import CapsuleBuilder as _CapsuleBuilder
    CAPSULE_BUILDER = _CapsuleBuilder(
        enabled=ENABLE_CAPSULE_BUILDER,
        min_block_chars=CAPSULE_MIN_CHARS,
        hot_window=CAPSULE_HOT_WINDOW,
    )
    print(f"  💊 Capsule builder loaded (enabled={ENABLE_CAPSULE_BUILDER}, min_chars={CAPSULE_MIN_CHARS})")
except ImportError as _cb_err:
    CAPSULE_BUILDER = None
    print(f"  ⚠️  Capsule builder unavailable: {_cb_err}")


# ---------------------------------------------------------------------------
# Skeleton extraction — strips function bodies from code blocks before injection
# Reduces code-heavy vault blocks by 70-90% (signatures + docstrings only)
# ---------------------------------------------------------------------------
def _skeletonize_block(content: str, file_ext: str) -> str:
    """Apply skeleton extraction to a code block if the language is supported."""
    if not SKELETON_ENABLED:
        return content
    lang_map = {
        ".py": "python", ".ts": "typescript", ".js": "javascript",
        ".go": "go", ".rs": "rust",
    }
    lang = lang_map.get(file_ext.lower(), "")
    if not lang:
        return content
    try:
        sys.path.insert(0, str(Path.home() / "vault" / "Projects" / "ocp-protocol" / "packages" / "pypi"))
        from tokenpak.skeleton_extractor import extract_skeleton
        return extract_skeleton(content, lang)
    except Exception:
        return content


def _inject_skeleton_into_blocks(blocks_text: str) -> str:
    """Walk a multi-block injection string and skeletonize code blocks."""
    if not SKELETON_ENABLED or not blocks_text:
        return blocks_text
    def _replace_fence(m):
        lang_hint = m.group(1).strip().lower()
        ext_map = {"python": ".py", "py": ".py", "typescript": ".ts", "ts": ".ts",
                   "javascript": ".js", "js": ".js", "go": ".go", "rust": ".rs"}
        ext = ext_map.get(lang_hint, "")
        code = m.group(2)
        skeletonized = _skeletonize_block(code, ext) if ext else code
        return f"```{m.group(1)}\n{skeletonized}\n```"
    return re.sub(r"```([^\n]*)\n(.*?)```", _replace_fence, blocks_text, flags=re.DOTALL)


# ---------------------------------------------------------------------------
# Shadow reader validation — coherence-check compressed output
# ---------------------------------------------------------------------------
def _shadow_validate(original: str, compressed: str) -> bool:
    """Returns True if compressed text passes coherence check, False = use original."""
    if not SHADOW_ENABLED:
        return True
    if not compressed or not original:
        return True
    try:
        sys.path.insert(0, str(Path.home() / "vault" / "Projects" / "ocp-protocol" / "packages" / "pypi"))
        from tokenpak.shadow_reader import ShadowReader
        reader = ShadowReader()
        result = reader.validate(original=original, compressed=compressed)
        return result.passed
    except Exception:
        return True  # fail-open: if shadow reader errors, allow compressed version


# ---------------------------------------------------------------------------
# Budget controller — enforce per-bucket token limits
# ---------------------------------------------------------------------------
def _apply_budget(components: dict, total_tokens: int = None) -> dict:
    """Apply Budgeter allocation policy to context components."""
    total = total_tokens or BUDGET_TOTAL_TOKENS
    try:
        sys.path.insert(0, str(Path.home() / "vault" / "Projects" / "ocp-protocol" / "packages" / "pypi"))
        from tokenpak.budgeter import Budgeter
        b = Budgeter()
        return b.allocate(components, total_tokens=total)
    except Exception:
        return components  # fail-open


# ---------------------------------------------------------------------------
# Router wiring — DeterministicRouter integration (feature-flagged)
# ---------------------------------------------------------------------------
_ROUTER_INSTANCE = None
_ROUTER_LOCK = threading.Lock()


def _get_router():
    """Return the DeterministicRouter singleton, or None if unavailable/disabled."""
    global _ROUTER_INSTANCE
    if not ROUTER_ENABLED:
        return None
    with _ROUTER_LOCK:
        if _ROUTER_INSTANCE is None:
            try:
                sys.path.insert(0, str(Path.home() / "vault" / "Projects" / "ocp-protocol" / "packages" / "pypi"))
                from tokenpak.agent.compression.pipeline import CompressionPipeline
                from tokenpak.agent.compression.slot_filler import SlotFiller
                from tokenpak.agent.compression.recipes import RecipeEngine
                from tokenpak.agent.proxy.intent_policy import decide as _policy_decide
                try:
                    from tokenpak.validation_gate import ValidationGate
                except ImportError:
                    ValidationGate = None  # type: ignore[assignment,misc]

                class _DeterministicRouter:
                    """Classifier-first router: intent → slots → deterministic recipe/action."""
                    def __init__(self):
                        self._pipeline = CompressionPipeline()
                        self._slot_filler = SlotFiller()
                        self._recipe_engine = RecipeEngine()
                        self._gate = (
                            ValidationGate(enabled=VALIDATION_GATE_ENABLED, token_budget_cap=VALIDATION_GATE_BUDGET_CAP)
                            if ValidationGate is not None and _has_validation_gate() and VALIDATION_GATE_ENABLED
                            else None
                        )

                    def route(self, user_text: str, session_id: str = "") -> "_RouterResult":
                        t0 = time.time()
                        try:
                            # Phase 1: Classify intent
                            intent = _classify_intent(user_text)

                            # Phase 2: Fill slots for this intent
                            filled = self._slot_filler.fill(intent, user_text)

                            # Phase 3: Deterministic policy decision (intent + slots → recipe + action)
                            decision = _policy_decide(intent, filled.slots, filled.confidence)

                            # Phase 4: Compress via pipeline (skipped for low-cost intents)
                            compressed = user_text
                            if decision.action.compress:
                                msgs = [{"role": "user", "content": user_text}]
                                pipeline_result = self._pipeline.run(msgs)
                                if pipeline_result.messages:
                                    compressed = pipeline_result.messages[-1].get("content", user_text)

                            elapsed = int((time.time() - t0) * 1000)
                            return _RouterResult(
                                ok=True,
                                fallback=decision.fallback,
                                intent=decision.intent,
                                recipe_id=decision.recipe_id,
                                slots=decision.slots_used,
                                elapsed_ms=elapsed,
                                compressed_text=compressed,
                                capsule=None,
                                fallback_reason=decision.fallback_reason,
                            )
                        except Exception as e:
                            elapsed = int((time.time() - t0) * 1000)
                            return _RouterResult(
                                ok=False, fallback=True,
                                intent="unknown", recipe_id="pipeline-v1",
                                slots={}, elapsed_ms=elapsed,
                                compressed_text="", capsule=None,
                                error=str(e),
                                fallback_reason=f"exception:{type(e).__name__}",
                            )

                _ROUTER_INSTANCE = _DeterministicRouter()
            except Exception as _router_init_err:
                print(f"  ⚠️ Router init failed: {_router_init_err}")
                return None
        return _ROUTER_INSTANCE


_VALIDATION_GATE_INSTANCE = None
_VALIDATION_GATE_LOCK = threading.Lock()


def _has_validation_gate() -> bool:
    try:
        from tokenpak.validation_gate import ValidationGate  # noqa
        return True
    except Exception:
        return False


def _get_validation_gate():
    global _VALIDATION_GATE_INSTANCE
    if not VALIDATION_GATE_ENABLED:
        return None
    with _VALIDATION_GATE_LOCK:
        if _VALIDATION_GATE_INSTANCE is None:
            try:
                from tokenpak.validation_gate import ValidationGate
                _VALIDATION_GATE_INSTANCE = ValidationGate(
                    enabled=True,
                    token_budget_cap=VALIDATION_GATE_BUDGET_CAP,
                )
            except Exception:
                return None
        return _VALIDATION_GATE_INSTANCE


class _RouterResult:
    """Lightweight result object from router.route()."""
    def __init__(self, ok, fallback, intent, recipe_id, slots, elapsed_ms,
                 compressed_text="", capsule=None, error="", fallback_reason=""):
        self.ok = ok
        self.fallback = fallback
        self.intent = intent
        self.recipe_id = recipe_id
        self.slots = slots
        self.elapsed_ms = elapsed_ms
        self.compressed_text = compressed_text
        self.capsule = capsule
        self.error = error
        self.fallback_reason = fallback_reason


def _classify_intent(text: str) -> str:
    """Keyword-based intent classification — canonical intent set.

    Priority order matters: more specific checks run first.
    Returns one of: status, usage, execute, debug, summarize, plan,
                    explain, search, create, query (fallback).
    """
    t = text.lower()
    # status — health/liveness checks (check before debug to avoid "error" overlap)
    if any(k in t for k in ("status", "health", "is it running", "is it up", "ping",
                              "uptime", "alive", "reachable", "available")):
        return "status"
    # usage — cost/token analytics (check before search/query)
    if any(k in t for k in ("usage", "cost", "spend", "how much", "token count",
                              "billing", "how many tokens")):
        return "usage"
    # execute — imperative run/deploy/start commands
    if any(k in t for k in ("run ", "execute", "start ", "deploy", "launch", "trigger",
                              "kick off", "fire")):
        return "execute"
    # debug — error diagnosis
    if any(k in t for k in ("fix", "debug", "error", "bug", "broken", "failing",
                              "exception", "traceback", "crash", "why is")):
        return "debug"
    # summarize — condensing content
    if any(k in t for k in ("summarize", "tldr", "brief", "recap", "summary",
                              "condense", "digest")):
        return "summarize"
    # plan — architecture / design / roadmap
    if any(k in t for k in ("plan", "design", "architect", "roadmap", "strategy",
                              "approach", "what should i", "how should i")):
        return "plan"
    # explain — knowledge / conceptual questions
    if any(k in t for k in ("explain", "what is", "how does", "describe", "tell me about",
                              "what does", "how do")):
        return "explain"
    # search — lookups and finding things
    if any(k in t for k in ("find", "search", "look up", "where", "locate", "which",
                              "list all")):
        return "search"
    # create — code / artifact generation
    if any(k in t for k in ("write", "create", "generate", "build", "implement",
                              "make a", "add a", "new ")):
        return "create"
    # query — safe catch-all fallback
    return "query"


def _extract_user_text(body_bytes: bytes) -> str:
    """Extract the last user message text from a request body."""
    try:
        data = json.loads(body_bytes)
    except Exception:
        return ""
    messages = data.get("messages", [])
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return " ".join(parts)
    return ""


def _run_router(body_bytes: bytes, session_id: str = "") -> Tuple[bytes, Optional[dict]]:
    """
    Run the DeterministicRouter on the request body.
    Returns (possibly-modified body, meta dict or None).
    """
    user_text = _extract_user_text(body_bytes)
    if not user_text:
        return body_bytes, None

    router = _get_router()
    if router is None:
        return body_bytes, None

    try:
        result = router.route(user_text, session_id=session_id)
        meta: Dict[str, Any] = {
            "intent": result.intent,
            "recipe_used": result.recipe_id,
            "fallback": result.fallback,
            "total_ms": result.elapsed_ms,
        }
        # Surface slot extraction for debugging and downstream consumers
        if result.slots:
            meta["slots"] = result.slots
        if hasattr(result, "fallback_reason") and result.fallback_reason:
            meta["fallback_reason"] = result.fallback_reason
        if hasattr(result, "error") and result.error:
            meta["error"] = result.error
        return body_bytes, meta
    except Exception as e:
        return body_bytes, {"fallback": True, "error": str(e), "intent": "unknown",
                            "recipe_used": "pipeline-v1", "total_ms": 0}


def _router_health() -> dict:
    """Return router health/status dict for the /health endpoint."""
    components = {
        "slot_filler": False,
        "recipe_engine": False,
        "validation_gate": False,
    }
    if not ROUTER_ENABLED:
        return {"enabled": False, "components": components}

    router = _get_router()
    if router is None:
        return {"enabled": True, "components": components}

    return {
        "enabled": True,
        "components": {
            "slot_filler": hasattr(router, "_slot_filler") and router._slot_filler is not None,
            "recipe_engine": hasattr(router, "_recipe_engine") and router._recipe_engine is not None,
            "validation_gate": hasattr(router, "_gate") and router._gate is not None,
        },
    }


# ---------------------------------------------------------------------------
# Style Contract: Protected content detection
# ---------------------------------------------------------------------------
PROTECTED_MARKERS = [
    "SOUL.md", "AGENTS.md", "IDENTITY.md", "USER.md", "TOOLS.md",
    "HEARTBEAT.md", "MEMORY.md", "BOOTSTRAP.md",
    "You are", "Your role is", "## Core Truths", "## Boundaries",
    "## Response Mode", "## Safety", "## Vibe",
    '"type": "function"', '"parameters":', '"required":',
    "## Runtime", "## Workspace Files", "## Silent Replies",
    "## Heartbeats", "## Messaging",
]

def is_protected_content(text: str) -> bool:
    if not text or len(text) < 50:
        return False
    marker_hits = sum(1 for m in PROTECTED_MARKERS if m in text)
    return marker_hits >= 2


def classify_message_risk(msg: dict) -> str:
    role = msg.get("role", "")
    content = msg.get("content", "")

    if isinstance(content, list):
        text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and "text" in p]
        content_text = "\n".join(text_parts)
    elif isinstance(content, str):
        content_text = content
    else:
        return "narrative"

    if role == "system":
        return "protected"
    if is_protected_content(content_text):
        return "protected"
    if role == "tool" or msg.get("type") == "tool_result":
        return "config"
    if "```" in content_text or content_text.count("    ") > 5:
        return "code"
    return "narrative"


def can_compress(risk_class: str, mode: str) -> bool:
    if mode == "strict":
        return False
    if risk_class == "protected":
        return False
    if mode == "hybrid":
        return risk_class == "narrative"
    return True


# ---------------------------------------------------------------------------
# SQLite monitor
# ---------------------------------------------------------------------------
import sqlite3

class Monitor:
    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                request_type TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                estimated_cost REAL,
                latency_ms INTEGER,
                status_code INTEGER,
                endpoint TEXT,
                compilation_mode TEXT,
                protected_tokens INTEGER,
                compressed_tokens INTEGER,
                injected_tokens INTEGER DEFAULT 0,
                injected_sources TEXT DEFAULT '',
                cache_read_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON requests(timestamp)")
        # Add columns if upgrading from v3
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN injected_tokens INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN injected_sources TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN cache_read_tokens INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN cache_creation_tokens INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        conn.commit()
        conn.close()

    def log(self, model, input_tokens, output_tokens, cost, latency_ms, status_code,
            endpoint, compilation_mode="", protected_tokens=0, compressed_tokens=0,
            injected_tokens=0, injected_sources="", cache_read_tokens=0, cache_creation_tokens=0):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """INSERT INTO requests
                   (timestamp,model,request_type,input_tokens,output_tokens,estimated_cost,
                    latency_ms,status_code,endpoint,compilation_mode,protected_tokens,
                    compressed_tokens,injected_tokens,injected_sources,cache_read_tokens,cache_creation_tokens)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now().isoformat(), model, "chat", input_tokens, output_tokens,
                 cost, latency_ms, status_code, endpoint, compilation_mode,
                 protected_tokens, compressed_tokens, injected_tokens, injected_sources,
                 cache_read_tokens, cache_creation_tokens)
            )
            conn.commit()
            conn.close()

    def get_stats(self, hours=24):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),
                   COALESCE(SUM(estimated_cost),0), COALESCE(AVG(latency_ms),0),
                   COALESCE(SUM(protected_tokens),0), COALESCE(SUM(compressed_tokens),0),
                   COALESCE(SUM(injected_tokens),0),
                   COALESCE(SUM(cache_read_tokens),0),
                   COALESCE(SUM(cache_creation_tokens),0)
            FROM requests WHERE timestamp >= datetime('now', ?)
        """, (f"-{hours} hours",)).fetchone()
        conn.close()
        return {
            "requests": row[0], "input_tokens": row[1], "output_tokens": row[2],
            "total_cost": round(row[3], 4), "avg_latency_ms": round(row[4], 0),
            "protected_tokens": row[5], "compressed_tokens": row[6],
            "injected_tokens": row[7],
            "cache_read_tokens": row[8],
            "cache_creation_tokens": row[9],
        }

    def get_by_model(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(estimated_cost),
                   SUM(cache_read_tokens), SUM(cache_creation_tokens)
            FROM requests GROUP BY model ORDER BY SUM(estimated_cost) DESC
        """).fetchall()
        conn.close()
        return {r[0]: {"requests": r[1], "input_tokens": r[2], "output_tokens": r[3], "cost": round(r[4],4),
                       "cache_read_tokens": r[5] or 0, "cache_creation_tokens": r[6] or 0} for r in rows}

    def recent(self, limit=20):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]


MONITOR = Monitor(MONITOR_DB)

# ---------------------------------------------------------------------------
# Session stats
# ---------------------------------------------------------------------------
SESSION = {
    "requests": 0,
    "input_tokens": 0,
    "sent_input_tokens": 0,
    "saved_tokens": 0,
    "protected_tokens": 0,
    "output_tokens": 0,
    "cost": 0.0,
    "cost_saved": 0.0,
    "start_time": time.time(),
    "errors": 0,
    "compilation_mode": COMPILATION_MODE,
    "injected_tokens": 0,
    "injection_hits": 0,
    "injection_skips": 0,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "cache_miss_reasons": {
        "timestamp_poison": 0,
        "uuid_request_id_poison": 0,
        "schema_tool_change": 0,
        "retrieval_order_drift_or_unknown": 0,
    },
    "canon_hits": 0,
    "canon_tokens_saved": 0,
    "ingest_entries": 0,
}

# ---------------------------------------------------------------------------
# Graceful Shutdown — SIGTERM/SIGINT drain support
# ---------------------------------------------------------------------------
_shutdown_event = threading.Event()
_active_request_count = 0
_active_request_lock = threading.Lock()
_active_requests_drained = threading.Event()

# ---------------------------------------------------------------------------
# Last Request Stats — captures most recent request for /stats/last
# ---------------------------------------------------------------------------
LAST_REQUEST = {
    "request_id": None,
    "timestamp": None,
    "model": None,
    "input_tokens_raw": 0,
    "input_tokens_sent": 0,
    "tokens_saved": 0,
    "percent_saved": 0.0,
    "cost_saved": 0.0,
    "output_tokens": 0,
}
_LAST_REQUEST_LOCK = threading.Lock()

def update_last_request(request_id: str, model: str, input_raw: int, input_sent: int, 
                       tokens_saved: int, cost_saved: float, output_tokens: int):
    """Thread-safe update of last request stats."""
    with _LAST_REQUEST_LOCK:
        LAST_REQUEST["request_id"] = request_id
        LAST_REQUEST["timestamp"] = datetime.now().isoformat()
        LAST_REQUEST["model"] = model
        LAST_REQUEST["input_tokens_raw"] = input_raw
        LAST_REQUEST["input_tokens_sent"] = input_sent
        LAST_REQUEST["tokens_saved"] = tokens_saved
        LAST_REQUEST["percent_saved"] = round(tokens_saved / input_raw * 100, 1) if input_raw > 0 else 0.0
        LAST_REQUEST["cost_saved"] = round(cost_saved, 6)
        LAST_REQUEST["output_tokens"] = output_tokens

# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------
MODEL_COSTS = {
    "claude-opus-4-5":    {"input": 15.0,  "output": 75.0},
    "claude-opus-4-6":    {"input": 15.0,  "output": 75.0},
    "claude-sonnet-4-5":  {"input": 3.0,   "output": 15.0},
    "claude-sonnet-4-6":  {"input": 3.0,   "output": 15.0},
    "claude-haiku-3-5":   {"input": 0.8,   "output": 4.0},
    "claude-haiku-4-5":   {"input": 0.8,   "output": 4.0},
    "gpt-4o":             {"input": 5.0,   "output": 15.0},
    "gpt-4o-mini":        {"input": 0.15,  "output": 0.6},
    "gpt-5.2-codex":      {"input": 2.0,   "output": 8.0},
    "gpt-5.3-codex":      {"input": 2.0,   "output": 8.0},
    "gpt-5.3-codex-spark":{"input": 0.5,   "output": 2.0},
    "gpt-5.1-codex-mini": {"input": 0.5,   "output": 2.0},
    "gemini-2-flash":     {"input": 0.1,   "output": 0.4},
    "gemini-3-pro-preview":{"input": 1.25, "output": 5.0},
    "gemini-3-flash-preview": {"input": 0.1, "output": 0.4},
}

def estimate_cost(model, input_tokens, output_tokens, cache_read=0, cache_creation=0):
    for key, costs in MODEL_COSTS.items():
        if key in model.lower():
            regular_input = max(0, input_tokens - cache_read - cache_creation)
            return (regular_input * costs["input"] +
                    cache_read * costs["input"] * 0.1 +
                    cache_creation * costs["input"] * 1.25 +
                    output_tokens * costs["output"]) / 1_000_000
    regular_input = max(0, input_tokens - cache_read - cache_creation)
    return (regular_input * 3.0 +
            cache_read * 3.0 * 0.1 +
            cache_creation * 3.0 * 1.25 +
            output_tokens * 15.0) / 1_000_000


def _header_mapping(headers: Any) -> Dict[str, str]:
    """
    Build a plain dict from BaseHTTPRequestHandler headers.
    """
    result: Dict[str, str] = {}
    try:
        for key in headers:
            result[str(key)] = str(headers[key])
    except Exception:
        pass
    return result


def _detect_adapter(path: str, headers: Mapping[str, str], body_bytes: Optional[bytes] = None) -> FormatAdapter:
    return ADAPTER_REGISTRY.detect(path=path, headers=headers, body=body_bytes)


def extract_request_tokens(body_bytes: bytes, adapter: Optional[FormatAdapter] = None) -> Tuple[str, int]:
    try:
        active_adapter = adapter or _detect_adapter("", {}, body_bytes)
        return active_adapter.extract_request_tokens(body_bytes, token_counter=count_tokens)
    except Exception:
        return "unknown", 0


def extract_response_tokens(body_bytes: bytes, adapter: Optional[FormatAdapter] = None, is_sse: bool = False) -> int:
    try:
        active_adapter = adapter or _detect_adapter("", {}, body_bytes)
        return active_adapter.extract_response_tokens(body_bytes, is_sse=is_sse)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Context Injection: Extract query signal from request
# ---------------------------------------------------------------------------
def extract_query_signal(body_bytes: bytes, adapter: Optional[FormatAdapter] = None) -> str:
    """
    Extract a search query from the request to find relevant vault context.
    Uses the last user message + any recent assistant context as signal.
    """
    try:
        active_adapter = adapter or _detect_adapter("", {}, body_bytes)
        return active_adapter.extract_query_signal(body_bytes)
    except Exception:
        return ""


def inject_vault_context(body_bytes: bytes, adapter: Optional[FormatAdapter] = None) -> Tuple[bytes, int, List[str]]:
    """
    Search vault index for relevant context and inject into the system prompt.
    Returns (new_body_bytes, injected_tokens, source_refs).
    """
    if not VAULT_INDEX.available:
        return body_bytes, 0, []

    active_adapter = adapter or _detect_adapter("", {}, body_bytes)
    query = extract_query_signal(body_bytes, adapter=active_adapter)
    if not query:
        return body_bytes, 0, []

    injection_text, tokens_used, source_refs = VAULT_INDEX.compile_injection(
        query, budget=INJECT_BUDGET, top_k=INJECT_TOP_K, min_score=INJECT_MIN_SCORE
    )

    if not injection_text:
        return body_bytes, 0, []

    # Apply skeleton extraction to code blocks in injection text (70-90% reduction on code)
    if SKELETON_ENABLED:
        injection_text = _inject_skeleton_into_blocks(injection_text)
        tokens_used = count_tokens(injection_text)

    try:
        new_body = active_adapter.inject_system_context(body_bytes, injection_text)
    except Exception:
        return body_bytes, 0, []
    return new_body, tokens_used, source_refs


# ---------------------------------------------------------------------------
# Compaction with style contracts
# ---------------------------------------------------------------------------
def compact_text(text: str) -> str:
    if not text:
        return text
    key = str(hash(text))
    if key in _COMPACT_CACHE:
        return _COMPACT_CACHE[key]
    t = " ".join(text.split())
    m = re.search(r'[.!?](?:\s|$)', t)
    if m:
        t = t[:m.end()].strip()
    if len(t) > COMPACT_MAX_CHARS:
        t = t[:COMPACT_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    # Shadow reader guard: if compressed text fails coherence check, return original
    if SHADOW_ENABLED and COMPILATION_MODE == "aggressive" and not _shadow_validate(text, t):
        t = text  # fall back to original — coherence check failed
    _COMPACT_CACHE[key] = t
    _COMPACT_CACHE_ORDER.append(key)
    if len(_COMPACT_CACHE_ORDER) > COMPACT_CACHE_SIZE:
        old = _COMPACT_CACHE_ORDER.pop(0)
        _COMPACT_CACHE.pop(old, None)
    return t


_UUID_PATTERN = re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.IGNORECASE)
_TIMESTAMP_PATTERN = re.compile(
    r'\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b'
)
_HEARTBEAT_COUNTER = re.compile(r'Heartbeat\s*#?\s*\d+', re.IGNORECASE)

def _strip_cache_poisons(body_bytes: bytes) -> bytes:
    """
    Strip dynamic content that breaks prompt cache hits:
    - ISO timestamps embedded in prompts (e.g. "Current time: 2026-03-09T17:00:00Z")
    - UUIDs embedded in prompts (e.g. "request_id: a1b2c3d4-...")
    - Heartbeat counters (e.g. "Heartbeat #1287")
    Only strips from message content strings, not from metadata fields.
    Fails open — returns original body if any error occurs.
    """
    try:
        data = json.loads(body_bytes)
        changed = False

        def _scrub(text: str) -> str:
            nonlocal changed
            original = text
            text = _UUID_PATTERN.sub("[id]", text)
            text = _TIMESTAMP_PATTERN.sub("[time]", text)
            text = _HEARTBEAT_COUNTER.sub("Heartbeat", text)
            if text != original:
                changed = True
            return text

        def _scrub_content(content):
            if isinstance(content, str):
                return _scrub(content)
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part["text"] = _scrub(part["text"])
            return content

        # Scrub message content
        for msg in data.get("messages", []):
            if isinstance(msg, dict):
                msg["content"] = _scrub_content(msg.get("content", ""))

        # Scrub system prompt (only text parts, not cache_control blocks)
        system = data.get("system")
        if isinstance(system, str):
            data["system"] = _scrub(system)
        elif isinstance(system, list):
            for part in system:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = _scrub(part["text"])

        if changed:
            return json.dumps(data, ensure_ascii=False).encode("utf-8")
        return body_bytes
    except Exception:
        return body_bytes  # fail-open


def _classify_cache_miss_reason(raw_body: Optional[bytes], cache_poison_scrubbed: bool, tools_schema_changed: bool, final_body: Optional[bytes]) -> str:
    """Best-effort classifier for cache misses."""
    if tools_schema_changed:
        return "schema_tool_change"

    raw_text = ""
    if raw_body:
        try:
            raw_text = raw_body.decode("utf-8", errors="ignore")
        except Exception:
            raw_text = ""

    if cache_poison_scrubbed:
        if _TIMESTAMP_PATTERN.search(raw_text):
            return "timestamp_poison"
        if _UUID_PATTERN.search(raw_text) or re.search(r"\brequest[_-]?id\b", raw_text, re.IGNORECASE):
            return "uuid_request_id_poison"
        return "timestamp_poison"

    if raw_body and final_body and raw_body != final_body:
        return "retrieval_order_drift_or_unknown"

    return "retrieval_order_drift_or_unknown"


def _build_cache_stats_payload() -> Dict[str, Any]:
    hits = int(SESSION.get("cache_hits", 0) or 0)
    misses = int(SESSION.get("cache_misses", 0) or 0)
    total = hits + misses
    hit_rate = (hits / total) if total > 0 else 0.0
    miss_reasons = dict(SESSION.get("cache_miss_reasons", {}))
    return {
        "hit_rate": round(hit_rate, 4),
        "cache_read_tokens": int(SESSION.get("cache_read_tokens", 0) or 0),
        "cache_creation_tokens": int(SESSION.get("cache_creation_tokens", 0) or 0),
        "cache_hits": hits,
        "cache_misses": misses,
        "total_cache_decisions": total,
        "miss_reasons": miss_reasons,
    }


def compact_request_body(body_bytes: bytes, adapter: Optional[FormatAdapter] = None):
    """
    Style-contract-aware compaction.
    Returns (new_body_bytes, sent_tokens, original_tokens, protected_token_count).
    """
    active_adapter = adapter or _detect_adapter("", {}, body_bytes)
    if active_adapter.source_format == "passthrough":
        model, tokens = extract_request_tokens(body_bytes, adapter=active_adapter)
        _ = model
        return body_bytes, tokens, tokens, 0

    try:
        canonical = active_adapter.normalize(body_bytes)
    except Exception:
        return body_bytes, 0, 0, 0

    _, original_tokens = extract_request_tokens(body_bytes, adapter=active_adapter)
    if original_tokens < COMPACT_THRESHOLD_TOKENS:
        return body_bytes, original_tokens, original_tokens, 0

    mode = COMPILATION_MODE
    if mode == "strict":
        return body_bytes, original_tokens, original_tokens, original_tokens

    protected_tokens = 0

    if isinstance(canonical.system, str):
        protected_tokens += count_tokens(canonical.system)
    elif isinstance(canonical.system, list):
        for part in canonical.system:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                protected_tokens += count_tokens(part["text"])

    messages = canonical.messages
    keep_from = max(0, len(messages) - 2)
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            last_user_idx = i

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        if i >= keep_from:
            risk = classify_message_risk(msg)
            if risk == "protected":
                content = msg.get("content", "")
                if isinstance(content, str):
                    protected_tokens += count_tokens(content)
                elif isinstance(content, list):
                    for p in content:
                        if isinstance(p, dict) and "text" in p:
                            protected_tokens += count_tokens(p["text"])
            continue
        if msg.get("role") == "user" and i == last_user_idx:
            continue

        risk = classify_message_risk(msg)
        if not can_compress(risk, mode):
            content = msg.get("content", "")
            if isinstance(content, str):
                protected_tokens += count_tokens(content)
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict) and "text" in p:
                        protected_tokens += count_tokens(p["text"])
            continue

        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = compact_text(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = compact_text(part["text"])

    try:
        new_body = active_adapter.denormalize(canonical)
    except Exception:
        return body_bytes, original_tokens, original_tokens, protected_tokens
    _, sent_tokens = extract_request_tokens(new_body, adapter=active_adapter)
    return new_body, sent_tokens, original_tokens, protected_tokens


# ---------------------------------------------------------------------------
# SSE stream parsing
# ---------------------------------------------------------------------------
def _extract_sse_tokens(sse_bytes):
    result = {"output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    try:
        text = sse_bytes.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                continue
            try:
                event = json.loads(data_str)
            except:
                continue
            if event.get("type") == "message_start":
                usage = event.get("message", {}).get("usage", {})
                if "cache_read_input_tokens" in usage:
                    result["cache_read_input_tokens"] = usage["cache_read_input_tokens"]
                if "cache_creation_input_tokens" in usage:
                    result["cache_creation_input_tokens"] = usage["cache_creation_input_tokens"]
            if event.get("type") == "message_delta":
                usage = event.get("usage", {})
                if "output_tokens" in usage:
                    result["output_tokens"] = usage["output_tokens"]
            if "usage" in event and "completion_tokens" in event.get("usage", {}):
                result["output_tokens"] = event["usage"]["completion_tokens"]
    except Exception as e:
        print(f"  ⚠️ SSE parse error: {e}")
    return result


# ---------------------------------------------------------------------------
# Forward Proxy Handler
# ---------------------------------------------------------------------------
class ForwardProxyHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def do_CONNECT(self):
        host, _, port = self.path.partition(":")
        port = int(port) if port else 443
        self._tunnel_connect(host, port)

    def _tunnel_connect(self, host, port):
        try:
            remote = socket.create_connection((host, port), timeout=30)
        except Exception as e:
            self.send_error(502, f"Cannot connect to {host}:{port}: {e}")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self.connection.setblocking(False)
        remote.setblocking(False)
        timeout = 120
        last_activity = time.time()
        while time.time() - last_activity < timeout:
            data_moved = False
            try:
                data = self.connection.recv(65536)
                if data:
                    remote.sendall(data)
                    last_activity = time.time()
                    data_moved = True
                elif data == b"":
                    break
            except BlockingIOError:
                pass
            except:
                break
            try:
                data = remote.recv(65536)
                if data:
                    self.connection.sendall(data)
                    last_activity = time.time()
                    data_moved = True
                elif data == b"":
                    break
            except BlockingIOError:
                pass
            except:
                break
            if not data_moved:
                time.sleep(0.01)
        remote.close()

    def do_GET(self):
        if self.path == "/health":
            vault_info = {
                "available": VAULT_INDEX.available,
                "blocks": len(VAULT_INDEX.blocks),
                "path": str(VAULT_INDEX.tokenpak_dir),
            }
            router_info = _router_health()
            self._send_json({
                "status": "ok",
                "compilation_mode": COMPILATION_MODE,
                "vault_index": vault_info,
                "router": {"enabled": ROUTER_ENABLED, **router_info},
                "capsule_available": CAPSULE_BUILDER is not None,
                "canon": {"enabled": CANON_AVAILABLE, "session_hits": SESSION.get("canon_hits", 0)},
                "skeleton": {"enabled": SKELETON_ENABLED},
                "shadow_reader": {"enabled": SHADOW_ENABLED},
                "budget": {"enabled": True, "total_tokens": BUDGET_TOTAL_TOKENS},
                "tool_schema_registry": {
                    "enabled": TOOL_REGISTRY_AVAILABLE,
                    **((_get_tool_registry().stats() if _get_tool_registry() else {}) if TOOL_REGISTRY_AVAILABLE else {}),
                },
                "cache_poison_removal": {"enabled": True},
                "strict_validation": {"enabled": STRICT_VALIDATION},
                "upstream_timeout_seconds": UPSTREAM_TIMEOUT,
                "circuit_breakers": {p: {"open": cb["open"], "failures": cb["failures"]} for p, cb in _provider_circuits.items()},
                "stats": SESSION,
            })
            return
        if self.path == "/stats":
            self._send_json({
                "session": SESSION,
                "compilation_mode": COMPILATION_MODE,
                "vault_index": {
                    "available": VAULT_INDEX.available,
                    "blocks": len(VAULT_INDEX.blocks),
                },
                "router": {"enabled": ROUTER_ENABLED},
                "capsule_available": CAPSULE_BUILDER is not None,
                "canon": {
                    "enabled": CANON_AVAILABLE,
                    "session_hits": SESSION.get("canon_hits", 0),
                    "tokens_saved": SESSION.get("canon_tokens_saved", 0),
                },
                "skeleton": {"enabled": SKELETON_ENABLED},
                "shadow_reader": {"enabled": SHADOW_ENABLED},
                "budget": {"enabled": True, "total_tokens": BUDGET_TOTAL_TOKENS},
                "today": MONITOR.get_stats(),
                "by_model": MONITOR.get_by_model(),
                "recent": MONITOR.recent(10),
            })
            return
        if self.path == "/cache-stats":
            self._send_json(_build_cache_stats_payload())
            return
        if self.path == "/recent":
            self._send_json({"recent": MONITOR.recent(50)})
            return
        if self.path == "/stats/last":
            # Per-request stats for the most recent request
            with _LAST_REQUEST_LOCK:
                if LAST_REQUEST["request_id"] is None:
                    self._send_json({
                        "error": "no_requests",
                        "message": "No requests captured yet. Send a message to see stats.",
                    })
                else:
                    self._send_json({
                        "request_id": LAST_REQUEST["request_id"],
                        "timestamp": LAST_REQUEST["timestamp"],
                        "model": LAST_REQUEST["model"],
                        "tokens_saved": LAST_REQUEST["tokens_saved"],
                        "percent_saved": LAST_REQUEST["percent_saved"],
                        "cost_saved": LAST_REQUEST["cost_saved"],
                        "session_total_saved": round(SESSION["cost_saved"], 4),
                        "session_requests": SESSION["requests"],
                        "input_tokens_raw": LAST_REQUEST["input_tokens_raw"],
                        "input_tokens_sent": LAST_REQUEST["input_tokens_sent"],
                        "output_tokens": LAST_REQUEST["output_tokens"],
                    })
            return
        if self.path == "/stats/session":
            # Session aggregates
            uptime_hours = round((time.time() - SESSION["start_time"]) / 3600, 2)
            self._send_json({
                "session_requests": SESSION["requests"],
                "session_total_saved": round(SESSION["cost_saved"], 4),
                "tokens_saved": SESSION["saved_tokens"],
                "tokens_sent": SESSION["sent_input_tokens"],
                "tokens_raw": SESSION["input_tokens"],
                "output_tokens": SESSION["output_tokens"],
                "total_cost": round(SESSION["cost"], 4),
                "uptime_hours": uptime_hours,
                "errors": SESSION["errors"],
                "avg_savings_pct": round(SESSION["saved_tokens"] / SESSION["input_tokens"] * 100, 1) if SESSION["input_tokens"] > 0 else 0.0,
            })
            return
        if self.path == "/vault":
            # Debug endpoint: show vault index state
            blocks_info = []
            for bid, block in VAULT_INDEX.blocks.items():
                blocks_info.append({
                    "block_id": bid,
                    "source_path": block["source_path"],
                    "risk_class": block["risk_class"],
                    "raw_tokens": block["raw_tokens"],
                })
            self._send_json({
                "available": VAULT_INDEX.available,
                "blocks": len(VAULT_INDEX.blocks),
                "total_tokens": sum(b["raw_tokens"] for b in VAULT_INDEX.blocks.values()),
                "path": str(VAULT_INDEX.tokenpak_dir),
                "block_list": blocks_info,
            })
            return
        if self.path == "/trace/last":
            trace = TRACE_STORAGE.get_last()
            if trace:
                self._send_json(trace.to_dict())
            else:
                self._send_json({"error": "no traces", "message": "No requests captured yet. Send a message to see the pipeline in action."})
            return
        if self.path.startswith("/trace/"):
            # /trace/{request_id}
            request_id = self.path.split("/trace/")[1]
            trace = TRACE_STORAGE.get_by_id(request_id)
            if trace:
                self._send_json(trace.to_dict())
            else:
                self._send_json({"error": "not found", "message": f"No trace found for request_id: {request_id}"})
            return
        if self.path == "/traces":
            traces = TRACE_STORAGE.get_all()
            self._send_json({"traces": [t.to_dict() for t in traces], "count": len(traces)})
            return
        if self.path == "/metrics":
            # Fix #3: Prometheus metrics export
            s = SESSION
            uptime = int(time.time() - s.get("start_time", time.time()))
            lines = [
                "# HELP tokenpak_requests_total Total requests processed",
                "# TYPE tokenpak_requests_total counter",
                f'tokenpak_requests_total {s.get("requests", 0)}',
                "# HELP tokenpak_tokens_input_total Total input tokens seen",
                "# TYPE tokenpak_tokens_input_total counter",
                f'tokenpak_tokens_input_total {s.get("input_tokens", 0)}',
                "# HELP tokenpak_tokens_saved_total Total tokens saved by compression",
                "# TYPE tokenpak_tokens_saved_total counter",
                f'tokenpak_tokens_saved_total {s.get("saved_tokens", 0)}',
                "# HELP tokenpak_tokens_injected_total Total tokens injected from vault",
                "# TYPE tokenpak_tokens_injected_total counter",
                f'tokenpak_tokens_injected_total {s.get("injected_tokens", 0)}',
                "# HELP tokenpak_cache_read_tokens_total Total cache read tokens",
                "# TYPE tokenpak_cache_read_tokens_total counter",
                f'tokenpak_cache_read_tokens_total {s.get("cache_read_tokens", 0)}',
                "# HELP tokenpak_cost_usd_total Total estimated cost in USD",
                "# TYPE tokenpak_cost_usd_total counter",
                f'tokenpak_cost_usd_total {s.get("cost", 0.0):.6f}',
                "# HELP tokenpak_errors_total Total errors",
                "# TYPE tokenpak_errors_total counter",
                f'tokenpak_errors_total {s.get("errors", 0)}',
                "# HELP tokenpak_uptime_seconds Proxy uptime in seconds",
                "# TYPE tokenpak_uptime_seconds gauge",
                f'tokenpak_uptime_seconds {uptime}',
                "# HELP tokenpak_canon_tokens_saved_total Tokens saved by CANON dedup",
                "# TYPE tokenpak_canon_tokens_saved_total counter",
                f'tokenpak_canon_tokens_saved_total {s.get("canon_tokens_saved", 0)}',
                "# HELP tokenpak_vault_blocks Vault index blocks loaded",
                "# TYPE tokenpak_vault_blocks gauge",
                f'tokenpak_vault_blocks {len(VAULT_INDEX.blocks) if VAULT_INDEX.available else 0}',
            ]
            body_out = "\n".join(lines).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", len(body_out))
            self.end_headers()
            self.wfile.write(body_out)
            return
        if self.path.startswith("http"):
            self._forward_request("GET")
        elif self.path.startswith("/ollama-proxy/"):
            self._ollama_proxy("GET")
        else:
            # Fix #2: JSON 404 instead of HTML
            self._send_json({"error": {"type": "not_found", "message": f"Unknown path: {self.path}"}}, status=404)

    def do_POST(self):
        # Fix #7: Per-IP rate limiting
        client_ip = self.client_address[0]
        if not _rate_limit_check(client_ip):
            self._send_json({
                "error": {"type": "rate_limit_exceeded", "message": f"Too many requests. Limit: {_RATE_LIMIT_RPM} req/min per IP."}
            }, status=429)
            return
        if self.path.startswith("http"):
            self._forward_request("POST")
        elif self.path.startswith("/ollama-proxy/"):
            self._ollama_proxy("POST")
        elif self.path.startswith("/v1/") or self.path.startswith("/v1beta/"):
            self._reverse_proxy("POST")
        elif self.path == "/ingest" or self.path == "/ingest/batch":
            self._ingest(self.path)
        else:
            # Fix #2: JSON 404 instead of HTML
            self._send_json({"error": {"type": "not_found", "message": f"Unknown path: {self.path}"}}, status=404)

    def do_PUT(self):
        if self.path.startswith("http"):
            self._forward_request("PUT")
        else:
            self._send_json({"error": {"type": "not_found", "message": f"Unknown path: {self.path}"}}, status=404)

    def do_DELETE(self):
        if self.path.startswith("http"):
            self._forward_request("DELETE")
        else:
            self._send_json({"error": {"type": "not_found", "message": f"Unknown path: {self.path}"}}, status=404)

    def _forward_request(self, method):
        self._proxy_to(self.path, method)

    def _ollama_proxy(self, method):
        """Route /ollama-proxy/... to the real ollama server with compaction pipeline.

        Circuit breaker: if upstream was unreachable within the last 120s,
        return 503 immediately instead of hanging for minutes.
        Connect timeout: 20s (configurable via TOKENPAK_OLLAMA_TIMEOUT).
        """
        from urllib.parse import urlparse

        # Check circuit breaker -- fail fast if upstream recently unreachable
        with _ollama_circuit_lock:
            if _ollama_circuit["open"]:
                elapsed = time.time() - _ollama_circuit["last_failure"]
                if elapsed < _ollama_circuit["cooldown"]:
                    err_msg = f"Ollama upstream {OLLAMA_UPSTREAM} unreachable (circuit open, retry in {int(_ollama_circuit['cooldown'] - elapsed)}s)"
                    print(f"  \u26a1 {err_msg}")
                    try:
                        err = json.dumps({"error": {"type": "circuit_open", "message": err_msg}}).encode()
                        self.send_response(503)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", len(err))
                        self.end_headers()
                        self.wfile.write(err)
                    except:
                        pass
                    return
                else:
                    _ollama_circuit["open"] = False
                    print(f"  \U0001f504 Ollama circuit breaker reset -- retrying upstream")

        # Probe upstream connectivity with short timeout before committing
        parsed = urlparse(OLLAMA_UPSTREAM)
        host = parsed.hostname
        port = parsed.port or 11434
        try:
            probe = socket.create_connection((host, port), timeout=OLLAMA_CONNECT_TIMEOUT)
            probe.close()
        except (socket.timeout, OSError, ConnectionRefusedError) as e:
            with _ollama_circuit_lock:
                _ollama_circuit["open"] = True
                _ollama_circuit["last_failure"] = time.time()
            err_msg = f"Ollama upstream {host}:{port} unreachable after {OLLAMA_CONNECT_TIMEOUT}s: {e}"
            print(f"  \u274c {err_msg}")
            SESSION["errors"] += 1
            try:
                err = json.dumps({"error": {"type": "upstream_unreachable", "message": err_msg}}).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", len(err))
                self.end_headers()
                self.wfile.write(err)
            except:
                pass
            return

        # Upstream reachable -- forward normally
        real_path = self.path[len("/ollama-proxy"):]
        target = OLLAMA_UPSTREAM + real_path
        self._proxy_to(target, method, force_intercept=True)

    def _reverse_proxy(self, method):
        headers = _header_mapping(self.headers)
        adapter = _detect_adapter(path=self.path, headers=headers, body_bytes=None)
        try:
            base = _resolve_upstream(adapter)
        except ValueError as exc:
            self._send_json(
                {
                    "error": {
                        "type": "upstream_route_missing",
                        "message": str(exc),
                    }
                },
                status=502,
            )
            return
        self._proxy_to(base + self.path, method, adapter=adapter)

    def _proxy_to(self, target_url, method, force_intercept=False, adapter: Optional[FormatAdapter] = None):
        t0 = time.time()
        parsed = urlparse(target_url)
        content_length = int(self.headers.get("Content-Length", 0))
        # Fix #1: Body size cap — reject requests over 10MB to prevent OOM
        MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB
        if content_length > MAX_BODY_BYTES:
            self.send_response(413)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"type": "request_too_large", "message": f"Request body exceeds 10MB limit ({content_length} bytes)"}}).encode())
            return
        body = self.rfile.read(content_length) if content_length > 0 else None
        active_adapter = adapter
        if active_adapter is None and body is not None:
            active_adapter = _detect_adapter(self.path, _header_mapping(self.headers), body)

        if active_adapter is None:
            active_adapter = _detect_adapter(self.path, _header_mapping(self.headers), None)

        should_log = (
            force_intercept
            or active_adapter.source_format != "passthrough"
            or any(h in target_url for h in INTERCEPT_HOSTS)
        )
        is_messages = True
        pipeline_enabled = active_adapter.source_format != "passthrough"

        model = "unknown"
        input_tokens = 0
        sent_input_tokens = 0
        protected_tokens = 0
        injected_tokens = 0
        injected_sources: List[str] = []
        is_streaming = False
        cache_read_tokens = 0
        cache_creation_tokens = 0
        cache_poison_scrubbed = False
        tools_schema_changed = False
        raw_request_body_for_cache_reason = body
        final_request_body_for_cache_reason = body
        router_meta: Optional[dict] = None

        # Pipeline trace
        trace: Optional[PipelineTrace] = None
        _wf_id = None  # proxy workflow tracking (TOKENPAK_WORKFLOW_TRACKING=1)
        if should_log and is_messages:
            trace = PipelineTrace(
                request_id=str(uuid.uuid4())[:8],
                timestamp=datetime.now().strftime("%H:%M:%S"),
            )
            # Start workflow tracking (no-op when feature flag is OFF)
            try:
                from tokenpak.agent.agentic.proxy_workflow import start_proxy_workflow
                _wf_id = start_proxy_workflow(
                    trace.request_id,
                    metadata={"path": self.path, "method": method},
                )
            except Exception:
                pass

        if should_log and is_messages and body:
            # Fix #5: Strict validation mode — reject malformed requests early
            if STRICT_VALIDATION:
                try:
                    _val_data = json.loads(body)
                    _val_errors = []
                    if "messages" not in _val_data:
                        _val_errors.append("missing required field: messages")
                    if "model" not in _val_data:
                        _val_errors.append("missing required field: model")
                    msgs = _val_data.get("messages", [])
                    if not isinstance(msgs, list) or len(msgs) == 0:
                        _val_errors.append("messages must be a non-empty array")
                    if _val_errors:
                        self._send_json({"error": {"type": "validation_error", "message": "; ".join(_val_errors)}}, status=400)
                        return
                except json.JSONDecodeError as _je:
                    self._send_json({"error": {"type": "invalid_json", "message": str(_je)}}, status=400)
                    return

            _original_body = body  # save for fallback
            try:
                model, input_tokens = extract_request_tokens(body, adapter=active_adapter)
                try:
                    req_data = json.loads(body)
                    is_streaming = req_data.get("stream", False)
                except:
                    pass

                if pipeline_enabled:
                    # Phase -1: Tool Schema Registry — normalize tools to byte-identical JSON
                    # Enables Anthropic cache hits on repeated tool schemas
                    if TOOL_REGISTRY_AVAILABLE and body:
                        try:
                            _tool_reg = _get_tool_registry()
                            if _tool_reg:
                                body, _tools_changed = _tool_reg.normalize_request(body)
                                tools_schema_changed = bool(_tools_changed)
                                _tstats = _tool_reg.stats()
                                SESSION["tool_schema_frozen_tools"] = _tstats.get("frozen_tools", 0)
                                SESSION["tool_schema_bytes_saved"] = _tool_reg.bytes_saved
                        except Exception as _treg_err:
                            pass  # fail-open: never break a request over tool registry

                    # Phase 0: Manual routing rules — rewrite model before any processing
                    try:
                        from tokenpak.routing.rules import RouteEngine, _extract_prompt_text, _count_tokens_approx
                        _route_engine = RouteEngine()
                        _route_payload = json.loads(body) if body else {}
                        _route_prompt = _extract_prompt_text(_route_payload)
                        _route_tokens = _count_tokens_approx(_route_prompt)
                        _matched_rule = _route_engine.match(
                            model=model,
                            prompt=_route_prompt,
                            token_count=_route_tokens,
                        )
                        if _matched_rule:
                            _route_payload["model"] = _matched_rule.target
                            body = json.dumps(_route_payload).encode()
                            model = _matched_rule.target
                            print(f"  🔀 Route rule [{_matched_rule.id}]: → {_matched_rule.target}")
                    except Exception as _route_err:
                        print(f"  ⚠️ Routing rule error (skipping): {_route_err}")

                    # Phase 0.3: DeterministicRouter — intent classification + compression pipeline
                    if ROUTER_ENABLED:
                        try:
                            _session_id_router = self.headers.get("X-OpenClaw-Session", model)
                            body, _router_meta = _run_router(body, session_id=_session_id_router)
                            router_meta = _router_meta
                            if _router_meta and not _router_meta.get("fallback"):
                                print(f"  🔀 Router: intent={_router_meta.get('intent','?')} recipe={_router_meta.get('recipe_used','?')} ({_router_meta.get('total_ms',0)}ms)")
                        except Exception as _router_err:
                            print(f"  ⚠️ Router stage error (skipping): {_router_err}")

                    # Phase 0.5: Capsule builder — compress historical context blocks
                    if CAPSULE_BUILDER is not None and ENABLE_CAPSULE_BUILDER:
                        t_capsule = time.time()
                        capsule_stage = StageTrace(
                            name="capsule",
                            enabled=True,
                            input_tokens=input_tokens,
                        )
                        try:
                            body, _cap_stats = CAPSULE_BUILDER.process(body)
                            _cap_blocks = _cap_stats.get("blocks_capsulized", 0)
                            _cap_ratio = _cap_stats.get("ratio", 1.0)
                            _cap_chars_in = _cap_stats.get("chars_in", 0)
                            _cap_chars_out = _cap_stats.get("chars_out", 0)
                            capsule_stage.details["blocks_capsulized"] = _cap_blocks
                            capsule_stage.details["compression_ratio"] = _cap_ratio
                            capsule_stage.details["chars_in"] = _cap_chars_in
                            capsule_stage.details["chars_out"] = _cap_chars_out
                            capsule_stage.details["skip_reason"] = _cap_stats.get("skip_reason")
                            if _cap_blocks > 0:
                                # Recount tokens after capsulisation
                                _, input_tokens = extract_request_tokens(body, adapter=active_adapter)
                                print(
                                    f"  💊 Capsule: {_cap_blocks} block(s) compressed "
                                    f"({_cap_chars_in}→{_cap_chars_out} chars, ratio={_cap_ratio})"
                                )
                            capsule_stage.output_tokens = input_tokens
                            capsule_stage.tokens_delta = capsule_stage.output_tokens - capsule_stage.input_tokens
                        except Exception as _cap_err:
                            print(f"  ⚠️  Capsule builder error (skipping): {_cap_err}")
                            capsule_stage.details["error"] = str(_cap_err)
                            capsule_stage.output_tokens = input_tokens
                        capsule_stage.duration_ms = (time.time() - t_capsule) * 1000
                        if trace:
                            trace.stages.append(capsule_stage)

                    # Phase 0.9: Cache Poison Removal — strip dynamic UUIDs, timestamps, heartbeat counters
                    # Must run BEFORE stable cache control so the stable prefix stays bit-identical
                    if body:
                        _pre_poison_body = body
                        body = _strip_cache_poisons(body)
                        cache_poison_scrubbed = body != _pre_poison_body

                    # Phase 1: Vault context injection (BEFORE compaction)
                    t_inject = time.time()
                    VAULT_INDEX.maybe_reload()
                    vault_stage = StageTrace(
                        name="vault_injection",
                        enabled=VAULT_INDEX.available,
                        input_tokens=input_tokens,
                    )
                    if VAULT_INDEX.available:
                        skip_injection = False
                        if INJECT_SKIP_MODELS.strip():
                            if any(skip.strip() and skip.strip().lower() in model.lower() for skip in INJECT_SKIP_MODELS.split(",")):
                                skip_injection = True
                        if input_tokens < INJECT_MIN_PROMPT:
                            skip_injection = True
                        if skip_injection:
                            SESSION["injection_skips"] += 1
                            vault_stage.details["skipped"] = True
                            vault_stage.details["reason"] = "model_skip" if INJECT_SKIP_MODELS.strip() and any(s.lower() in model.lower() for s in INJECT_SKIP_MODELS.split(",")) else "prompt_too_short"
                            # Even when skipping vault injection, apply cache_control to stable prefix
                            if PROMPT_BUILDER_AVAILABLE:
                                body = _apply_stable_cache_control(body)
                        else:
                            body, injected_tokens, injected_sources = inject_vault_context(body, adapter=active_adapter)
                            if injected_tokens > 0:
                                # Recount tokens after injection
                                _, input_tokens = extract_request_tokens(body, adapter=active_adapter)
                                vault_stage.tokens_delta = injected_tokens
                                vault_stage.details["blocks_matched"] = len(injected_sources)
                                vault_stage.details["block_names"] = injected_sources[:5]  # Top 5
                                vault_stage.details["tokens_injected"] = injected_tokens
                    vault_stage.output_tokens = input_tokens
                    vault_stage.duration_ms = (time.time() - t_inject) * 1000
                    if trace:
                        trace.stages.append(vault_stage)

                    # Phase 1.5: CANON dedup (AFTER injection, BEFORE compaction)
                    if CANON_AVAILABLE and injected_tokens > 0:
                        t_canon = time.time()
                        canon_stage = StageTrace(
                            name="canon_dedup",
                            enabled=True,
                            input_tokens=input_tokens,
                        )
                        try:
                            session_id = self.headers.get("X-OpenClaw-Session", model)
                            body, canon_refs, canon_saved = apply_canon_refs(body, session_id)
                            if canon_refs > 0:
                                SESSION["canon_hits"] += canon_refs
                                SESSION["canon_tokens_saved"] += canon_saved
                                canon_stage.tokens_delta = -canon_saved
                                canon_stage.details["blocks_referenced"] = canon_refs
                                canon_stage.details["tokens_saved"] = canon_saved
                                _, input_tokens = extract_request_tokens(body, adapter=active_adapter)
                        except Exception as _canon_err:
                            canon_stage.details["error"] = str(_canon_err)
                        canon_stage.output_tokens = input_tokens
                        canon_stage.duration_ms = (time.time() - t_canon) * 1000
                        if trace:
                            trace.stages.append(canon_stage)

                    # Phase 2: Compaction (AFTER injection)
                    t_compact = time.time()
                    compaction_stage = StageTrace(
                        name="compaction",
                        enabled=ENABLE_COMPACTION,
                        input_tokens=input_tokens,
                    )
                    if ENABLE_COMPACTION:
                        body, sent_input_tokens, original_tokens, protected_tokens = compact_request_body(
                            body,
                            adapter=active_adapter,
                        )
                        if original_tokens > 0:
                            input_tokens = original_tokens
                        compaction_stage.output_tokens = sent_input_tokens
                        compaction_stage.tokens_delta = -(original_tokens - sent_input_tokens) if original_tokens else 0
                        compaction_stage.details["mode"] = COMPILATION_MODE
                        compaction_stage.details["protected_tokens"] = protected_tokens
                        compaction_stage.details["tokens_removed"] = max(0, original_tokens - sent_input_tokens) if original_tokens else 0
                    else:
                        sent_input_tokens = input_tokens
                        compaction_stage.output_tokens = sent_input_tokens
                    compaction_stage.duration_ms = (time.time() - t_compact) * 1000
                    if trace:
                        trace.stages.append(compaction_stage)
                    # Workflow: vault_inject done → compress done → begin forward
                    if _wf_id:
                        try:
                            from tokenpak.agent.agentic.proxy_workflow import advance_step
                            advance_step(_wf_id, "vault_inject", "compress")
                            advance_step(_wf_id, "compress", "forward")
                        except Exception:
                            pass
                else:
                    sent_input_tokens = input_tokens
            except Exception as _pipeline_err:
                print(f"  ⚠️ Pre-pipeline error (falling back to original body): {_pipeline_err}")
                body = _original_body  # restore original body so request still forwards
                model, input_tokens = extract_request_tokens(body, adapter=active_adapter)
                sent_input_tokens = input_tokens

        final_request_body_for_cache_reason = body

        # Final validation gate (pre-forward): budget, deterministic context, fingerprint, dry-run
        if should_log and is_messages and body and active_adapter.source_format != "passthrough":
            gate = _get_validation_gate()
            if gate is not None:
                try:
                    gate_result = gate.validate_request(
                        request_body=body,
                        model=model,
                        input_tokens=sent_input_tokens or input_tokens,
                        router_meta=router_meta or {},
                    )
                    if gate_result.fingerprint:
                        print(f"  🧾 Determinism fingerprint: {gate_result.fingerprint}")
                    if not gate_result.valid:
                        self._send_json(
                            {
                                "error": {
                                    "type": "validation_gate_failed",
                                    "message": "Request blocked by validation gate",
                                    "reasons": gate_result.errors,
                                },
                                "warnings": gate_result.warnings,
                                "fingerprint": gate_result.fingerprint,
                            },
                            status=422,
                        )
                        return
                    if gate_result.dry_run:
                        self._send_json(
                            {
                                "status": "dry_run",
                                "message": "Validation gate accepted request; upstream forward skipped",
                                "plan": gate_result.plan,
                                "fingerprint": gate_result.fingerprint,
                                "warnings": gate_result.warnings,
                            },
                            status=200,
                        )
                        return
                except Exception as _gate_err:
                    print(f"  ⚠️ Validation gate error (fail-open): {_gate_err}")

        fwd_headers = {}
        for key in self.headers:
            if key.lower() in ("host", "proxy-connection", "proxy-authorization",
                               "connection", "keep-alive", "transfer-encoding",
                               "te", "trailer", "upgrade", "content-length",
                               "accept-encoding"):
                continue
            fwd_headers[key] = self.headers[key]
        fwd_headers["Host"] = parsed.netloc
        if sent_input_tokens == 0:
            sent_input_tokens = input_tokens
        if body is not None:
            fwd_headers["Content-Length"] = str(len(body))

        # Fix #5: Check per-provider circuit breaker before attempting upstream
        _cb_provider = _provider_for_url(target_url)
        if _circuit_check(_cb_provider):
            self._send_json({
                "error": {"type": "circuit_open", "message": f"Provider {_cb_provider} circuit is open — too many recent failures. Retry in 60s."}
            }, status=503)
            return

        try:
            if parsed.scheme == "https":
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(parsed.netloc, timeout=UPSTREAM_TIMEOUT, context=ctx)
            else:
                conn = http.client.HTTPConnection(parsed.netloc, timeout=UPSTREAM_TIMEOUT)
            path = parsed.path
            if parsed.query:
                path += "?" + parsed.query
            conn.request(method, path, body=body, headers=fwd_headers)
            resp = conn.getresponse()
            status = resp.status
            # Fix #5: Record success/failure for circuit breaker
            if status >= 500:
                _circuit_record_failure(_cb_provider)
            else:
                _circuit_record_success(_cb_provider)
            content_type = resp.getheader("Content-Type", "")
            is_sse = "text/event-stream" in content_type

            # Fix #4: Normalize upstream error responses to unified JSON shape
            # Anthropic returns {"type":"error","error":{...},"request_id":"..."}
            # We normalize all 4xx/5xx to {"error":{"type":...,"message":...}}
            _resp_content_type = resp.getheader("Content-Type", "")
            if status >= 400 and "application/json" in _resp_content_type and not is_sse:
                try:
                    _err_raw = resp.read()
                    _err_data = json.loads(_err_raw)
                    # Anthropic shape: {"type":"error","error":{"type":...,"message":...}}
                    if "type" in _err_data and _err_data.get("type") == "error" and "error" in _err_data:
                        _inner = _err_data["error"]
                        _normalized = {"error": {"type": _inner.get("type", "upstream_error"), "message": _inner.get("message", ""), "request_id": _err_data.get("request_id", "")}}
                    # OpenAI shape: {"error":{"message":...,"type":...,"code":...}}
                    elif "error" in _err_data and isinstance(_err_data["error"], dict):
                        _normalized = _err_data  # already correct shape
                    else:
                        _normalized = {"error": {"type": "upstream_error", "message": str(_err_data)}}
                    _err_body = json.dumps(_normalized, indent=2).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", len(_err_body))
                    self.end_headers()
                    self.wfile.write(_err_body)
                    return
                except Exception:
                    resp = type("FakeResp", (), {"read": lambda self: _err_raw, "getheaders": lambda self: [], "getheader": lambda self, k, d="": d})()

            self.send_response(status)
            for h_key, h_val in resp.getheaders():
                h_lower = h_key.lower()
                if h_lower in ("connection", "keep-alive", "transfer-encoding"):
                    continue
                if h_lower == "content-length":
                    continue
                self.send_header(h_key, h_val)
            self.end_headers()

            if is_sse:
                output_tokens = 0
                sse_buffer = b""
                chunk_count = 0
                early_break = False
                _pending_chunk = b""
                _footer_injected = False
                import zlib as _zlib
                _ce = resp.getheader("Content-Encoding", "")
                _decomp = _zlib.decompressobj(_zlib.MAX_WBITS | 16) if "gzip" in _ce else None
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        # Flush any pending chunk at end of stream
                        if _pending_chunk:
                            try:
                                self.wfile.write(_pending_chunk)
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError):
                                pass
                            if should_log and is_messages:
                                sse_buffer += _pending_chunk
                        break
                    chunk_count += 1
                    if _decomp:
                        try:
                            chunk = _decomp.decompress(chunk)
                        except Exception:
                            pass
                    if not chunk:
                        continue

                    # Chat footer injection — buffer chunks to find message_stop
                    if CHAT_FOOTER_ENABLED and not _footer_injected and should_log and is_messages:
                        combined = _pending_chunk + chunk
                        _pending_chunk = b""
                        if b'"type":"message_stop"' in combined or b'"type": "message_stop"' in combined:
                            try:
                                # Find injection point — right before message_stop event
                                stop_idx = combined.find(b'event: message_stop')
                                if stop_idx == -1:
                                    # Inline format — find the event: line before type:message_stop
                                    ms_idx = combined.find(b'"type":"message_stop"')
                                    if ms_idx == -1:
                                        ms_idx = combined.find(b'"type": "message_stop"')
                                    if ms_idx > 0:
                                        search_back = combined[:ms_idx].rfind(b'event:')
                                        stop_idx = search_back if search_back >= 0 else -1

                                if stop_idx > 0:
                                    before_stop = combined[:stop_idx]
                                    after_stop = combined[stop_idx:]
                                    self.wfile.write(before_stop)
                                    self.wfile.flush()
                                    sse_buffer += before_stop

                                    # Build footer stats
                                    _temp_usage = _extract_sse_tokens(sse_buffer)
                                    _temp_output = _temp_usage.get("output_tokens", 0)
                                    _temp_cache_r = _temp_usage.get("cache_read_input_tokens", 0)
                                    _saved = max(0, input_tokens - sent_input_tokens)
                                    _pct = int(100 * _saved / input_tokens) if input_tokens > 0 else 0
                                    _cost = estimate_cost(model, sent_input_tokens, _temp_output, _temp_cache_r, 0)
                                    _footer_text = f"\n\n───\n📊 {input_tokens:,}→{sent_input_tokens:,} tok (-{_pct}%) | ${_cost:.3f}"
                                    if _temp_cache_r > 0:
                                        _footer_text += f" | cache: {_temp_cache_r:,}r"
                                    _footer_event = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": _footer_text}}
                                    _footer_sse = f"event: content_block_delta\ndata: {json.dumps(_footer_event)}\n\n".encode()
                                    self.wfile.write(_footer_sse)
                                    self.wfile.flush()
                                    _footer_injected = True

                                    self.wfile.write(after_stop)
                                    self.wfile.flush()
                                    sse_buffer += after_stop
                                    continue
                                else:
                                    # Couldn't find injection point — write combined as-is
                                    self.wfile.write(combined)
                                    self.wfile.flush()
                                    sse_buffer += combined
                                    _footer_injected = True
                                    continue
                            except Exception:
                                # Fail-open — write the chunk normally
                                self.wfile.write(combined)
                                self.wfile.flush()
                                sse_buffer += combined
                                _footer_injected = True
                                continue
                        else:
                            # Buffer one chunk ahead to catch message_stop split across chunks
                            if _pending_chunk:
                                try:
                                    self.wfile.write(_pending_chunk)
                                    self.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError):
                                    early_break = True
                                    break
                                if should_log and is_messages:
                                    sse_buffer += _pending_chunk
                            _pending_chunk = combined
                            continue

                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        early_break = True
                        break
                    if should_log and is_messages:
                        sse_buffer += chunk
                if should_log and is_messages:
                    sse_usage = _extract_sse_tokens(sse_buffer)
                    output_tokens = extract_response_tokens(sse_buffer, adapter=active_adapter, is_sse=True)
                    cache_read_tokens = sse_usage.get("cache_read_input_tokens", 0)
                    cache_creation_tokens = sse_usage.get("cache_creation_input_tokens", 0)
            else:
                resp_body = resp.read()
                output_tokens = 0
                # Chat footer — JSON (non-streaming) injection
                if CHAT_FOOTER_ENABLED and should_log and is_messages and status == 200:
                    try:
                        body_for_parse = resp_body
                        if "gzip" in resp.getheader("Content-Encoding", ""):
                            body_for_parse = gzip.decompress(resp_body)
                        resp_json = json.loads(body_for_parse)
                        usage = resp_json.get("usage", {})
                        _out_tok = usage.get("output_tokens", 0)
                        _cache_r = usage.get("cache_read_input_tokens", 0)
                        _pct = round((input_tokens - sent_input_tokens) / input_tokens * 100, 1) if input_tokens else 0
                        _cost = estimate_cost(model, sent_input_tokens, _out_tok, _cache_r, 0)
                        _footer_text = f"\n\n───\n📊 {input_tokens:,}→{sent_input_tokens:,} tok (-{_pct}%) | ${_cost:.3f}"
                        if _cache_r > 0:
                            _footer_text += f" | cache: {_cache_r:,}r"
                        content = resp_json.get("content", [])
                        if content and isinstance(content, list):
                            for i in range(len(content) - 1, -1, -1):
                                if content[i].get("type") == "text":
                                    content[i]["text"] += _footer_text
                                    break
                            resp_json["content"] = content
                            resp_body = json.dumps(resp_json).encode()
                    except Exception:
                        pass  # fail-open
                self.wfile.write(resp_body)
                self.wfile.flush()
                if should_log and is_messages:
                    resp_for_metrics = resp_body
                    if "gzip" in resp.getheader("Content-Encoding", ""):
                        try:
                            resp_for_metrics = gzip.decompress(resp_body)
                        except:
                            pass
                    output_tokens = extract_response_tokens(resp_for_metrics, adapter=active_adapter)
                    try:
                        usage = json.loads(resp_for_metrics).get("usage", {})
                        cache_read_tokens = usage.get("cache_read_input_tokens", 0)
                        cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
                    except:
                        pass

            conn.close()
            latency_ms = int((time.time() - t0) * 1000)

            if should_log and is_messages and input_tokens > 0:
                cost = estimate_cost(model, sent_input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens)
                saved = max(0, input_tokens - sent_input_tokens)
                # Estimate cost saved (what it would have cost without compression)
                cost_without_compression = estimate_cost(model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens)
                cost_saved = max(0.0, cost_without_compression - cost)
                sources_str = ",".join(injected_sources) if injected_sources else ""
                try:
                    MONITOR.log(model, sent_input_tokens, output_tokens, cost, latency_ms, status,
                               target_url, COMPILATION_MODE, protected_tokens, saved,
                               injected_tokens, sources_str, cache_read_tokens, cache_creation_tokens)
                except Exception as _monitor_err:
                    print(f"  ⚠️ Monitor.log() failed (SQLite error, request unaffected): {_monitor_err}")
                try:
                    from tokenpak.telemetry.anon_metrics import record_request
                    record_request(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        tokens_saved=saved,
                        latency_ms=latency_ms,
                        model=model,
                    )
                except Exception:
                    pass  # never break the proxy
                SESSION["requests"] += 1
                SESSION["input_tokens"] += input_tokens
                SESSION["sent_input_tokens"] += sent_input_tokens
                SESSION["saved_tokens"] += saved
                SESSION["protected_tokens"] += protected_tokens
                SESSION["output_tokens"] += output_tokens
                SESSION["cost"] += cost
                SESSION["cost_saved"] += cost_saved
                SESSION["injected_tokens"] += injected_tokens
                SESSION["cache_read_tokens"] += cache_read_tokens
                SESSION["cache_creation_tokens"] += cache_creation_tokens
                if cache_read_tokens > 0:
                    SESSION["cache_hits"] += 1
                else:
                    SESSION["cache_misses"] += 1
                    miss_reason = _classify_cache_miss_reason(
                        raw_request_body_for_cache_reason,
                        cache_poison_scrubbed=cache_poison_scrubbed,
                        tools_schema_changed=tools_schema_changed,
                        final_body=final_request_body_for_cache_reason,
                    )
                    miss_map = SESSION.setdefault("cache_miss_reasons", {})
                    miss_map[miss_reason] = int(miss_map.get(miss_reason, 0) or 0) + 1
                if injected_tokens > 0:
                    SESSION["injection_hits"] += 1

                # Complete and store pipeline trace
                if trace:
                    trace.model = model
                    trace.input_tokens = input_tokens
                    trace.output_tokens = output_tokens
                    trace.tokens_saved = saved
                    trace.cost_saved = cost_saved
                    trace.total_cost = cost
                    trace.duration_ms = latency_ms
                    trace.status = "complete"
                    TRACE_STORAGE.store(trace)

                # Workflow tracking: mark forward done → log_metrics → complete
                if _wf_id:
                    try:
                        from tokenpak.agent.agentic.proxy_workflow import advance_step, complete_workflow
                        advance_step(_wf_id, "forward", "log_metrics")
                        complete_workflow(_wf_id)
                    except Exception:
                        pass

                # Update last request stats for /stats/last endpoint
                request_id = trace.request_id if trace else str(uuid.uuid4())[:8]
                update_last_request(
                    request_id=request_id,
                    model=model,
                    input_raw=input_tokens,
                    input_sent=sent_input_tokens,
                    tokens_saved=saved,
                    cost_saved=cost_saved,
                    output_tokens=output_tokens,
                )

                stream_tag = " [SSE]" if is_sse else ""
                mode_tag = f" [{COMPILATION_MODE}]"
                inject_tag = f" [+{injected_tokens} vault]" if injected_tokens > 0 else ""
                # Cache status tag: show FRESH/CACHED with token counts for clarity
                if cache_read_tokens > 0:
                    _saved_k = f"{cache_read_tokens:,}"
                    cache_tag = f" (CACHED: {_saved_k} tokens)"
                elif cache_creation_tokens > 0:
                    _written_k = f"{cache_creation_tokens:,}"
                    cache_tag = f" (FRESH: {_written_k} written)"
                else:
                    cache_tag = " (FRESH)"
                print(f"  📊 {model}{stream_tag}{mode_tag}{inject_tag}: {input_tokens:,} in → {sent_input_tokens:,} sent "
                      f"(saved {saved:,}, protected {protected_tokens:,}) / {output_tokens:,} out | "
                      f"~${cost:.4f}{cache_tag} | {latency_ms}ms")

        except Exception as e:
            SESSION["errors"] += 1
            latency_ms = int((time.time() - t0) * 1000)
            import traceback as _tb; _tb.print_exc(file=__import__("sys").stderr)
            print(f"  ❌ Proxy error: {type(e).__name__}: {e} | {latency_ms}ms")
            # Workflow tracking: mark the in-progress step as failed (not whole workflow)
            if _wf_id:
                try:
                    from tokenpak.agent.agentic.proxy_workflow import fail_step as _wf_fail
                    _wf_fail(_wf_id, "forward", error=f"{type(e).__name__}: {e}")
                except Exception:
                    pass
            try:
                err = json.dumps({"error": {"type": "proxy_error", "message": str(e)}}).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", len(err))
                self.end_headers()
                self.wfile.write(err)
            except:
                pass

    def _ingest(self, path):
        """Handle /ingest and /ingest/batch POST requests."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json({"error": "empty request body"}, status=400)
            return
        if content_length > 1024 * 1024:  # 1MB limit for ingest payloads
            self._send_json({"error": "request body too large (max 1MB)"}, status=413)
            return
        
        try:
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json({"error": f"invalid JSON: {e}"}, status=400)
            return
        
        if path == "/ingest":
            self._ingest_single(payload)
        elif path == "/ingest/batch":
            self._ingest_batch(payload)
    
    def _ingest_single(self, payload):
        """Handle single entry ingest."""
        if not isinstance(payload, dict):
            self._send_json({"error": "expected object, got " + type(payload).__name__}, status=400)
            return
        
        # Validate required fields
        required = {"model", "tokens", "cost"}
        missing = required - set(payload.keys())
        if missing:
            self._send_json({"error": f"missing required fields: {', '.join(missing)}"}, status=400)
            return
        
        try:
            # Basic type validation
            model = payload.get("model")
            tokens = payload.get("tokens")
            cost = payload.get("cost")
            
            if not isinstance(model, str) or not model:
                raise ValueError("model must be a non-empty string")
            if not isinstance(tokens, int) or tokens < 0:
                raise ValueError("tokens must be a non-negative integer")
            if not isinstance(cost, (int, float)) or cost < 0:
                raise ValueError("cost must be a non-negative number")
            
            # Validate timestamp if provided
            timestamp = payload.get("timestamp")
            if timestamp is not None:
                if not isinstance(timestamp, str):
                    raise ValueError("timestamp must be a string")
                # Validate ISO 8601 format
                try:
                    datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError(f"invalid ISO 8601 timestamp: {timestamp}")
            else:
                # Use current UTC time
                timestamp = datetime.now(timezone.utc).isoformat()
                payload["timestamp"] = timestamp
            
            # Write entry
            entry_id = _ingest_write_entry(payload)
            self._send_json({"status": "ok", "ids": [entry_id]}, status=200)
            SESSION["ingest_entries"] = SESSION.get("ingest_entries", 0) + 1
        except ValueError as e:
            self._send_json({"error": str(e)}, status=422)
        except Exception as e:
            self._send_json({"error": f"internal error: {e}"}, status=500)
    
    def _ingest_batch(self, payload):
        """Handle batch entry ingest."""
        if not isinstance(payload, dict):
            self._send_json({"error": "expected object, got " + type(payload).__name__}, status=400)
            return
        
        if "events" not in payload:
            self._send_json({"error": "missing 'events' field"}, status=400)
            return
        
        events = payload["events"]
        if not isinstance(events, list):
            self._send_json({"error": "events must be a list"}, status=400)
            return
        
        if len(events) == 0:
            self._send_json({"error": "events list cannot be empty"}, status=400)
            return
        
        if len(events) > 1000:
            self._send_json({"error": "events list too large (max 1000)"}, status=400)
            return
        
        ids = []
        errors = []
        
        for i, event in enumerate(events):
            if not isinstance(event, dict):
                errors.append(f"event[{i}]: expected object, got {type(event).__name__}")
                continue
            
            required = {"model", "tokens", "cost"}
            missing = required - set(event.keys())
            if missing:
                errors.append(f"event[{i}]: missing fields {', '.join(missing)}")
                continue
            
            try:
                model = event.get("model")
                tokens = event.get("tokens")
                cost = event.get("cost")
                
                if not isinstance(model, str) or not model:
                    raise ValueError("model must be non-empty string")
                if not isinstance(tokens, int) or tokens < 0:
                    raise ValueError("tokens must be non-negative int")
                if not isinstance(cost, (int, float)) or cost < 0:
                    raise ValueError("cost must be non-negative number")
                
                timestamp = event.get("timestamp")
                if timestamp is not None:
                    if not isinstance(timestamp, str):
                        raise ValueError("timestamp must be string")
                    try:
                        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    except ValueError:
                        raise ValueError(f"invalid timestamp: {timestamp}")
                else:
                    timestamp = datetime.now(timezone.utc).isoformat()
                    event["timestamp"] = timestamp
                
                entry_id = _ingest_write_entry(event)
                ids.append(entry_id)
            except ValueError as e:
                errors.append(f"event[{i}]: {e}")
        
        # Return success if we got any entries
        if ids:
            self._send_json({"status": "ok", "ids": ids, "errors": errors if errors else None}, status=200)
            SESSION["ingest_entries"] = SESSION.get("ingest_entries", 0) + len(ids)
        else:
            # All events failed
            self._send_json({"error": f"all events failed: {'; '.join(errors)}"}, status=422)

    def _send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Ingest storage
# ---------------------------------------------------------------------------
INGEST_ENTRIES_DIR = Path.home() / "vault" / ".tokenpak" / "entries"

def _ingest_write_entry(entry: Dict[str, Any]) -> str:
    """Append a single entry to the JSONL file, return its id."""
    entry_id = entry.setdefault("id", str(uuid.uuid4()))
    date_str = None
    
    # Use timestamp date if provided, else today
    ts = entry.get("timestamp")
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Create entries directory
    INGEST_ENTRIES_DIR.mkdir(parents=True, exist_ok=True)
    
    # Append to JSONL file
    entries_file = INGEST_ENTRIES_DIR / f"{date_str}.jsonl"
    with open(entries_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())
    
    return entry_id


# ---------------------------------------------------------------------------
# Vault sync
# ---------------------------------------------------------------------------
def sync_to_vault():
    vault_path = Path.home() / "vault" / "System" / "tokenpak-stats.json"
    if vault_path.parent.exists():
        stats = MONITOR.get_stats()
        stats["by_model"] = MONITOR.get_by_model()
        stats["last_sync"] = datetime.now().isoformat()
        stats["compilation_mode"] = COMPILATION_MODE
        stats["session"] = {
            "requests": SESSION["requests"],
            "protected_tokens": SESSION["protected_tokens"],
            "injected_tokens": SESSION["injected_tokens"],
            "injection_hits": SESSION["injection_hits"],
            "uptime_hours": round((time.time() - SESSION["start_time"]) / 3600, 2),
        }
        vault_path.write_text(json.dumps(stats, indent=2))


def sync_loop():
    while True:
        time.sleep(VAULT_SYNC_INTERVAL)
        try:
            sync_to_vault()
        except Exception as e:
            print(f"  ⚠️ Vault sync failed: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
class ThreadedHTTPServer(HTTPServer):
    def process_request(self, request, client_address):
        global _active_request_count
        with _active_request_lock:
            _active_request_count += 1
        t = threading.Thread(target=self._handle, args=(request, client_address))
        t.daemon = True
        t.start()

    def _handle(self, request, client_address):
        global _active_request_count
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            with _active_request_lock:
                _active_request_count -= 1
                if _active_request_count == 0 and _shutdown_event.is_set():
                    _active_requests_drained.set()


def main():
    port = PROXY_PORT
    mode_desc = {
        "strict": "100% lossless — no compression",
        "hybrid": "Protected/Code strict, Narrative compressed",
        "aggressive": "Everything except protected gets compressed",
    }

    # Load vault index on startup
    VAULT_INDEX.maybe_reload()
    vault_status = f"{len(VAULT_INDEX.blocks)} blocks" if VAULT_INDEX.available else "not found"

    # Proxy workflow tracking — startup dangling workflow check
    try:
        import sys as _sys; _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tokenpak"))
        from tokenpak.agent.agentic.proxy_workflow import recover_proxy_workflows
        dangling = recover_proxy_workflows()
        if dangling:
            print(f"[proxy_workflow] ⚠️  {len(dangling)} incomplete proxy workflow(s) from prior run:")
            for wf in dangling[:5]:
                running_step = next((s["name"] for s in wf["steps"] if s["status"] == "running"), "—")
                print(f"  • {wf['id'][:8]}… step={running_step}")
    except Exception as _e:
        print(f"[proxy_workflow] startup check skipped: {_e}")

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║             TokenPak Forward Proxy v4                            ║
║             Two-Tier Context Injection                           ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  Listening:    http://0.0.0.0:{port:<5}                              ║
║  Mode:         {COMPILATION_MODE:<10} ({mode_desc.get(COMPILATION_MODE, '?')})
║  Compaction:   {'ON' if ENABLE_COMPACTION else 'OFF':<10}                                       ║
║  Threshold:    {COMPACT_THRESHOLD_TOKENS} tokens                               ║
║  DB:           {str(MONITOR_DB):<50}║
║                                                                  ║
║  Two-Tier Index:                                                 ║
║    📚 Vault:     {vault_status:<44}║
║    💉 Budget:    {INJECT_BUDGET} tokens/request                        ║
║    🎯 Min score: {INJECT_MIN_SCORE}                                          ║
║    📂 Path:      {str(VAULT_INDEX_PATH):<44}║
║                                                                  ║
║  Style Contracts:                                                ║
║    🔒 PROTECTED — system prompts, SOUL.md, tool schemas          ║
║    📝 NARRATIVE — docs, markdown (compressible in hybrid+)       ║
║    💻 CODE      — source code (strict in hybrid, compressible    ║
║                   in aggressive)                                 ║
║    ⚙️  CONFIG    — JSON/YAML/config (strict in hybrid)            ║
║                                                                  ║
║  Endpoints:  /health  /stats  /recent  /vault                    ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    sync_thread = threading.Thread(target=sync_loop, daemon=True)
    sync_thread.start()

    # Schedule anonymous metrics daily batch sync (non-blocking, opt-in only)
    try:
        from tokenpak.telemetry.reporter import schedule_daily_sync
        schedule_daily_sync()
    except Exception:
        pass

    server = ThreadedHTTPServer(("0.0.0.0", port), ForwardProxyHandler)

    def _handle_signal(signum, frame):
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(f"\n[shutdown] {sig_name} received — stopping gracefully…")
        _shutdown_event.set()
        # shutdown() must be called from a different thread than serve_forever()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    server.serve_forever()

    # --- Drain in-flight requests (up to 10s) ---
    drain_timeout = 10
    with _active_request_lock:
        count = _active_request_count
    if count > 0:
        print(f"[shutdown] Draining {count} in-flight request(s) (up to {drain_timeout}s)…")
        _active_requests_drained.wait(timeout=drain_timeout)
        with _active_request_lock:
            remaining = _active_request_count
        if remaining:
            print(f"[shutdown] ⚠️  {remaining} request(s) still active after {drain_timeout}s — forcing exit")
        else:
            print("[shutdown] ✅ All in-flight requests completed")
    else:
        print("[shutdown] ✅ No in-flight requests — clean exit")

    print(f"\n📊 Session Summary:")
    print(f"   Mode:            {COMPILATION_MODE}")
    print(f"   Requests:        {SESSION['requests']}")
    print(f"   Input:           {SESSION['input_tokens']:,} tokens")
    print(f"   Sent:            {SESSION['sent_input_tokens']:,} tokens")
    print(f"   Protected:       {SESSION['protected_tokens']:,} tokens (never compressed)")
    print(f"   Saved:           {SESSION['saved_tokens']:,} tokens")
    print(f"   Injected:        {SESSION['injected_tokens']:,} tokens ({SESSION['injection_hits']} hits)")
    print(f"   Output:          {SESSION['output_tokens']:,} tokens")
    print(f"   Est. cost:       ${SESSION['cost']:.4f}")
    print(f"   Errors:          {SESSION['errors']}")
    sync_to_vault()
    print("[shutdown] SQLite connections closed (per-request open/close pattern — no persistent handles)")
    sys.exit(0)


if __name__ == "__main__":
    main()
