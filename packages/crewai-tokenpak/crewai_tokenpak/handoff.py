"""
TokenPakHandoff — Manages context handoff between CrewAI agents using HandoffManager.

Compresses messages and state when passing between agents to stay
within token budgets, and uses tokenpak HandoffManager for persistent tracking.
"""

from typing import Any, Dict, List, Optional

from tokenpak.agent.agentic.handoff import (
    ContextRef,
    HandoffBlock,
    HandoffManager,
    HandoffStatus,
    HandoffWire,
    TokenPak,
)


class TokenPakHandoff:
    """
    Manages token-efficient handoffs between CrewAI agents.

    Compresses intermediate state before passing to next agent,
    and uses HandoffManager for persistent tracking.

    Example::

        handoff = TokenPakHandoff(budget=2000)

        # Agent A prepares
        wire = handoff.prepare_handoff(
            state={"key": "value"},
            from_agent="cali",
            to_agent="sue",
            what_was_done="Researched topic X",
            whats_next="Write report",
        )

        # Agent B receives
        context = handoff.receive_handoff_wire(wire)

    """

    def __init__(
        self,
        budget: int = 2000,
        keep_recent: int = 10,
        manager: Optional[HandoffManager] = None,
    ):
        self.budget = budget
        self.keep_recent = keep_recent
        self._manager = manager or HandoffManager()

    # ------------------------------------------------------------------
    # High-level API (wire format — stateless pass-through)
    # ------------------------------------------------------------------

    def prepare_handoff(
        self,
        state: Dict[str, Any],
        from_agent: str,
        to_agent: str,
        what_was_done: str = "",
        whats_next: str = "",
        extra_blocks: Optional[List[HandoffBlock]] = None,
    ) -> str:
        """Compress state and produce a wire-format string for Agent B.

        Also records the handoff in HandoffManager for auditing.

        Returns:
            JSON wire string for passing to :meth:`receive_handoff_wire`.
        """
        pack = TokenPak()
        pack.add(HandoffBlock(
            type="task_state",
            id="state",
            content=self._compress_state(state),
            metadata={"budget": self.budget},
        ))
        for blk in (extra_blocks or []):
            pack.add(blk)

        wire_obj = HandoffWire(
            pack=pack,
            from_agent=from_agent,
            to_agent=to_agent,
            summary=f"Done: {what_was_done} | Next: {whats_next}" if (what_was_done or whats_next) else "",
            metadata={"what_was_done": what_was_done, "whats_next": whats_next},
        )

        # Persist record via HandoffManager (best-effort)
        try:
            self._manager.create_handoff(
                from_agent=from_agent,
                to_agent=to_agent,
                what_was_done=what_was_done,
                whats_next=whats_next,
                metadata={"wire_id": wire_obj.id},
            )
        except (ValueError, Exception):
            pass  # unknown agents OK in non-strict mode

        return wire_obj.to_wire()

    def receive_handoff_wire(self, wire: str) -> Dict[str, Any]:
        """Deserialise a wire string and return the state dict.

        Returns:
            Dict with 'pack', 'prompt', and 'metadata' keys.
        """
        h = HandoffWire.from_wire(wire)
        return {
            "pack": h.pack,
            "prompt": h.pack.to_prompt(),
            "metadata": h.metadata,
            "from_agent": h.from_agent,
            "to_agent": h.to_agent,
            "summary": h.summary,
        }

    # ------------------------------------------------------------------
    # Legacy dict-based API (backward compatible)
    # ------------------------------------------------------------------

    def prepare_handoff_dict(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Compress state for handoff (dict version, backward compatible)."""
        return self._compress_state_dict(state)

    def receive_handoff(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Decompress state received from previous agent (dict version)."""
        return state

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compress_state(self, state: Dict[str, Any]) -> str:
        """Convert state dict to compressed string representation."""
        import json
        lines = []
        for key, value in state.items():
            v_str = json.dumps(value) if not isinstance(value, str) else value
            lines.append(f"{key}: {v_str}")
        return "\n".join(lines)

    def _compress_state_dict(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Return state dict, trimmed to keep_recent entries if too large."""
        if isinstance(state, dict) and len(state) > self.keep_recent:
            keys = list(state.keys())[-self.keep_recent:]
            return {k: state[k] for k in keys}
        return state
