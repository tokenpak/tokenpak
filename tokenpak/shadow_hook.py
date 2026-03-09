# SPDX-License-Identifier: MIT
"""Shadow Mode proxy hook for TokenPak.

Lightweight integration point for the forward proxy. The proxy calls
`record_request()` before forwarding and `record_response()` after
receiving the LLM reply. Thread-safe, fail-silent (never breaks proxy).

Usage in proxy.py:
    from tokenpak.shadow_hook import ShadowHook
    _shadow = ShadowHook()

    # Before forwarding:
    txn_id = _shadow.record_request(model, query, context_tokens)

    # After response received:
    _shadow.record_response(txn_id, response_text, response_tokens, latency_ms)
"""

import threading
import time
from typing import Optional

from .routing_ledger import DEFAULT_LEDGER_PATH, RoutingLedger


class ShadowHook:
    """
    Thin wrapper around RoutingLedger for proxy use.
    Designed to be fail-silent — any error is caught and logged to stderr only.
    """

    def __init__(self, ledger_path: str = DEFAULT_LEDGER_PATH, enabled: bool = True):
        self.enabled = enabled
        self._ledger: Optional[RoutingLedger] = None
        self._pending: dict = {}  # txn_id → {model, query, context_tokens, start_time}
        self._lock = threading.Lock()
        if enabled:
            try:
                self._ledger = RoutingLedger(ledger_path)
            except Exception as e:
                import sys

                print(f"[shadow-mode] init failed: {e}", file=sys.stderr)
                self.enabled = False

    def record_request(
        self,
        model: str,
        query: str,
        context_tokens: int = 0,
    ) -> Optional[int]:
        """
        Called when a request is about to be forwarded to the LLM.

        Returns a pending transaction ID used to correlate with the response.
        Returns None if shadow mode is disabled or on error.
        """
        if not self.enabled or self._ledger is None:
            return None
        try:
            txn_key = id(threading.current_thread())
            with self._lock:
                self._pending[txn_key] = {
                    "model": model,
                    "query": query,
                    "context_tokens": context_tokens,
                    "start_time": time.perf_counter(),
                }
            return txn_key
        except Exception as e:
            import sys

            print(f"[shadow-mode] record_request error: {e}", file=sys.stderr)
            return None

    def record_response(
        self,
        txn_key: Optional[int],
        response_text: str,
        response_tokens: int = 0,
        latency_ms: float = 0.0,
        context_blocks: Optional[list] = None,
    ) -> Optional[int]:
        """
        Called after the LLM response is received.

        Args:
            txn_key:        Key returned by record_request (or None).
            response_text:  Full LLM response.
            response_tokens: Token count of response.
            latency_ms:     Measured latency in ms (overrides internal timing if > 0).
            context_blocks: Block content strings (for complexity scoring).

        Returns:
            Row ID of committed transaction, or None on failure.
        """
        if not self.enabled or self._ledger is None or txn_key is None:
            return None
        try:
            with self._lock:
                pending = self._pending.pop(txn_key, None)
            if pending is None:
                return None

            elapsed = (time.perf_counter() - pending["start_time"]) * 1000
            actual_latency = latency_ms if latency_ms > 0 else elapsed

            return self._ledger.log_transaction(
                model=pending["model"],
                query=pending["query"],
                context_blocks=context_blocks or [],
                response=response_text,
                accepted=None,  # unreviewed until feedback arrives
                latency_ms=actual_latency,
                context_tokens=pending["context_tokens"],
                response_tokens=response_tokens,
            )
        except Exception as e:
            import sys

            print(f"[shadow-mode] record_response error: {e}", file=sys.stderr)
            return None

    def record_feedback(
        self,
        transaction_id: int,
        accepted: bool,
        reason: Optional[str] = None,
    ) -> bool:
        """
        Record user feedback (retry = rejected, continued = accepted).
        Also triggers Elo update.
        """
        if not self.enabled or self._ledger is None:
            return False
        try:
            txn = self._ledger.get_transaction(transaction_id)
            if txn:
                # Update Elo rating
                from .elo import update_elo

                update_elo(txn["model_used"], txn["task_type"], accepted)
            return self._ledger.record_outcome(transaction_id, accepted, reason)
        except Exception as e:
            import sys

            print(f"[shadow-mode] record_feedback error: {e}", file=sys.stderr)
            return False

    def get_stats(self) -> dict:
        """Return ledger stats, or empty dict on failure."""
        if not self.enabled or self._ledger is None:
            return {}
        try:
            return self._ledger.get_stats()
        except Exception:
            return {}
