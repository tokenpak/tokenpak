# SPDX-License-Identifier: Apache-2.0
"""Cached, non-blocking, honest status source for the interactive menu.

Cumulative-spec section D (status strip) + design-pass honesty fix.

Why this module exists
----------------------
The previous status strip (``menu.py:_status_strip``) issued **synchronous**
``urlopen(/health, /stats, timeout=1)`` probes on *every* redraw. That stalls
the whole menu specifically during the proxy's ~2.5-minute vault-load boot
window (socket open, no response yet) — and, on the cold path, printed a
fabricated ``Saved $0.00``, which violates the truth-over-polish rule.

This module replaces that with a lazy, TTL'd, backoff-protected cache that the
main render loop reads instantly. It **never** fabricates a value (truth over
polish): unknown metrics render as ``None`` here (the caller prints ``—`` /
``Unknown`` / last-good), never ``$0.00``.

Design invariants honored:
- D1  cached values render instantly; the redraw never blocks on a probe.
- D2  TTL split: health ~1.5s, stats ~7s.
- D3  refresh timeout 300ms (hard < 500ms).
- D4  failed-refresh backoff ~3s; surface ``Starting`` (timeout) vs ``Stopped``
      (connection refused) vs last-good while backing off.
- D5  single-writer: only the caller (main loop) invokes ``snapshot()``; there
      is no background thread. Refresh is lazy "on stale".
- D6  port from ``TOKENPAK_PORT`` / config — never hardcoded in display code.
- D7  honesty: unknown -> ``None``; the strip shows ``—``/``Unknown``/last-good.

Zero external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional, cast

# Schema version for the machine-readable (``--json``) snapshot. Bump on any
# field rename/removal so consumers can pin. (spec F3)
STATUS_SCHEMA_VERSION = 1

_HEALTH_TTL = 1.5  # D2
_STATS_TTL = 7.0  # D2
_BACKOFF = 3.0  # D4
_TIMEOUT = 0.3  # D3 (300ms; hard ceiling < 500ms)


def _port() -> int:
    """Canonical proxy port — config / ``TOKENPAK_PORT``, never hardcoded (D6)."""
    try:
        return int(os.environ.get("TOKENPAK_PORT", "8766"))
    except (TypeError, ValueError):
        return 8766


def _monotonic() -> float:
    # Wrapped so tests can monkeypatch a deterministic clock.
    return time.monotonic()


@dataclass
class ProxyStatus:
    """Honest proxy state. ``None`` means *unknown* — never fabricate."""

    state: str  # "running" | "stopped" | "starting" | "unknown"
    cost: Optional[float] = None  # today's spend; None when unknown
    saved: Optional[float] = None  # today's savings; None when unknown


class StatusCache:
    """Lazy, TTL'd, backoff-protected proxy-status cache (single-writer)."""

    def __init__(self) -> None:
        self._health: Optional[dict[str, Any]] = None
        self._health_at: float = 0.0
        self._health_state: str = "unknown"
        self._stats: Optional[dict[str, Any]] = None
        self._stats_at: float = 0.0
        self._backoff_until: float = 0.0

    # -- internal probes ---------------------------------------------------
    def _get(self, path: str) -> Optional[dict[str, Any]]:
        url = f"http://127.0.0.1:{_port()}{path}"
        try:
            resp = urllib.request.urlopen(url, timeout=_TIMEOUT)  # noqa: S310 (localhost)
            raw = resp.read()
            if resp.status != 200:
                return None
            return cast(dict[str, Any], json.loads(raw.decode() or "{}")) if raw else {}
        except Exception:  # noqa: BLE001 — re-raised classification happens in caller
            raise

    def _refresh_health(self, now: float) -> None:
        if now < self._backoff_until:
            return  # D4: respect backoff, keep last-good / starting
        if self._health is not None and (now - self._health_at) < _HEALTH_TTL:
            return  # fresh enough (D2)
        try:
            data = self._get("/health")
            self._health = data if data is not None else {}
            self._health_at = now
            self._health_state = "running"
            self._backoff_until = 0.0
        except (TimeoutError, OSError) as exc:
            # Distinguish boot window (timeout) from stopped (refused). (D4)
            self._backoff_until = now + _BACKOFF
            if isinstance(exc, urllib.error.URLError):
                exc = getattr(exc, "reason", exc)
            if isinstance(exc, (TimeoutError,)) or "timed out" in str(exc).lower():
                self._health_state = "starting"
            elif isinstance(exc, ConnectionRefusedError) or "refused" in str(exc).lower():
                self._health_state = "stopped"
            else:
                # Keep last-known state if we had one; else unknown.
                self._health_state = self._health_state if self._health else "unknown"
            self._health = None
        except Exception:  # noqa: BLE001
            self._backoff_until = now + _BACKOFF
            self._health = None
            self._health_state = (
                self._health_state if self._health_state != "unknown" else "unknown"
            )

    def _refresh_stats(self, now: float) -> None:
        if now < self._backoff_until:
            return
        if self._stats is not None and (now - self._stats_at) < _STATS_TTL:
            return
        if self._health_state != "running":
            return  # no point probing stats when the proxy isn't up
        try:
            data = self._get("/stats")
            self._stats = data if data is not None else {}
            self._stats_at = now
        except Exception:  # noqa: BLE001
            # Don't clobber last-good stats on a transient miss; just don't update.
            self._backoff_until = now + _BACKOFF

    # -- public ------------------------------------------------------------
    def snapshot(self, *, probe: bool = True) -> ProxyStatus:
        """Return the current honest status. Never blocks beyond ``_TIMEOUT``.

        ``probe=False`` reads only already-cached state (no network) — used by
        the ``--json`` path so it is instant and deterministic (a fresh process
        with nothing cached reports ``unknown``, never a fabricated value).
        """
        if probe:
            now = _monotonic()
            self._refresh_health(now)
            self._refresh_stats(now)

        cost: Optional[float] = None
        saved: Optional[float] = None
        if self._health_state == "running" and self._stats is not None:
            raw_cost = self._stats.get("cost")
            raw_saved = self._stats.get("cost_saved")
            cost = float(raw_cost) if isinstance(raw_cost, (int, float)) else None
            saved = float(raw_saved) if isinstance(raw_saved, (int, float)) else None
        return ProxyStatus(state=self._health_state, cost=cost, saved=saved)


# Module singleton. D5: only the main render loop calls ``snapshot()``; there is
# no background thread, so this needs no lock.
_CACHE = StatusCache()


def snapshot(*, probe: bool = True) -> ProxyStatus:
    """Process-wide honest status snapshot (cached, non-blocking)."""
    return _CACHE.snapshot(probe=probe)


def json_snapshot() -> dict[str, Any]:
    """Deterministic, schema-versioned status dict for ``tokenpak --json`` (F3).

    Cheap: reads only cached state (no fresh probe is forced), emits stable
    field names, and never fabricates a savings figure (unknown -> null).
    """
    s = snapshot(probe=False)
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "proxy": s.state,  # running|stopped|starting|unknown
        "cost_today": s.cost,  # may be null (honesty — D7)
        "saved_today": s.saved,  # may be null
        "port": _port(),
    }


def reset_cache() -> None:
    """Test hook — clear the module singleton's cached state."""
    global _CACHE
    _CACHE = StatusCache()
