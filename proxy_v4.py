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
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field, asdict
from collections import deque
import uuid

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

INTERCEPT_HOSTS = {"api.anthropic.com", "api.openai.com"}

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


def extract_request_tokens(body_bytes):
    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "unknown", 0
    model = data.get("model", "unknown")
    tokens = 0
    if "system" in data:
        sys_content = data["system"]
        if isinstance(sys_content, str):
            tokens += count_tokens(sys_content)
        elif isinstance(sys_content, list):
            for part in sys_content:
                if isinstance(part, dict) and "text" in part:
                    tokens += count_tokens(part["text"])
    for msg in data.get("messages", []):
        content = msg.get("content", "")
        if isinstance(content, str):
            tokens += count_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if "text" in part:
                        tokens += count_tokens(part["text"])
                    if part.get("type") == "image":
                        tokens += 1000
    if "prompt" in data:
        tokens += count_tokens(data["prompt"])
    return model, tokens


def extract_response_tokens(body_bytes):
    try:
        data = json.loads(body_bytes)
    except:
        return 0
    usage = data.get("usage", {})
    if "output_tokens" in usage:
        return usage["output_tokens"]
    if "completion_tokens" in usage:
        return usage["completion_tokens"]
    return 0


# ---------------------------------------------------------------------------
# Context Injection: Extract query signal from request
# ---------------------------------------------------------------------------
def extract_query_signal(body_bytes: bytes) -> str:
    """
    Extract a search query from the request to find relevant vault context.
    Uses the last user message + any recent assistant context as signal.
    """
    try:
        data = json.loads(body_bytes)
    except:
        return ""

    messages = data.get("messages", [])
    if not messages:
        return ""

    # Get last user message
    last_user = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                last_user = content
            elif isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and "text" in p]
                last_user = " ".join(parts)
            break

    if not last_user:
        return ""

    # Truncate to reasonable query length (BM25 works better with focused queries)
    words = last_user.split()
    if len(words) > 50:
        last_user = " ".join(words[:50])

    return last_user


def inject_vault_context(body_bytes: bytes) -> Tuple[bytes, int, List[str]]:
    """
    Search vault index for relevant context and inject into the system prompt.
    Returns (new_body_bytes, injected_tokens, source_refs).
    """
    if not VAULT_INDEX.available:
        return body_bytes, 0, []

    query = extract_query_signal(body_bytes)
    if not query:
        return body_bytes, 0, []

    injection_text, tokens_used, source_refs = VAULT_INDEX.compile_injection(
        query, budget=INJECT_BUDGET, top_k=INJECT_TOP_K, min_score=INJECT_MIN_SCORE
    )

    if not injection_text:
        return body_bytes, 0, []

    try:
        data = json.loads(body_bytes)
    except:
        return body_bytes, 0, []

    # Inject into system prompt
    system = data.get("system", "")
    if isinstance(system, str):
        data["system"] = [
            {"type": "text", "text": system},
            {"type": "text", "text": injection_text, "cache_control": {"type": "ephemeral"}},
        ]
    elif isinstance(system, list):
        # Anthropic format: list of content blocks
        data["system"].append({
            "type": "text",
            "text": injection_text,
            "cache_control": {"type": "ephemeral"},
        })
    else:
        # No system prompt — add one
        data["system"] = injection_text

    new_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
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
    _COMPACT_CACHE[key] = t
    _COMPACT_CACHE_ORDER.append(key)
    if len(_COMPACT_CACHE_ORDER) > COMPACT_CACHE_SIZE:
        old = _COMPACT_CACHE_ORDER.pop(0)
        _COMPACT_CACHE.pop(old, None)
    return t


