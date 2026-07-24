"""
tokenpak.routing.fallback
────────────────────────────────
Proxy-layer fallback bridge.

Wraps :class:`~tokenpak.orchestration.retry.RetryEngine` with a
simpler, context-oriented API for the proxy layer.  Consumers work
with a single ``FallbackRouter`` instance (or the ``fallback_call``
convenience function) and never touch the lower-level engine directly.

Key additions over raw RetryEngine
-----------------------------------
- ``FallbackExhaustedError`` — raised instead of ``RetryExhaustedError``;
  carries ``context`` and ``cause`` for upstream error reporting.
- Handoff support — ``on_handoff`` returning ``True`` short-circuits the
  exhaustion path and returns ``{"_handoff": True}``.
- FailoverManager integration — when a ``FailoverManager`` is attached
  and enabled, its ``iter_providers`` drives the provider-switch hook.
- Functional API — ``fallback_call`` for one-shot use.
- ``get_recent_fallback_events`` — thin wrapper around
  ``load_recent_retry_events`` for external consumers.

Usage
------
    router = FallbackRouter(state_dir=Path("/tmp/state"))
    result = router.call(fn=my_task, context={"task": "...", "model": "..."})

    # or functional:
    result = fallback_call(fn=my_task, context=..., state_dir=...)
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Callable, Optional, Protocol, TypeVar, cast

from tokenpak.orchestration.retry import (
    ImmediateAlertError,
    RetryEngine,
    RetryExhaustedError,
    load_recent_retry_events,
)

logger = logging.getLogger(__name__)

FallbackContext = dict[str, object]
FallbackResult = TypeVar("FallbackResult")


class _ProviderEntry(Protocol):
    provider: str


class _FailoverManager(Protocol):
    @property
    def enabled(self) -> bool: ...

    def iter_providers(self, model: str, *, preferred: str) -> Iterable[_ProviderEntry]: ...


__all__ = [
    "FallbackRouter",
    "FallbackExhaustedError",
    "fallback_call",
    "get_recent_fallback_events",
]


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class FallbackExhaustedError(Exception):
    """
    All fallback levels exhausted.

    Attributes:
        context:  The task context dict passed to the router.
        cause:    The underlying :class:`RetryExhaustedError` (or
                  :class:`ImmediateAlertError` for auth failures).
    """

    def __init__(
        self,
        context: FallbackContext,
        cause: Exception,
    ) -> None:
        self.context = context
        self.cause = cause
        task = context.get("task", "unknown")
        super().__init__(f"Fallback exhausted for task '{task}': {cause}")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class FallbackRouter:
    """
    High-level fallback router for the proxy layer.

    Parameters
    ----------
    state_dir:
        Directory for persisting partial state on failure.
        Defaults to ``~/.tokenpak/retry_state``.
    failover_manager:
        Optional :class:`~tokenpak.proxy.failover.FailoverManager`.
        When attached and enabled, its ``iter_providers`` drives the
        provider-switch hook.
    on_handoff:
        Called when Level 3 (handoff) is reached.
        Signature: ``(context, partial_state) -> bool``.
        Return ``True`` to accept the handoff (router returns
        ``{"_handoff": True}``).  Return ``False`` to escalate further.
    on_human_alert:
        Called when Level 4 (human alert) is reached.
        Signature: ``(alert_dict) -> None``.
    """

    def __init__(
        self,
        state_dir: Optional[Path | str] = None,
        failover_manager: Optional[_FailoverManager] = None,
        on_handoff: Optional[Callable[[FallbackContext, FallbackContext], bool]] = None,
        on_human_alert: Optional[Callable[[FallbackContext], None]] = None,
    ) -> None:
        self._state_dir = Path(state_dir) if state_dir is not None else None
        self._failover_manager = failover_manager
        self.on_handoff = on_handoff
        self.on_human_alert = on_human_alert

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _make_provider_switch_hook(
        self, context: FallbackContext
    ) -> Optional[Callable[[str], str]]:
        """Build a provider-switch hook from the FailoverManager if available."""
        mgr = self._failover_manager
        if mgr is None or not mgr.enabled:
            return None

        model_value = context.get("model", "")
        provider_value = context.get("provider", "anthropic")
        model = model_value if isinstance(model_value, str) else ""
        preferred = provider_value if isinstance(provider_value, str) else "anthropic"

        # Materialise the iterator once so the hook can step through it.
        try:
            providers = list(mgr.iter_providers(model, preferred=preferred))
        except Exception:  # noqa: BLE001
            return None

        _iter = iter(providers)
        # Advance past the first entry (already being used)
        next(_iter, None)

        def _switch(_current: str) -> str:
            entry = next(_iter, None)
            if entry is None:
                return _current
            return entry.provider

        return _switch

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def call(
        self,
        fn: Callable[[FallbackContext, FallbackContext], FallbackResult],
        context: FallbackContext,
        partial_state: Optional[FallbackContext] = None,
    ) -> FallbackResult | dict[str, bool]:
        """
        Execute *fn* with automatic fallback.

        Parameters
        ----------
        fn:
            Task callable.  Signature: ``fn(context, partial_state) -> result``.
        context:
            Task metadata (``task``, ``model``, ``provider``, …).
        partial_state:
            Optional mutable state dict; created fresh if omitted.

        Returns
        -------
        Any
            The result from *fn* on success, or ``{"_handoff": True}`` if
            a handoff was accepted.

        Raises
        ------
        FallbackExhaustedError
            When all fallback levels have been tried and failed.
        """
        on_provider_switch = self._make_provider_switch_hook(context)

        handoff_result: list[dict[str, bool]] = []  # Mutable container for closure

        def _on_handoff(ctx: FallbackContext, state: FallbackContext) -> bool:
            if self.on_handoff is not None:
                accepted = self.on_handoff(ctx, state)
                if accepted:
                    handoff_result.append({"_handoff": True})
                    return True
            return False

        engine = RetryEngine(
            fn=fn,
            context=context,
            partial_state=partial_state or {},
            state_dir=self._state_dir,
            on_handoff=_on_handoff if self.on_handoff is not None else None,
            on_human_alert=self.on_human_alert,
            on_provider_switch=on_provider_switch,
        )

        try:
            result = cast(FallbackResult, engine.run())
            # If a handoff was accepted during run(), return its sentinel.
            if handoff_result:
                return handoff_result[0]
            return result
        except RetryExhaustedError as exc:
            # Check if a handoff was accepted mid-run
            if handoff_result:
                return handoff_result[0]
            raise FallbackExhaustedError(context=context, cause=exc) from exc
        except ImmediateAlertError as exc:
            # Auth / fatal errors bypassed escalation → wrap and re-raise
            if self.on_human_alert is not None:
                self.on_human_alert(
                    {
                        "severity": "critical",
                        "task": context.get("task"),
                        "error": str(exc.original),
                        "http_status": exc.status_code,
                    }
                )
            raise FallbackExhaustedError(context=context, cause=exc) from exc


# ---------------------------------------------------------------------------
# Functional API
# ---------------------------------------------------------------------------


def fallback_call(
    fn: Callable[[FallbackContext, FallbackContext], FallbackResult],
    context: FallbackContext,
    state_dir: Optional[Path | str] = None,
    on_human_alert: Optional[Callable[[FallbackContext], None]] = None,
    on_handoff: Optional[Callable[[FallbackContext, FallbackContext], bool]] = None,
    failover_manager: Optional[_FailoverManager] = None,
) -> FallbackResult | dict[str, bool]:
    """
    One-shot fallback call.  Convenience wrapper around :class:`FallbackRouter`.

    Parameters
    ----------
    fn:
        Task callable (same as ``FallbackRouter.call``).
    context:
        Task metadata dict.
    state_dir:
        Optional state persistence directory.
    on_human_alert:
        Optional human-alert callback.
    on_handoff:
        Optional handoff callback.
    failover_manager:
        Optional FailoverManager.

    Returns
    -------
    Any
        Result from *fn*.

    Raises
    ------
    FallbackExhaustedError
        When all fallback levels have been tried and failed.
    """
    router = FallbackRouter(
        state_dir=state_dir,
        failover_manager=failover_manager,
        on_handoff=on_handoff,
        on_human_alert=on_human_alert,
    )
    return router.call(fn=fn, context=context)


# ---------------------------------------------------------------------------
# Event log access
# ---------------------------------------------------------------------------


def get_recent_fallback_events(n: int = 20) -> list[dict[str, object]]:
    """
    Return up to *n* most-recent retry/fallback events from the JSONL log.

    Delegates to :func:`tokenpak.orchestration.retry.load_recent_retry_events`.
    """
    return load_recent_retry_events(n)
