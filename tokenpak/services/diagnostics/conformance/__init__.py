"""TIP-1.0 self-conformance capture primitives.

This module is the single shared contract that proxy + companion emit
through so tests can validate live artifacts against the registry
schemas. There is no parallel tree: ``proxy/monitor.py``,
``proxy/middleware/*``, and ``companion/journal/store.py`` all notify
through the same helpers here.

Phase TIP-SC (2026-04-22) owns this module. Production emit paths gain
exactly one ``_notify_*`` call at the chokepoint; the notification is a
no-op when no observer is installed, so the release-default path pays
no cost.

Validation itself is delegated to the ``tokenpak-tip-validator`` PyPI
package (a ``[dev]`` extra). This module does not re-implement schema
validation — it only captures + forwards.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Mapping, Protocol, runtime_checkable


@runtime_checkable
class ConformanceObserver(Protocol):
    """Observer contract satisfied by the pytest conformance harness.

    Production code calls ``notify_*`` free functions below, which
    dispatch to the thread-local observer if one is installed.
    """

    def on_telemetry_row(self, row: Mapping[str, Any]) -> None: ...
    def on_response_headers(
        self, headers: Mapping[str, str], direction: str
    ) -> None: ...
    def on_companion_journal_row(self, row: Mapping[str, Any]) -> None: ...
    def on_capability_published(
        self, profile: str, caps: "list[str] | tuple[str, ...] | frozenset[str]"
    ) -> None: ...


_tls = threading.local()


def _get() -> "ConformanceObserver | None":
    return getattr(_tls, "observer", None)


def install(observer: ConformanceObserver) -> Callable[[], None]:
    """Install ``observer`` for the current thread. Returns uninstall callback.

    Designed for pytest fixtures: install at setup, uninstall in
    teardown. Thread-local isolation means parallel tests do not race.
    """
    prior = _get()
    _tls.observer = observer

    def _uninstall() -> None:
        _tls.observer = prior

    return _uninstall


def notify_telemetry_row(row: Mapping[str, Any]) -> None:
    """Forward a wire-side telemetry row to the active observer, if any."""
    obs = _get()
    if obs is not None:
        obs.on_telemetry_row(row)


def notify_response_headers(
    headers: Mapping[str, str], direction: str = "response"
) -> None:
    """Forward an outbound header set to the active observer, if any."""
    obs = _get()
    if obs is not None:
        obs.on_response_headers(headers, direction)


def notify_companion_journal_row(row: Mapping[str, Any]) -> None:
    """Forward a companion journal row to the active observer, if any."""
    obs = _get()
    if obs is not None:
        obs.on_companion_journal_row(row)


def notify_capability_published(
    profile: str, caps: "list[str] | tuple[str, ...] | frozenset[str]"
) -> None:
    """Forward a capability self-declaration at startup to the observer."""
    obs = _get()
    if obs is not None:
        obs.on_capability_published(profile, caps)


__all__ = [
    "ConformanceObserver",
    "install",
    "notify_telemetry_row",
    "notify_response_headers",
    "notify_companion_journal_row",
    "notify_capability_published",
    # SC-07 — doctor --conformance runner.
    "run_conformance_checks",
    "summarize",
    "exit_code_for",
]


# Re-export the SC-07 runner so ``tokenpak doctor --conformance`` and
# any other caller imports from the same diagnostics-layer namespace.
# Placed at the module bottom to avoid a circular import with
# runner.py (which imports the observer helpers above at call time,
# not at import time).
from tokenpak.services.diagnostics.conformance.runner import (  # noqa: E402
    exit_code_for,
    run_conformance_checks,
    summarize,
)