def compact_request_body(body_bytes: bytes):
    """
    Style-contract-aware compaction.
    Returns (new_body_bytes, sent_tokens, original_tokens, protected_token_count).
    """
    try:
        data = json.loads(body_bytes)
    except Exception:
        return body_bytes, 0, 0, 0

    _, original_tokens = extract_request_tokens(body_bytes)
    if original_tokens < COMPACT_THRESHOLD_TOKENS:
        return body_bytes, original_tokens, original_tokens, 0

    mode = COMPILATION_MODE
    if mode == "strict":
        return body_bytes, original_tokens, original_tokens, original_tokens

    protected_tokens = 0

    if isinstance(data.get("system"), str):
        protected_tokens += count_tokens(data["system"])
    elif isinstance(data.get("system"), list):
        for part in data["system"]:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                protected_tokens += count_tokens(part["text"])

    messages = data.get("messages", [])
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

    new_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    _, sent_tokens = extract_request_tokens(new_body)
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
            self._send_json({
                "status": "ok",
                "compilation_mode": COMPILATION_MODE,
                "vault_index": vault_info,
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
                "today": MONITOR.get_stats(),
                "by_model": MONITOR.get_by_model(),
                "recent": MONITOR.recent(10),
            })
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
        if self.path.startswith("http"):
            self._forward_request("GET")
        elif self.path.startswith("/ollama-proxy/"):
            self._ollama_proxy("GET")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("http"):
            self._forward_request("POST")
        elif self.path.startswith("/ollama-proxy/"):
            self._ollama_proxy("POST")
        elif self.path.startswith("/v1/"):
            self._reverse_proxy("POST")
        else:
            self.send_error(404)

    def do_PUT(self):
        if self.path.startswith("http"):
            self._forward_request("PUT")
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("http"):
            self._forward_request("DELETE")
        else:
            self.send_error(404)

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
        if "/v1/messages" in self.path or self.headers.get("x-api-key") or self.headers.get("anthropic-version"):
            base = "https://api.anthropic.com"
        elif self.headers.get("Authorization", "").startswith("Bearer ") and "/chat/completions" in self.path:
            base = "https://api.openai.com"
        else:
            base = "https://api.anthropic.com"
        self._proxy_to(base + self.path, method)

    def _proxy_to(self, target_url, method, force_intercept=False):
        t0 = time.time()
        from urllib.parse import urlparse
        parsed = urlparse(target_url)
        should_log = force_intercept or any(h in target_url for h in INTERCEPT_HOSTS)
        is_messages = "/messages" in target_url or "/chat/completions" in target_url
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        model = "unknown"
        input_tokens = 0
        sent_input_tokens = 0
        protected_tokens = 0
        injected_tokens = 0
        injected_sources: List[str] = []
        is_streaming = False
        cache_read_tokens = 0
        cache_creation_tokens = 0

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
            _original_body = body  # save for fallback
            try:
                model, input_tokens = extract_request_tokens(body)
                try:
                    req_data = json.loads(body)
                    is_streaming = req_data.get("stream", False)
                except:
                    pass

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
                    else:
                        body, injected_tokens, injected_sources = inject_vault_context(body)
                        if injected_tokens > 0:
                            # Recount tokens after injection
                            _, input_tokens = extract_request_tokens(body)
                            vault_stage.tokens_delta = injected_tokens
                            vault_stage.details["blocks_matched"] = len(injected_sources)
                            vault_stage.details["block_names"] = injected_sources[:5]  # Top 5
                            vault_stage.details["tokens_injected"] = injected_tokens
                vault_stage.output_tokens = input_tokens
                vault_stage.duration_ms = (time.time() - t_inject) * 1000
                if trace:
                    trace.stages.append(vault_stage)

                # Phase 2: Compaction (AFTER injection)
                t_compact = time.time()
                compaction_stage = StageTrace(
                    name="compaction",
                    enabled=ENABLE_COMPACTION,
                    input_tokens=input_tokens,
                )
                if ENABLE_COMPACTION:
                    body, sent_input_tokens, original_tokens, protected_tokens = compact_request_body(body)
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
            except Exception as _pipeline_err:
                print(f"  ⚠️ Pre-pipeline error (falling back to original body): {_pipeline_err}")
                body = _original_body  # restore original body so request still forwards
                model, input_tokens = extract_request_tokens(body)
                sent_input_tokens = input_tokens

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

        try:
            if parsed.scheme == "https":
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(parsed.netloc, timeout=300, context=ctx)
            else:
                conn = http.client.HTTPConnection(parsed.netloc, timeout=300)
            path = parsed.path
            if parsed.query:
                path += "?" + parsed.query
            conn.request(method, path, body=body, headers=fwd_headers)
            resp = conn.getresponse()
            status = resp.status
            content_type = resp.getheader("Content-Type", "")
            is_sse = "text/event-stream" in content_type

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
                import zlib as _zlib
                _ce = resp.getheader("Content-Encoding", "")
                _decomp = _zlib.decompressobj(_zlib.MAX_WBITS | 16) if "gzip" in _ce else None
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    chunk_count += 1
                    if _decomp:
                        try:
                            chunk = _decomp.decompress(chunk)
                        except Exception:
                            pass
                    if not chunk:
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
                    output_tokens = sse_usage.get("output_tokens", 0)
                    cache_read_tokens = sse_usage.get("cache_read_input_tokens", 0)
                    cache_creation_tokens = sse_usage.get("cache_creation_input_tokens", 0)
            else:
                resp_body = resp.read()
                self.wfile.write(resp_body)
                self.wfile.flush()
                output_tokens = 0
                if should_log and is_messages:
                    resp_for_metrics = resp_body
                    if "gzip" in resp.getheader("Content-Encoding", ""):
                        try:
                            resp_for_metrics = gzip.decompress(resp_body)
                        except:
                            pass
                    output_tokens = extract_response_tokens(resp_for_metrics)
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
                print(f"  📊 {model}{stream_tag}{mode_tag}{inject_tag}: {input_tokens:,} in → {sent_input_tokens:,} sent "
                      f"(saved {saved:,}, protected {protected_tokens:,}) / {output_tokens:,} out | "
                      f"~${cost:.4f} | {latency_ms}ms")

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

    def _send_json(self, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


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
