"""
tokenpak.agent.agentic.retry
─────────────────────────────
Automatic retry with 5-level escalation:

  Level 0: wait + retry (exponential backoff)
  Level 1: downgrade model (e.g. opus → sonnet → haiku)
  Level 2: switch provider (anthropic → openai → google)
  Level 3: handoff to another agent (saves state)
  Level 4: save partial state + alert human

Never silently fails: every failure path is logged; the final level
surfaces a human-readable alert with full context.

Per-error behavior
------------------
The engine inspects HTTP status codes from exception messages/attributes:
  - 429 (rate limit)  → always wait with backoff before retrying
  - 500/502/503/504   → retry immediately (server error)
  - 401/403 (auth)    → alert immediately, skip escalation

Configuration (~/.tokenpak/config.json)
---------------------------------------
    {
      "retry": {
        "max_retries": 3,
        "backoff_factor": 2.0,
        "wait_seconds": [1, 2, 4],
        "downgrade_chain": ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"],
        "provider_chain": ["anthropic", "openai", "google"],
        "per_error": {
          "429": "wait",
          "500": "retry",
          "401": "alert"
        }
      }
    }

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
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_STATE_DIR = Path.home() / ".tokenpak" / "retry_state"
RETRY_EVENT_LOG = Path.home() / ".tokenpak" / "retry_events.jsonl"
CONFIG_PATH = Path.home() / ".tokenpak" / "config.json"

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

# Per-error-type default behavior
# "wait"  → force Level 0 exponential backoff
# "retry" → immediate retry (no wait)
# "alert" → skip escalation, go directly to Level 4 alert
DEFAULT_PER_ERROR: dict[str, str] = {
    "429": "wait",
    "500": "retry",
    "502": "retry",
    "503": "retry",
    "504": "retry",
    "401": "alert",
    "403": "alert",
}


def _load_retry_config() -> dict:
    """Load the retry section from ~/.tokenpak/config.json (fail-safe)."""
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text())
            return data.get("retry", {})
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _extract_http_status(exc: Exception) -> Optional[str]:
    """Try to extract an HTTP status code from an exception."""
    # Check attribute (e.g. requests.HTTPError, httpx.HTTPStatusError)
    for attr in ("status_code", "code", "response_code"):
        val = getattr(exc, attr, None)
        if val is not None:
            return str(int(val))
    # Scan message for 3-digit HTTP codes
    msg = str(exc)
    match = re.search(r"\b(4\d{2}|5\d{2})\b", msg)
    if match:
        return match.group(1)
    return None


def _append_retry_event(event: dict) -> None:
    """Append a retry event to the JSONL log (fail-silent)."""
    try:
        RETRY_EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with RETRY_EVENT_LOG.open("a") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
    except OSError:
        pass


def load_recent_retry_events(n: int = 20) -> list[dict]:
    """Return up to *n* most-recent retry events from the JSONL log."""
    if not RETRY_EVENT_LOG.exists():
        return []
    try:
        lines = RETRY_EVENT_LOG.read_text().strip().splitlines()
        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return events[-n:]
    except OSError:
        return []


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


class ImmediateAlertError(Exception):
    """Raised by per-error routing when an auth/fatal error demands immediate alert."""

    def __init__(self, status_code: str, original: Exception):
        self.status_code = status_code
        self.original = original
        super().__init__(f"Immediate alert triggered by HTTP {status_code}: {original}")


@dataclass
class RetryAttempt:
    level: int
    description: str
    error: str
    http_status: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = {
            "level": self.level,
            "description": self.description,
            "error": self.error,
            "timestamp": self.timestamp,
        }
        if self.http_status:
            d["http_status"] = self.http_status
        return d


class RetryEngine:
    """
    5-level retry engine with escalation, per-error routing, and partial-state preservation.

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
        Wait times between Level-0 retries. Defaults to config or [1, 2, 4].
    per_error : dict[str, str] | None
        Map of HTTP status code str → behavior ("wait", "retry", "alert").
        Merged over DEFAULT_PER_ERROR; config file takes next priority.
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
        per_error: Optional[dict[str, str]] = None,
        on_model_downgrade: Optional[Callable[[str], str]] = None,
        on_provider_switch: Optional[Callable[[str], str]] = None,
        on_handoff: Optional[Callable[[dict, dict], bool]] = None,
        on_human_alert: Optional[Callable[[dict], None]] = None,
    ):
        # Load persistent config
        cfg = _load_retry_config()

        self.fn = fn
        self.context = context
        self.partial_state: dict = partial_state if partial_state is not None else {}
        self.state_dir = Path(state_dir or DEFAULT_STATE_DIR)
        self.agent_id = agent_id or os.environ.get("TOKENPAK_AGENT", "cali")
        self.wait_seconds = wait_seconds or cfg.get("wait_seconds", [1.0, 2.0, 4.0])

        # Per-error routing: defaults ← config file ← caller override
        self._per_error: dict[str, str] = {**DEFAULT_PER_ERROR}
        self._per_error.update(cfg.get("per_error", {}))
        if per_error:
            self._per_error.update(per_error)

        # Model/provider chains from config (override constants)
        self._model_chain: list[str] = cfg.get("downgrade_chain", MODEL_DOWNGRADE_PATH)
        self._provider_chain: list[str] = cfg.get("provider_chain", PROVIDER_FALLBACK_PATH)

        self.on_model_downgrade = on_model_downgrade or self._default_model_downgrade
        self.on_provider_switch = on_provider_switch or self._default_provider_switch
        self.on_handoff = on_handoff
        self.on_human_alert = on_human_alert or self._default_human_alert
        self.attempts: list[RetryAttempt] = []
        self._current_model: str = context.get("model", self._model_chain[0])
        self._current_provider: str = context.get("provider", self._provider_chain[0])
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ── per-error routing ─────────────────────────────────────────────────────

    def _error_behavior(self, exc: Exception) -> Optional[str]:
        """Return the configured behavior for *exc*'s HTTP status, or None."""
        status = _extract_http_status(exc)
        if status is None:
            return None
        behavior = self._per_error.get(status)
        if behavior:
            logger.debug("Per-error routing: HTTP %s → %s", status, behavior)
        return behavior

    def _maybe_immediate_alert(self, exc: Exception) -> None:
        """If exception maps to 'alert' behavior, raise ImmediateAlertError."""
        status = _extract_http_status(exc)
        if status and self._per_error.get(status) == "alert":
            _append_retry_event(
                {
                    "event": "immediate_alert",
                    "http_status": status,
                    "error": str(exc),
                    "task_id": self.context.get("task_id"),
                    "timestamp": time.time(),
                }
            )
            raise ImmediateAlertError(status, exc)

    # ── default hooks ─────────────────────────────────────────────────────────

    def _default_model_downgrade(self, current_model: str) -> str:
        try:
            idx = self._model_chain.index(current_model)
            if idx + 1 < len(self._model_chain):
                return self._model_chain[idx + 1]
        except ValueError:
            pass
        return self._model_chain[-1]

    def _default_provider_switch(self, current_provider: str) -> str:
        try:
            idx = self._provider_chain.index(current_provider)
            if idx + 1 < len(self._provider_chain):
                return self._provider_chain[idx + 1]
        except ValueError:
            pass
        return self._provider_chain[-1]

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

    # ── shadow/event logging ──────────────────────────────────────────────────

    def _log_event(self, event_type: str, **extra: Any) -> None:
        """Append a structured retry event to the JSONL shadow log."""
        event = {
            "event": event_type,
            "task_id": self.context.get("task_id"),
            "task": self.context.get("task"),
            "agent": self.agent_id,
            "timestamp": time.time(),
            **extra,
        }
        _append_retry_event(event)
        logger.debug("retry_event: %s", json.dumps(event, default=str))

    # ── escalation levels ─────────────────────────────────────────────────────

    def _level0_wait_retry(self) -> Any:
        """Level 0: exponential backoff, up to len(wait_seconds) attempts."""
        for i, wait in enumerate(self.wait_seconds):
            try:
                result = self.fn(self.context, self.partial_state)
                self._log_event("level0_success", attempt=i)
                return result
            except Exception as exc:
                self._maybe_immediate_alert(exc)
                behavior = self._error_behavior(exc)
                actual_wait = wait if behavior in ("wait", None) else 0
                self.attempts.append(
                    RetryAttempt(
                        level=0,
                        description=f"wait-retry attempt {i + 1}/{len(self.wait_seconds)}, waited {actual_wait}s",
                        error=str(exc),
                        http_status=_extract_http_status(exc),
                    )
                )
                self._log_event(
                    "level0_retry",
                    attempt=i + 1,
                    wait=actual_wait,
                    error=str(exc),
                    http_status=_extract_http_status(exc),
                )
                logger.warning(
                    "Level 0 attempt %d failed: %s — waiting %.1fs", i + 1, exc, actual_wait
                )
                if actual_wait > 0:
                    time.sleep(actual_wait)
        # Final attempt after last wait
        return self.fn(self.context, self.partial_state)

    def _level1_downgrade_model(self) -> Any:
        """Level 1: downgrade to cheaper/more-available model."""
        next_model = self.on_model_downgrade(self._current_model)
        if next_model == self._current_model:
            raise RuntimeError(f"No model downgrade available from '{self._current_model}'")
        logger.info("Level 1: downgrading model %s → %s", self._current_model, next_model)
        self._log_event(
            "level1_model_downgrade", from_model=self._current_model, to_model=next_model
        )
        self._current_model = next_model
        self.context = {**self.context, "model": next_model}
        return self.fn(self.context, self.partial_state)

    def _level2_switch_provider(self) -> Any:
        """Level 2: switch to alternate provider."""
        next_provider = self.on_provider_switch(self._current_provider)
        if next_provider == self._current_provider:
            raise RuntimeError(f"No provider fallback available from '{self._current_provider}'")
        logger.info("Level 2: switching provider %s → %s", self._current_provider, next_provider)
        self._log_event(
            "level2_provider_switch",
            from_provider=self._current_provider,
            to_provider=next_provider,
        )
        self._current_provider = next_provider
        self.context = {**self.context, "provider": next_provider}
        return self.fn(self.context, self.partial_state)

    def _level3_handoff(self) -> Any:
        """Level 3: hand off to another agent, preserving state."""
        if self.on_handoff is None:
            raise RuntimeError("No handoff handler configured")
        logger.info("Level 3: attempting agent handoff")
        state_path = self._save_state()
        self._log_event("level3_handoff_attempt", state_file=str(state_path))
        accepted = self.on_handoff(
            self.context, {**self.partial_state, "_state_file": str(state_path)}
        )
        if not accepted:
            self._log_event("level3_handoff_rejected")
            raise RuntimeError("Handoff rejected by all available agents")
        self._log_event("level3_handoff_accepted", state_file=str(state_path))
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
        self._log_event("level4_human_alert", error=str(last_error), state_file=str(state_path))
        self.on_human_alert(alert)

    # ── main entry point ──────────────────────────────────────────────────────

    def run(self) -> Any:
        """
        Execute the task with full escalation.

        Returns the task result on success.
        Raises RetryExhaustedError after Level 4.
        """
        self._log_event("run_start")

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
                self._log_event("run_success", level=level, description=description)
                return result
            except ImmediateAlertError as exc:
                # Auth/fatal errors: skip escalation chain, go directly to Level 4
                logger.error(
                    "Immediate alert triggered (HTTP %s): %s",
                    exc.status_code,
                    exc.original,
                )
                last_exc = exc.original
                self.attempts.append(
                    RetryAttempt(
                        level=level,
                        description=f"immediate alert (HTTP {exc.status_code})",
                        error=str(exc.original),
                        http_status=exc.status_code,
                    )
                )
                break
            except Exception as exc:
                last_exc = exc
                self.attempts.append(
                    RetryAttempt(
                        level=level,
                        description=description,
                        error=str(exc),
                        http_status=_extract_http_status(exc),
                    )
                )
                logger.warning("Level %d (%s) failed: %s", level, description, exc)

        # Level 4: save + alert
        self._level4_human_alert(last_exc)
        raise RetryExhaustedError(
            context=self.context,
            partial_state=self.partial_state,
            attempts=self.attempts,  # type: ignore
        )
