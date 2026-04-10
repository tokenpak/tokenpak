# SPDX-License-Identifier: Apache-2.0
"""Shadow Reader — Passive request observation for TokenPak Phase 3.

Allows observing requests WITHOUT executing them. Useful for:
- Safe production testing of new compression rules
- Gradual rollout of validation changes
- Analyzing request patterns without side effects
- Collecting metrics for future optimization

Shadow mode is PASSIVE: reads requests, logs observations, does NOT modify behavior.
"""

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SHADOW_MODE = os.environ.get("TOKENPAK_SHADOW_MODE", "").lower() == "true"
SHADOW_LOG_PATH = Path(
    os.environ.get(
        "TOKENPAK_SHADOW_LOG", str(Path.home() / ".tokenpak" / "shadow_observations.jsonl")
    )
)

# Enable/disable per-category logging in shadow mode
SHADOW_LOG_REQUESTS = os.environ.get("TOKENPAK_SHADOW_LOG_REQUESTS", "true").lower() == "true"
SHADOW_LOG_RESPONSES = os.environ.get("TOKENPAK_SHADOW_LOG_RESPONSES", "true").lower() == "true"
SHADOW_LOG_METRICS = os.environ.get("TOKENPAK_SHADOW_LOG_METRICS", "true").lower() == "true"

# Batch size before flushing to disk (non-blocking write in background)
SHADOW_BATCH_SIZE = int(os.environ.get("TOKENPAK_SHADOW_BATCH_SIZE", "50"))

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Types
# ---------------------------------------------------------------------------


@dataclass
class ShadowObservation:
    """Single shadow observation record."""

    timestamp: str  # ISO 8601
    observation_id: str  # Unique ID for this record
    mode: str  # "request", "response", "metric"

    # Request fields (if mode == "request")
    request_method: Optional[str] = None
    request_path: Optional[str] = None
    request_headers: Optional[Dict[str, str]] = None
    request_body_size: Optional[int] = None
    request_model: Optional[str] = None

    # Response fields (if mode == "response")
    response_status: Optional[int] = None
    response_headers: Optional[Dict[str, str]] = None
    response_body_size: Optional[int] = None
    response_latency_ms: Optional[float] = None

    # Metric fields (if mode == "metric")
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    metric_tags: Optional[Dict[str, str]] = field(default_factory=dict)

    # Analysis fields (populated after observation)
    compression_applicable: Optional[bool] = None
    compression_gain_tokens: Optional[int] = None
    estimated_cost_change: Optional[float] = None
    safety_concern: Optional[str] = None  # "protected", "config", etc. if not applicable


# ---------------------------------------------------------------------------
# Shadow Reader Instance
# ---------------------------------------------------------------------------


