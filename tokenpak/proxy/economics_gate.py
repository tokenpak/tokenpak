"""Bound concurrent session-economics evaluations.

Every companion status line posts ``/v1/messages/session-economics`` about
every two seconds and gives up after half a second. The proxy serves each
request on its own thread and keeps evaluating after the client has gone, so
whenever one evaluation took longer than the poll interval, request threads
piled up, each holding open ledger connections and its own parse of the
ledger.

:class:`EconomicsGate` bounds that work:

- concurrent requests with the same key (session, model hint, frozen clock,
  ledger path) share one evaluation instead of starting their own;
- at most ``max_concurrent`` distinct evaluations run at once;
- a request that cannot start, or whose shared evaluation does not finish,
  within ``wait_s`` gets :class:`EconomicsBusy` (served as ``503`` with
  ``Retry-After``) rather than holding a thread.

So the threads this endpoint can occupy are bounded by
``max_concurrent`` plus the requests that arrive within ``wait_s``.

Configuration (read once, at first use):

- ``TOKENPAK_ECONOMICS_MAX_CONCURRENT`` (default 2, minimum 1)
- ``TOKENPAK_ECONOMICS_WAIT_S`` (default 1.0 seconds, minimum 0.05)
"""

from __future__ import annotations

import os
import socket
import threading
from typing import Callable, Hashable, Optional, TypeVar

T = TypeVar("T")

DEFAULT_MAX_CONCURRENT = 2
DEFAULT_WAIT_S = 1.0


class EconomicsBusy(Exception):
    """No evaluation slot became free (or a shared one did not finish) in time."""


class _Flight:
    __slots__ = ("done", "result", "failed")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: object = None
        self.failed = False


class EconomicsGate:
    """Single-flight plus a concurrency cap for one kind of evaluation."""

    def __init__(
        self, max_concurrent: int = DEFAULT_MAX_CONCURRENT, wait_s: float = DEFAULT_WAIT_S
    ):
        self.max_concurrent = max(1, int(max_concurrent))
        self.wait_s = max(0.05, float(wait_s))
        self._slots = threading.BoundedSemaphore(self.max_concurrent)
        self._lock = threading.Lock()
        self._flights: dict[Hashable, _Flight] = {}

    def run(self, key: Hashable, compute: Callable[[], T]) -> T:
        with self._lock:
            flight = self._flights.get(key)
            leader = flight is None
            if flight is None:
                flight = _Flight()
                self._flights[key] = flight
        if not leader:
            if not flight.done.wait(self.wait_s) or flight.failed:
                raise EconomicsBusy()
            return flight.result  # type: ignore[return-value]
        try:
            if not self._slots.acquire(timeout=self.wait_s):
                raise EconomicsBusy()
            try:
                result = compute()
            finally:
                self._slots.release()
            flight.result = result
            return result
        except BaseException:
            flight.failed = True
            raise
        finally:
            with self._lock:
                if self._flights.get(key) is flight:
                    del self._flights[key]
            flight.done.set()

    def in_flight(self) -> int:
        with self._lock:
            return len(self._flights)


def client_disconnected(sock: socket.socket) -> bool:
    """True when the peer has already closed its end of ``sock``.

    A status line that gave up after its timeout has closed the connection
    by the time a busy proxy reaches its request; evaluating it then only
    burns CPU for an answer nobody reads. Never blocks.
    """
    try:
        return sock.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
    except (BlockingIOError, InterruptedError):
        return False
    except OSError:
        return True


def _env_number(name: str, default: float, cast: Callable[[str], float]) -> float:
    try:
        return cast(os.environ.get(name, "") or str(default))
    except ValueError:
        return default


_GATE: Optional[EconomicsGate] = None
_GATE_LOCK = threading.Lock()


def get_economics_gate() -> EconomicsGate:
    """The process-wide gate for the session-economics endpoint."""
    global _GATE
    if _GATE is None:
        with _GATE_LOCK:
            if _GATE is None:
                _GATE = EconomicsGate(
                    max_concurrent=int(
                        _env_number(
                            "TOKENPAK_ECONOMICS_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT, int
                        )
                    ),
                    wait_s=_env_number("TOKENPAK_ECONOMICS_WAIT_S", DEFAULT_WAIT_S, float),
                )
    return _GATE


__all__ = [
    "DEFAULT_MAX_CONCURRENT",
    "DEFAULT_WAIT_S",
    "EconomicsBusy",
    "EconomicsGate",
    "client_disconnected",
    "get_economics_gate",
]
