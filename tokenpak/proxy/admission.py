# SPDX-License-Identifier: Apache-2.0
"""Managed background-agent concurrency gate (local-only).

Bounds how many TokenPak-managed Claude Companion / background-agent model
requests run concurrently through the local proxy, queueing the excess FIFO
instead of letting a burst fan out unbounded through ``127.0.0.1:<port>``.

This sits *behind* the listener admission lease in ``tokenpak.proxy.server``:
the lease bounds the total managed connections the listener will hold at once
(reject-fast beyond it), while this gate bounds how many of those admitted
connections may execute in parallel (default 2), holding the rest in a
bounded FIFO queue until a running subagent completes and releases its slot.
Control-plane traffic (health/status/doctor/budget/journal) never passes
through either layer.

Operator configuration (durable preference, ``~/.tokenpak/config.yaml``)::

    companion:
      local_proxy_agent_concurrency:
        max_parallel_subagents: 2   # default when absent

Environment override (higher precedence, for fast recovery / scripted runs)::

    TOKENPAK_LOCAL_AGENT_CONCURRENCY=auto|off|<positive int>

``auto`` defers to config (default 2). ``off`` disables the gate entirely and
is an explicit operator opt-out — it is never the default. Any other value is
parsed as a positive integer cap; invalid values warn and fall back rather
than silently meaning "unlimited". When the local proxy is degraded (memory
guard / degradation tracker) or a provider circuit breaker is open, the
effective cap drops to 1 so recovery happens serially.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Callable, Optional, Tuple

logger = logging.getLogger("tokenpak.proxy.admission")

DEFAULT_MAX_PARALLEL = 2
ENV_OVERRIDE = "TOKENPAK_LOCAL_AGENT_CONCURRENCY"
CONFIG_KEY = "companion.local_proxy_agent_concurrency.max_parallel_subagents"

# How long a queued managed request may wait for a slot before receiving the
# structured busy response. Bounded by design: a queued request never hangs
# silently (packet requirement: no silent hangs, bounded/observable waits).
DEFAULT_QUEUE_WAIT_S = 30.0

ADMITTED = "admitted"
QUEUE_FULL = "queue_full"
WAIT_TIMEOUT = "timeout"


def _config_max_parallel() -> int:
    """Read the durable config cap; warn-and-fallback on invalid values."""
    try:
        from tokenpak.core.config_loader import get as _cfg

        raw = _cfg(CONFIG_KEY, DEFAULT_MAX_PARALLEL)
    except ImportError:
        raw = DEFAULT_MAX_PARALLEL
    try:
        if isinstance(raw, bool):  # bool is an int subclass; True would mean 1
            raise ValueError
        value = int(raw)
        if value < 1:
            raise ValueError
        return value
    except (TypeError, ValueError):
        logger.warning(
            "invalid %s=%r — falling back to default %d",
            CONFIG_KEY,
            raw,
            DEFAULT_MAX_PARALLEL,
        )
        return DEFAULT_MAX_PARALLEL


def resolve_agent_concurrency() -> Tuple[Optional[int], str]:
    """Resolve the managed-subagent parallel cap.

    Returns ``(cap, source)``. ``cap`` is ``None`` only when the operator
    explicitly set ``TOKENPAK_LOCAL_AGENT_CONCURRENCY=off``; every other
    path — including invalid values — resolves to a positive cap so invalid
    config can never silently mean unlimited.
    """
    raw = os.environ.get(ENV_OVERRIDE)
    if raw is not None:
        value = raw.strip().lower()
        if value == "off":
            return None, "env:off"
        if value == "auto":
            return _config_max_parallel(), "env:auto"
        try:
            cap = int(value)
            if cap >= 1:
                return cap, "env"
        except ValueError:
            pass
        logger.warning(
            "invalid %s=%r — falling back to config/default", ENV_OVERRIDE, raw
        )
    return _config_max_parallel(), "config"


def build_busy_response(reason: str, retry_after_s: int = 5) -> bytes:
    """Build a complete, correctly-framed 503 for gate overflow/timeouts.

    Always a full JSON document with explicit Content-Length so a throttled
    Claude Code client sees a structured local-busy error, never a
    connection reset, a bare timeout, or a truncated body.
    """
    body = json.dumps(
        {
            "error": {
                "type": "local_agent_concurrency_busy",
                "reason": reason,
                "message": (
                    "TokenPak local proxy is throttling managed background-agent "
                    "traffic; a slot opens when a running subagent completes. "
                    "Retry after the indicated delay, or lower "
                    "companion.local_proxy_agent_concurrency.max_parallel_subagents "
                    "to serialize recovery."
                ),
                "retry_after_seconds": retry_after_s,
            }
        }
    ).encode("utf-8")
    return (
        b"HTTP/1.1 503 Service Unavailable\r\n"
        b"Content-Type: application/json\r\n"
        b"Retry-After: " + str(retry_after_s).encode("ascii") + b"\r\n"
        b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
        b"Connection: close\r\n"
        b"\r\n" + body
    )


class AgentConcurrencyGate:
    """FIFO concurrency gate with a dynamic cap and bounded queue.

    ``acquire`` admits up to ``effective_cap()`` requests concurrently;
    later arrivals wait FIFO (bounded queue depth, bounded wait) and are
    admitted strictly in arrival order as slots release. The cap is dynamic:
    when ``degraded_probe`` reports a degraded local proxy the effective cap
    drops to 1, serializing managed traffic during recovery without any
    config change.
    """

    def __init__(
        self,
        max_parallel: int,
        max_queue: int,
        degraded_probe: Optional[Callable[[], bool]] = None,
        source: str = "config",
    ) -> None:
        if max_parallel < 1:
            raise ValueError("max_parallel must be >= 1")
        self._configured_cap = max_parallel
        self._max_queue = max(0, max_queue)
        self._degraded_probe = degraded_probe
        self.source = source
        self._cond = threading.Condition(threading.Lock())
        self._in_flight = 0
        self._queue: deque = deque()
        self.admitted_total = 0
        self.queued_total = 0
        self.rejected_queue_full = 0
        self.rejected_wait_timeout = 0

    # -- cap -----------------------------------------------------------------

    def effective_cap(self) -> int:
        """Current cap: 1 while the local proxy is degraded, else configured."""
        if self._degraded_probe is not None:
            try:
                if self._degraded_probe():
                    return 1
            except Exception:  # probe failure must never wedge admission
                pass
        return self._configured_cap

    # -- admission -----------------------------------------------------------

    def acquire(self, wait_timeout: float = DEFAULT_QUEUE_WAIT_S) -> str:
        """Admit, queue-then-admit, or reject the calling request.

        Returns ``ADMITTED`` (caller must ``release()`` when done),
        ``QUEUE_FULL`` (bounded queue is at depth), or ``WAIT_TIMEOUT``
        (bounded wait expired before a slot opened).
        """
        ticket = object()
        deadline = time.monotonic() + wait_timeout
        with self._cond:
            if not self._queue and self._in_flight < self.effective_cap():
                self._in_flight += 1
                self.admitted_total += 1
                return ADMITTED
            if len(self._queue) >= self._max_queue:
                self.rejected_queue_full += 1
                return QUEUE_FULL
            self._queue.append(ticket)
            self.queued_total += 1
            try:
                while True:
                    if self._queue[0] is ticket and self._in_flight < self.effective_cap():
                        self._queue.popleft()
                        self._in_flight += 1
                        self.admitted_total += 1
                        # Wake the next queued ticket in case capacity remains.
                        self._cond.notify_all()
                        return ADMITTED
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._queue.remove(ticket)
                        self.rejected_wait_timeout += 1
                        self._cond.notify_all()
                        return WAIT_TIMEOUT
                    self._cond.wait(remaining)
            except BaseException:
                # Interrupted while queued: leave no stale ticket behind.
                try:
                    self._queue.remove(ticket)
                except ValueError:
                    pass
                self._cond.notify_all()
                raise

    def release(self) -> None:
        """Release an admitted slot and wake the head of the queue."""
        with self._cond:
            if self._in_flight > 0:
                self._in_flight -= 1
            self._cond.notify_all()

    # -- observability -------------------------------------------------------

    def snapshot(self) -> dict:
        """Point-in-time gate state for /health, status, and doctor surfaces."""
        with self._cond:
            in_flight = self._in_flight
            queued = len(self._queue)
            admitted_total = self.admitted_total
            queued_total = self.queued_total
            rejected_queue_full = self.rejected_queue_full
            rejected_wait_timeout = self.rejected_wait_timeout
        effective = self.effective_cap()
        return {
            "enabled": True,
            "max_parallel_subagents": self._configured_cap,
            "effective_cap": effective,
            "degraded_serial": effective == 1 and self._configured_cap > 1,
            "in_flight": in_flight,
            "queued": queued,
            "queue_depth_max": self._max_queue,
            "admitted_total": admitted_total,
            "queued_total": queued_total,
            "rejected_queue_full": rejected_queue_full,
            "rejected_wait_timeout": rejected_wait_timeout,
            "source": self.source,
        }
