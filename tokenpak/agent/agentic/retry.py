"""
tokenpak.agent.agentic.retry
─────────────────────────────
Automatic retry with 5-level escalation:

  Level 0: wait + retry (exponential backoff)
  Level 1: downgrade model (e.g. sonnet → haiku)
  Level 2: switch provider
  Level 3: handoff to another agent (saves state)
  Level 4: save partial state + alert human

Never silently fails: every failure path is logged; the final level
surfaces a human-readable alert with full context.

Usage
-----
    engine = RetryEngine(
        fn=my_task,
        context={"task": "...", "args": {...}},
        state_dir=Path("~/.tokenpak/retry_state"),
    )
    result = engine.run()          # raises RetryExhaustedError on final failure

Hooks
-----
Callers may pass callables to intercept escalation decisions:
    on_model_downgrade(current_model) -> str   (return next model)
    on_provider_switch(current_provider) -> str
    on_handoff(context, partial_state) -> bool  (True = handoff accepted)
    on_human_alert(alert: dict) -> None
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_STATE_DIR = Path.home() / ".tokenpak" / "retry_state"

MODEL_DOWNGRADE_PATH: list[str] = [
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "gpt-4o",
    "gpt-4o-mini",
]

PROVIDER_FALLBACK_PATH: list[str] = [
    "anthropic",
    "openai",
    "google",
]


class RetryExhaustedError(Exception):
    """Raised when all 5 escalation levels have failed."""

    def __init__(self, context: dict, partial_state: dict, attempts: list[dict]):
        self.context = context
        self.partial_state = partial_state
        self.attempts = attempts
        super().__init__(
            f"All retry levels exhausted after {len(attempts)} attempts. "
            f"Partial state saved. Human alert sent."
        )


@dataclass
class RetryAttempt:
    level: int
    description: str
    error: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "description": self.description,
            "error": self.error,
            "timestamp": self.timestamp,
        }


class RetryEngine:
    """
    5-level retry engine with escalation and partial-state preservation.

    Parameters
    ----------
    fn : callable
        The task function. Signature: fn(context, partial_state) -> result.
        Should update partial_state in-place as it makes progress.
    context : dict
        Task metadata (task name, args, agent_id, etc.).
    partial_state : dict | None
        Mutable state tracking progress. Created fresh if None.
    state_dir : Path | None
        Where to persist partial state on failure.
    agent_id : str | None
        Current agent identifier.
    wait_seconds : list[float]
        Wait times between Level-0 retries.
    on_model_downgrade : callable | None
        Hook: (current_model) -> next_model string.
    on_provider_switch : callable | None
        Hook: (current_provider) -> next_provider string.
    on_handoff : callable | None
        Hook: (context, partial_state) -> bool (True = accepted).
    on_human_alert : callable | None
        Hook: (alert_dict) -> None. Default: logs at CRITICAL level.
    """

    def __init__(
        self,
        fn: Callable[[dict, dict], Any],
        context: dict,
        partial_state: Optional[dict] = None,
        state_dir: Optional[Path | str] = None,
        agent_id: Optional[str] = None,
        wait_seconds: Optional[list[float]] = None,
        on_model_downgrade: Optional[Callable[[str], str]] = None,
        on_provider_switch: Optional[Callable[[str], str]] = None,
        on_handoff: Optional[Callable[[dict, dict], bool]] = None,
        on_human_alert: Optional[Callable[[dict], None]] = None,
    ):
        self.fn = fn
        self.context = context
        self.partial_state: dict = partial_state if partial_state is not None else {}
        self.state_dir = Path(state_dir or DEFAULT_STATE_DIR)
        self.agent_id = agent_id or os.environ.get("TOKENPAK_AGENT", "cali")
        self.wait_seconds = wait_seconds or [2.0, 4.0, 8.0]
        self.on_model_downgrade = on_model_downgrade or self._default_model_downgrade
        self.on_provider_switch = on_provider_switch or self._default_provider_switch
        self.on_handoff = on_handoff  # None = no handoff available
        self.on_human_alert = on_human_alert or self._default_human_alert
        self.attempts: list[RetryAttempt] = []
        self._current_model: str = context.get("model", MODEL_DOWNGRADE_PATH[0])
        self._current_provider: str = context.get("provider", PROVIDER_FALLBACK_PATH[0])
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ── default hooks ─────────────────────────────────────────────────────────

    def _default_model_downgrade(self, current_model: str) -> str:
        try:
            idx = MODEL_DOWNGRADE_PATH.index(current_model)
            if idx + 1 < len(MODEL_DOWNGRADE_PATH):
                return MODEL_DOWNGRADE_PATH[idx + 1]
        except ValueError:
            pass
        return MODEL_DOWNGRADE_PATH[-1]

    def _default_provider_switch(self, current_provider: str) -> str:
        try:
            idx = PROVIDER_FALLBACK_PATH.index(current_provider)
            if idx + 1 < len(PROVIDER_FALLBACK_PATH):
                return PROVIDER_FALLBACK_PATH[idx + 1]
        except ValueError:
            pass
        return PROVIDER_FALLBACK_PATH[-1]

    def _default_human_alert(self, alert: dict) -> None:
        logger.critical(
            "TOKENPAK HUMAN ALERT: %s",
            json.dumps(alert, indent=2, default=str),
        )

    # ── state persistence ─────────────────────────────────────────────────────

    def _state_file(self) -> Path:
        task_id = self.context.get("task_id", "unknown")
        return self.state_dir / f"{task_id}.json"

    def _save_state(self) -> Path:
        payload = {
            "context": self.context,
            "partial_state": self.partial_state,
            "attempts": [a.to_dict() for a in self.attempts],
            "saved_at": time.time(),
            "agent_id": self.agent_id,
        }
        path = self._state_file()
        path.write_text(json.dumps(payload, indent=2, default=str))
        logger.info("Partial state saved to %s", path)
        return path

    @classmethod
    def load_state(cls, state_file: Path) -> dict:
        """Reload persisted state for inspection or resume."""
        return json.loads(Path(state_file).read_text())

    # ── escalation levels ─────────────────────────────────────────────────────

    def _level0_wait_retry(self) -> Any:
        """Level 0: exponential backoff, up to len(wait_seconds) attempts."""
        for i, wait in enumerate(self.wait_seconds):
            try:
                return self.fn(self.context, self.partial_state)
            except Exception as exc:
                self.attempts.append(RetryAttempt(
                    level=0,
                    description=f"wait-retry attempt {i+1}/{len(self.wait_seconds)}, waited {wait}s",
                    error=str(exc),
                ))
                logger.warning("Level 0 attempt %d failed: %s — waiting %.1fs", i + 1, exc, wait)
                time.sleep(wait)
        # Final attempt after last wait
        return self.fn(self.context, self.partial_state)

    def _level1_downgrade_model(self) -> Any:
        """Level 1: downgrade to cheaper/more-available model."""
        next_model = self.on_model_downgrade(self._current_model)
        if next_model == self._current_model:
            raise RuntimeError(f"No model downgrade available from '{self._current_model}'")
        logger.info("Level 1: downgrading model %s → %s", self._current_model, next_model)
        self._current_model = next_model
        self.context = {**self.context, "model": next_model}
        return self.fn(self.context, self.partial_state)

    def _level2_switch_provider(self) -> Any:
        """Level 2: switch to alternate provider."""
        next_provider = self.on_provider_switch(self._current_provider)
        if next_provider == self._current_provider:
            raise RuntimeError(f"No provider fallback available from '{self._current_provider}'")
        logger.info("Level 2: switching provider %s → %s", self._current_provider, next_provider)
        self._current_provider = next_provider
        self.context = {**self.context, "provider": next_provider}
        return self.fn(self.context, self.partial_state)

    def _level3_handoff(self) -> Any:
        """Level 3: hand off to another agent, preserving state."""
        if self.on_handoff is None:
            raise RuntimeError("No handoff handler configured")
        logger.info("Level 3: attempting agent handoff")
        state_path = self._save_state()
        accepted = self.on_handoff(self.context, {**self.partial_state, "_state_file": str(state_path)})
        if not accepted:
            raise RuntimeError("Handoff rejected by all available agents")
        # Handoff accepted — return sentinel; caller decides how to proceed
        return {"_handoff": True, "state_file": str(state_path)}

    def _level4_human_alert(self, last_error: Exception) -> None:
        """Level 4: save state + alert human. Always raises RetryExhaustedError after."""
        state_path = self._save_state()
        alert = {
            "severity": "critical",
            "agent": self.agent_id,
            "task_id": self.context.get("task_id", "unknown"),
            "task_name": self.context.get("task", "unknown"),
            "error": str(last_error),
            "attempts": [a.to_dict() for a in self.attempts],
            "partial_state_file": str(state_path),
            "context": self.context,
            "timestamp": time.time(),
            "message": (
                f"Task '{self.context.get('task', 'unknown')}' failed at all retry levels. "
                f"Partial state saved to {state_path}. Manual intervention required."
            ),
        }
        self.on_human_alert(alert)

    # ── main entry point ──────────────────────────────────────────────────────

    def run(self) -> Any:
        """
        Execute the task with full escalation.

        Returns the task result on success.
        Raises RetryExhaustedError after Level 4.
        """
        escalation_levels = [
            (0, "wait + retry", self._level0_wait_retry),
            (1, "model downgrade", self._level1_downgrade_model),
            (2, "provider switch", self._level2_switch_provider),
            (3, "agent handoff", self._level3_handoff),
        ]

        last_exc: Exception = RuntimeError("Unknown error")

        for level, description, handler in escalation_levels:
            try:
                result = handler()
                logger.info("Task succeeded at level %d (%s)", level, description)
                return result
            except Exception as exc:
                last_exc = exc
                self.attempts.append(RetryAttempt(
                    level=level,
                    description=description,
                    error=str(exc),
                ))
                logger.warning("Level %d (%s) failed: %s", level, description, exc)

        # Level 4: save + alert
        self._level4_human_alert(last_exc)
        raise RetryExhaustedError(
            context=self.context,
            partial_state=self.partial_state,
            attempts=self.attempts,
        )