class ShadowReader:
    """Passive request observer for Phase 3 testing."""

    def __init__(self, shadow_log_path: Path = None):
        self.enabled = SHADOW_MODE
        self.log_path = shadow_log_path or SHADOW_LOG_PATH
        self.log_requests = SHADOW_LOG_REQUESTS
        self.log_responses = SHADOW_LOG_RESPONSES
        self.log_metrics = SHADOW_LOG_METRICS
        self.batch_size = SHADOW_BATCH_SIZE

        # In-memory buffer (flushed periodically)
        self._buffer: List[ShadowObservation] = []
        self._buffer_lock = threading.Lock()
        self._flush_thread = None
        self._stop_flush = threading.Event()

        # Statistics
        self._stats = {
            "observations_logged": 0,
            "bytes_written": 0,
            "flush_count": 0,
            "last_flush": None,
        }
        self._stats_lock = threading.Lock()

        # Ensure log directory exists
        if self.enabled:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Shadow Reader initialized: {self.log_path}")

        # Start background flush thread
        if self.enabled:
            self._start_flush_thread()

    def _start_flush_thread(self):
        """Start background thread to flush observations every 5 seconds."""

        def flush_loop():
            while not self._stop_flush.is_set():
                time.sleep(5)  # Flush every 5 sec
                self.flush()

        self._flush_thread = threading.Thread(target=flush_loop, daemon=True)
        self._flush_thread.start()

    def observe_request(
        self,
        method: str,
        path: str,
        headers: Dict[str, str],
        body_size: int,
        model: Optional[str] = None,
    ) -> str:
        """Log incoming request observation. Returns observation_id."""
        if not self.enabled or not self.log_requests:
            return ""

        obs_id = self._gen_obs_id()
        obs = ShadowObservation(
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            observation_id=obs_id,
            mode="request",
            request_method=method,
            request_path=path,
            request_headers=dict(headers),  # Copy to avoid mutations
            request_body_size=body_size,
            request_model=model,
        )

        self._add_to_buffer(obs)
        return obs_id

    def observe_response(
        self,
        obs_id: str,
        status: int,
        headers: Dict[str, str],
        body_size: int,
        latency_ms: float,
    ):
        """Log outgoing response observation."""
        if not self.enabled or not self.log_responses:
            return

        obs = ShadowObservation(
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            observation_id=obs_id or self._gen_obs_id(),
            mode="response",
            response_status=status,
            response_headers=dict(headers),
            response_body_size=body_size,
            response_latency_ms=latency_ms,
        )

        self._add_to_buffer(obs)

    def observe_metric(
        self,
        metric_name: str,
        metric_value: float,
        tags: Optional[Dict[str, str]] = None,
    ):
        """Log a metric observation."""
        if not self.enabled or not self.log_metrics:
            return

        obs = ShadowObservation(
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            observation_id=self._gen_obs_id(),
            mode="metric",
            metric_name=metric_name,
            metric_value=metric_value,
            metric_tags=tags or {},
        )

        self._add_to_buffer(obs)

    def mark_compression_analysis(
        self,
        obs_id: str,
        applicable: bool,
        gain_tokens: Optional[int] = None,
        cost_change: Optional[float] = None,
        safety_concern: Optional[str] = None,
    ):
        """Annotate observation with post-processing analysis."""
        if not self.enabled:
            return

        with self._buffer_lock:
            for obs in self._buffer:
                if obs.observation_id == obs_id:
                    obs.compression_applicable = applicable
                    obs.compression_gain_tokens = gain_tokens
                    obs.estimated_cost_change = cost_change
                    obs.safety_concern = safety_concern
                    break

    def _add_to_buffer(self, obs: ShadowObservation):
        """Add observation to buffer. Flush if batch size reached."""
        with self._buffer_lock:
            self._buffer.append(obs)
            if len(self._buffer) >= self.batch_size:
                self._flush_locked()

    def _flush_locked(self):
        """Flush buffer to disk. MUST be called with _buffer_lock held."""
        if not self._buffer:
            return

        try:
            buffer_copy = self._buffer[:]
            self._buffer.clear()

            # Write in background to avoid blocking
            threading.Thread(
                target=self._write_observations,
                args=(buffer_copy,),
                daemon=True,
            ).start()
        except Exception as e:
            logger.error(f"Shadow flush error: {e}")

    def flush(self):
        """Explicit flush (thread-safe)."""
        with self._buffer_lock:
            self._flush_locked()

    def _write_observations(self, observations: List[ShadowObservation]):
        """Write observations to JSONL file (non-blocking)."""
        try:
            with open(self.log_path, "a") as f:
                for obs in observations:
                    line = json.dumps(asdict(obs))
                    f.write(line + "\n")

                    with self._stats_lock:
                        self._stats["observations_logged"] += 1
                        self._stats["bytes_written"] += len(line) + 1

            with self._stats_lock:
                self._stats["flush_count"] += 1
                self._stats["last_flush"] = (
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                )
        except Exception as e:
            logger.error(f"Shadow write error: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Return shadow reader statistics."""
        with self._stats_lock:
            return dict(self._stats)

    def stop(self):
        """Stop the flush thread cleanly."""
        if self.enabled and self._flush_thread:
            self.flush()  # Final flush
            self._stop_flush.set()
            self._flush_thread.join(timeout=2)

    @staticmethod
    def _gen_obs_id() -> str:
        """Generate unique observation ID."""
        import uuid

        return str(uuid.uuid4())[:8]


# ---------------------------------------------------------------------------
# Singleton Instance
# ---------------------------------------------------------------------------

_shadow_reader: Optional[ShadowReader] = None


def get_shadow_reader() -> ShadowReader:
    """Get or create the shadow reader singleton."""
    global _shadow_reader
    if _shadow_reader is None:
        _shadow_reader = ShadowReader()
    return _shadow_reader


def is_shadow_mode_enabled() -> bool:
    """Check if shadow mode is enabled."""
    return SHADOW_MODE


# ---------------------------------------------------------------------------
# Example Usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Enable shadow mode for demo
    os.environ["TOKENPAK_SHADOW_MODE"] = "true"

    reader = get_shadow_reader()

    # Simulate a request
    obs_id = reader.observe_request(
        method="POST",
        path="/v1/messages",
        headers={"content-type": "application/json", "authorization": "Bearer xxx"},
        body_size=1024,
        model="claude-3-opus",
    )
    print(f"Logged request observation: {obs_id}")

    # Simulate response
    reader.observe_response(
        obs_id=obs_id,
        status=200,
        headers={"content-type": "application/json"},
        body_size=512,
        latency_ms=250.5,
    )
    print(f"Logged response for {obs_id}")

    # Simulate metric
    reader.observe_metric(
        "compression.savings",
        42.5,
        tags={"model": "claude-3-opus", "category": "code"},
    )

    # Mark analysis
    reader.mark_compression_analysis(
        obs_id=obs_id,
        applicable=True,
        gain_tokens=128,
        cost_change=-0.012,
    )

    # Flush and show stats
    reader.flush()
    time.sleep(1)
    print(f"\nStats: {json.dumps(reader.get_stats(), indent=2)}")

    # Read a few lines from the log
    if reader.log_path.exists():
        print(f"\nFirst 3 observations from {reader.log_path}:")
        with open(reader.log_path) as f:
            for i, line in enumerate(f):
                if i >= 3:
                    break
                record = json.loads(line)
                print(
                    f"  [{i+1}] {record['mode']:8} id={record['observation_id']} ts={record['timestamp']}"
                )
