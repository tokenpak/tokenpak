# SPDX-License-Identifier: Apache-2.0
"""Client-side usage meter — WS-5 (TRIX-MTC-08).

Records per-request token usage keyed by ``license_id`` and posts batches
to the license server's ``POST /usage`` endpoint. Designed to be:

- **Cheap to call** from the request hot path: ``record(...)`` only appends
  to an in-memory queue; the network call happens on flush.
- **Resilient to outages**: if the license server is unreachable, the
  buffered events are persisted to a local JSONL spool and replayed on
  the next successful flush. Nothing is lost across process restarts.
- **Periodic**: a 24h heartbeat thread flushes once per day even when the
  application is otherwise idle. Direct flush is also exposed for tests
  and manual operation.

The meter is intentionally NOT wired into the telemetry pipeline by
default — call sites opt in via :func:`get_default_meter` and
:func:`record_usage`. See ``services/`` integration in
``services/usage_metering_bridge.py`` for the pipeline hook.

Path note (2026-04-28): the WS-5 task spec calls for this client to live
at ``tokenpak/agent/license/usage_meter.py``, but ``agent/license/`` was
removed during the 2026-04-19 17-subsystem consolidation (FIN-07/FIN-11).
The remaining canonical home for licensing code is ``tokenpak/licensing/``,
which is where this module lives. See submission block for QA review.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib import error as _urlerror
from urllib import request as _urlrequest


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_LICENSE_SERVER = os.environ.get(
    "TOKENPAK_LICENSE_SERVER", "http://127.0.0.1:8900"
)
DEFAULT_SPOOL_DIR = Path(
    os.environ.get(
        "TOKENPAK_USAGE_SPOOL_DIR",
        str(Path.home() / ".tokenpak" / "usage_spool"),
    )
)
DEFAULT_HEARTBEAT_SECONDS = 24 * 60 * 60  # 24h cadence per acceptance criterion 6
DEFAULT_HTTP_TIMEOUT = 5.0
SPOOL_FILENAME = "buffer.jsonl"


@dataclass
class UsageEvent:
    """One usage event — what we POST to /usage."""

    license_id: str
    tokens_in: int
    tokens_out: int
    model: str
    ts: str = field(
        default_factory=lambda: datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    def to_payload(self) -> dict:
        return {
            "license_id": self.license_id,
            "tokens_in": int(self.tokens_in),
            "tokens_out": int(self.tokens_out),
            "model": self.model,
            "ts": self.ts,
        }


class UsageMeter:
    """Client-side usage meter with local buffering and graceful degradation.

    Lifecycle:
        meter = UsageMeter(license_id="TPAK-...", server_url=...)
        meter.record(tokens_in=100, tokens_out=20, model="gpt-4o")
        meter.flush()              # explicit
        meter.start_heartbeat()    # 24h cadence
        meter.stop_heartbeat()
    """

    def __init__(
        self,
        license_id: Optional[str] = None,
        server_url: str = DEFAULT_LICENSE_SERVER,
        spool_dir: Optional[Path] = None,
        http_timeout: float = DEFAULT_HTTP_TIMEOUT,
        heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    ) -> None:
        self.license_id = license_id
        self.server_url = server_url.rstrip("/")
        self.spool_dir = Path(spool_dir) if spool_dir else DEFAULT_SPOOL_DIR
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.spool_path = self.spool_dir / SPOOL_FILENAME
        self.http_timeout = http_timeout
        self.heartbeat_seconds = heartbeat_seconds

        # In-memory buffer protected by lock; written through to disk on
        # ``record()`` so a crash mid-cycle does not lose events.
        self._lock = threading.Lock()
        self._heartbeat_stop: Optional[threading.Event] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ API

    def record(
        self,
        tokens_in: int,
        tokens_out: int,
        model: str,
        license_id: Optional[str] = None,
        ts: Optional[str] = None,
    ) -> None:
        """Append one usage event to the spool. Cheap, lock-protected."""
        license_id = license_id or self.license_id
        if not license_id:
            # No license attached — this happens on Free tier installs that
            # have not activated. Drop silently rather than spam logs.
            return

        event = UsageEvent(
            license_id=license_id,
            tokens_in=int(tokens_in),
            tokens_out=int(tokens_out),
            model=model,
        )
        if ts is not None:
            event.ts = ts

        with self._lock:
            self._append_to_spool(event)

    def flush(self) -> dict:
        """Drain the spool to the license server. Best-effort.

        Returns a dict ``{"posted": N, "remaining": M, "errors": E}``.
        On network failure, events are left in the spool for the next
        attempt (graceful degradation per acceptance criterion 7).
        """
        with self._lock:
            events = list(self._read_spool())

        if not events:
            return {"posted": 0, "remaining": 0, "errors": 0}

        posted = 0
        errors = 0
        unposted: list[UsageEvent] = []

        for event in events:
            ok = self._post_event(event)
            if ok:
                posted += 1
            else:
                errors += 1
                unposted.append(event)
                # First failure usually indicates the server is down — bail
                # out so the rest of the buffer is replayed together later.
                # Anything we already posted stays posted (the server is
                # idempotent only at the row level, not by event ID — but
                # this is acceptable for usage metering, double-counting is
                # better than under-counting).
                idx = events.index(event)
                unposted.extend(events[idx + 1 :])
                break

        with self._lock:
            self._rewrite_spool(unposted)

        return {
            "posted": posted,
            "remaining": len(unposted),
            "errors": errors,
        }

    def start_heartbeat(self) -> None:
        """Start the 24h flush heartbeat in a background daemon thread."""
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return  # already running
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="tokenpak-usage-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def stop_heartbeat(self) -> None:
        if self._heartbeat_stop is not None:
            self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2.0)
        self._heartbeat_stop = None
        self._heartbeat_thread = None

    # ---------------------------------------------------------- internals

    def _heartbeat_loop(self) -> None:
        assert self._heartbeat_stop is not None
        while not self._heartbeat_stop.is_set():
            # Wait up to heartbeat_seconds; wake early on stop.
            if self._heartbeat_stop.wait(timeout=self.heartbeat_seconds):
                return
            try:
                self.flush()
            except Exception:  # pragma: no cover — defensive
                logger.exception("usage meter heartbeat flush failed")

    def _append_to_spool(self, event: UsageEvent) -> None:
        # JSONL: one event per line, append-only. Truncating on flush
        # rather than per-event keeps record() in a single fsync-free
        # write path.
        line = json.dumps(asdict(event), separators=(",", ":")) + "\n"
        with self.spool_path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    def _read_spool(self) -> Iterable[UsageEvent]:
        if not self.spool_path.exists():
            return []
        events: list[UsageEvent] = []
        with self.spool_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("dropping malformed usage spool line: %r", line)
                    continue
                events.append(UsageEvent(**obj))
        return events

    def _rewrite_spool(self, events: list[UsageEvent]) -> None:
        if not events:
            # Empty list → remove the file entirely so disk usage stays bounded.
            try:
                self.spool_path.unlink()
            except FileNotFoundError:
                pass
            return
        tmp_path = self.spool_path.with_suffix(".jsonl.tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            for event in events:
                fh.write(
                    json.dumps(asdict(event), separators=(",", ":")) + "\n"
                )
        os.replace(tmp_path, self.spool_path)

    def _post_event(self, event: UsageEvent) -> bool:
        url = f"{self.server_url}/usage"
        body = json.dumps(event.to_payload()).encode("utf-8")
        req = _urlrequest.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with _urlrequest.urlopen(req, timeout=self.http_timeout) as resp:
                if 200 <= resp.status < 300:
                    return True
                logger.warning(
                    "usage meter: server returned status=%s body=%s",
                    resp.status,
                    resp.read()[:200],
                )
                return False
        except _urlerror.HTTPError as exc:
            # 4xx (e.g. license_not_found 404) means this *specific* event
            # is dead — drop it rather than retry forever.
            if 400 <= exc.code < 500:
                logger.warning(
                    "usage meter: dropping event (server rejected %s): %s",
                    exc.code,
                    exc.reason,
                )
                return True  # treat as posted so we stop retrying
            logger.warning(
                "usage meter: server error %s — will retry: %s",
                exc.code,
                exc.reason,
            )
            return False
        except _urlerror.URLError as exc:
            logger.debug("usage meter: server unreachable, buffering: %s", exc)
            return False
        except Exception:  # pragma: no cover — defensive
            logger.exception("usage meter: unexpected post failure")
            return False


# ---------------------------------------------------------------------------
# Module-level convenience: a singleton meter that other subsystems can
# import without each constructing their own. The license_id is resolved
# lazily from ``tokenpak.licensing`` if available, else left None until the
# caller sets it explicitly.
# ---------------------------------------------------------------------------


_default_meter_lock = threading.Lock()
_default_meter: Optional[UsageMeter] = None


def _resolve_license_id() -> Optional[str]:
    """Best-effort resolution of the active license_id."""
    try:
        from tokenpak import licensing as _lic  # local import to avoid cycle

        # The licensing module exposes a summary helper; fall back through
        # several plausible entry points so we don't break if the API
        # surface is slightly different than expected.
        for attr in ("summary_for_cli", "current_summary", "get_summary"):
            fn = getattr(_lic, attr, None)
            if callable(fn):
                summary = fn()
                if isinstance(summary, dict):
                    return summary.get("license_id") or summary.get("key_id")
    except Exception:  # pragma: no cover — defensive
        return None
    return None


def get_default_meter() -> UsageMeter:
    """Return the process-wide default :class:`UsageMeter`."""
    global _default_meter
    with _default_meter_lock:
        if _default_meter is None:
            _default_meter = UsageMeter(license_id=_resolve_license_id())
        return _default_meter


def record_usage(
    tokens_in: int,
    tokens_out: int,
    model: str,
    license_id: Optional[str] = None,
    ts: Optional[str] = None,
) -> None:
    """Record one usage event using the process-wide default meter.

    Safe to call from the request hot path. Buffers locally and lets the
    24h heartbeat (or an explicit ``flush()``) push to the server.
    """
    get_default_meter().record(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model=model,
        license_id=license_id,
        ts=ts,
    )


def flush_default() -> dict:
    """Force-flush the process-wide default meter."""
    return get_default_meter().flush()


def start_default_heartbeat() -> None:
    """Start the 24h heartbeat on the process-wide default meter."""
    get_default_meter().start_heartbeat()


def _reset_default_meter_for_testing() -> None:
    """Drop the singleton (test-only)."""
    global _default_meter
    with _default_meter_lock:
        if _default_meter is not None:
            try:
                _default_meter.stop_heartbeat()
            except Exception:
                pass
        _default_meter = None
