"""
tokenpak.agent.routing.fallback
────────────────────────────────────────────────────────────────────────────────
Proxy-layer fallback bridge.

Bridges the proxy's provider routing with the agentic RetryEngine,
providing unified retry + fallback behavior for HTTP proxy requests.

This module is the "Hooks into proxy router" integration layer:
  - Wraps any provider call with RetryEngine escalation
  - Integrates FailoverManager for provider switching
  - Provides FallbackRouter — a drop-in helper for proxy routing that
    adds retry/fallback intelligence to any provider request

Architecture
────────────
  proxy.py / proxy.server
       │
       ▼
  FallbackRouter.call(request_fn, context)
       │
       ▼
  RetryEngine (agentic/retry.py)
   L0: backoff
   L1: model downgrade
   L2: provider switch  ←── FailoverManager (proxy/failover.py)
   L3: agent handoff
   L4: human alert

Usage
─────
  from tokenpak.agent.routing.fallback import FallbackRouter, fallback_call

  # Functional API (one-shot)
  result = fallback_call(
      fn=call_anthropic,
      context={"task": "chat", "model": "claude-opus-4-5", "provider": "anthropic"},
  )

  # Class API (persistent configuration)
  router = FallbackRouter(agent_id="proxy-worker")
  result = router.call(fn=call_anthropic, context=ctx)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional

from tokenpak.agent.agentic.retry import (
    RetryEngine,
    RetryExhaustedError,
    load_recent_retry_events,
)
from tokenpak.agent.proxy.failover import (
    FailoverManager,
    FailoverResult,
)

logger = logging.getLogger(__name__)


# ── Public exceptions ─────────────────────────────────────────────────────────


class FallbackExhaustedError(Exception):
    """Raised when all fallback options (retry + provider chain) are exhausted."""

    def __init__(self, context: dict, cause: RetryExhaustedError):
        self.context = context
        self.cause = cause
        super().__init__(
            f"All fallback options exhausted for task '{context.get('task', 'unknown')}': {cause}"
        )


# ── FallbackRouter ────────────────────────────────────────────────────────────


class FallbackRouter:
    """
    Proxy-layer fallback router.

    Wraps any provider request function with:
    - Exponential backoff (Level 0)
    - Model downgrade within provider (Level 1)
    - Provider switch via FailoverManager (Level 2)
    - Agent handoff (Level 3, if on_handoff provided)
    - Human alert (Level 4)

    Parameters
    ----------
    agent_id : str
        Identifier of the agent/proxy instance making calls.
    state_dir : Path | None
        Override state persistence directory.
    on_handoff : callable | None
        Hook: (context, partial_state) -> bool
    on_human_alert : callable | None
        Hook: (alert_dict) -> None
    failover_manager : FailoverManager | None
        Pre-configured failover manager (loads from config.yaml if None).
    """

    def __init__(
        self,
        agent_id: str = "proxy-worker",
        state_dir: Optional[Path] = None,
        on_handoff: Optional[Callable[[dict, dict], bool]] = None,
        on_human_alert: Optional[Callable[[dict], None]] = None,
        failover_manager: Optional[FailoverManager] = None,
    ):
        self.agent_id = agent_id
        self.state_dir = state_dir
        self.on_handoff = on_handoff
        self.on_human_alert = on_human_alert
        self._failover = failover_manager or FailoverManager()

    def _build_provider_switch_hook(
        self,
        original_model: str,
        original_provider: str,
    ) -> Optional[Callable[[str], str]]:
        """
        Build an on_provider_switch hook backed by FailoverManager.

        Returns None if failover is disabled (RetryEngine uses its own default).
        """
        if not self._failover.enabled:
            return None

        # Snapshot the full provider iteration at call time
        all_results: List[FailoverResult] = list(
            self._failover.iter_providers(original_model, preferred=original_provider)
        )
        provider_iter: Iterator[FailoverResult] = iter(all_results)
        # Advance past the first entry (the current/preferred provider)
        try:
            next(provider_iter)
        except StopIteration:
            return None

        def _switch(current_provider: str) -> str:
            try:
                result: FailoverResult = next(provider_iter)
                logger.info(
                    "FallbackRouter: switching provider %s → %s (model: %s → %s)",
                    current_provider,
                    result.provider,
                    original_model,
                    result.model,
                )
                return result.provider
            except StopIteration:
                # No more providers — return same provider to trigger
                # RetryEngine's "no fallback available" detection
                return current_provider

        return _switch

    def call(
        self,
        fn: Callable[[dict, dict], Any],
        context: dict,
        partial_state: Optional[dict] = None,
    ) -> Any:
        """
        Execute *fn* with full retry/fallback intelligence.

        Parameters
        ----------
        fn : callable
            Provider request function. Signature: fn(context, partial_state) -> result.
        context : dict
            Request context. Should include "task", "model", "provider", "task_id".
        partial_state : dict | None
            Mutable progress state (created fresh if None).

        Returns
        -------
        Any
            Result of fn on success.

        Raises
        ------
        FallbackExhaustedError
            When all escalation levels fail.
        """
        original_model = context.get("model", "unknown")
        original_provider = context.get("provider", "anthropic")

        provider_switch_hook = self._build_provider_switch_hook(original_model, original_provider)

        engine = RetryEngine(
            fn=fn,
            context=context,
            partial_state=partial_state,
            state_dir=self.state_dir,
            agent_id=self.agent_id,
            on_provider_switch=provider_switch_hook,
            on_handoff=self.on_handoff,
            on_human_alert=self.on_human_alert,
        )

        try:
            return engine.run()
        except RetryExhaustedError as exc:
            raise FallbackExhaustedError(context=context, cause=exc) from exc


# ── Functional API ────────────────────────────────────────────────────────────


def fallback_call(
    fn: Callable[[dict, dict], Any],
    context: dict,
    partial_state: Optional[dict] = None,
    agent_id: str = "proxy-worker",
    state_dir: Optional[Path] = None,
    on_handoff: Optional[Callable[[dict, dict], bool]] = None,
    on_human_alert: Optional[Callable[[dict], None]] = None,
) -> Any:
    """
    One-shot fallback call — convenience wrapper for FallbackRouter.

    Suitable for single requests where you don't need to maintain
    a persistent FallbackRouter instance.

    Raises FallbackExhaustedError if all levels fail.
    """
    router = FallbackRouter(
        agent_id=agent_id,
        state_dir=state_dir,
        on_handoff=on_handoff,
        on_human_alert=on_human_alert,
    )
    return router.call(fn=fn, context=context, partial_state=partial_state)


def get_recent_fallback_events(n: int = 20) -> list[dict]:
    """
    Return the most recent retry/fallback events from the JSONL shadow log.

    Convenience re-export for the proxy to surface events in `tokenpak status`.
    """
    return load_recent_retry_events(n)
