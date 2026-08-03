"""TokenPak handoff manager for CrewAI agents."""

from typing import Any, Dict, List, Optional

from tokenpak.agent.agentic.handoff import (
    HandoffBlock,
    HandoffManager,
    HandoffWire,
    TokenPak,
    ContextRef,
    HandoffStatus,
)


class TokenPakHandoff:
    """Create and consume TokenPak wire-format handoffs between agents."""

    def __init__(self, budget: int = 2000, keep_recent: int = 10, manager: Optional[HandoffManager] = None):
        self.budget = budget
        self.keep_recent = keep_recent
        self._manager = manager or HandoffManager()

    def prepare_handoff(
        self,
        state: Dict[str, Any],
        from_agent: str,
        to_agent: str,
        what_was_done: str = "",
        whats_next: str = "",
        extra_blocks: Optional[List[HandoffBlock]] = None,
    ) -> str:
        """Compress state and produce a wire string."""
        pack = TokenPak()
        pack.add(
            HandoffBlock(
                type="task_state",
                id="state",
                content=self._compress_state(state),
                metadata={"budget": self.budget},
            )
        )

        for block in (extra_blocks or []):
            pack.add(block)

        summary = ""
        if what_was_done or whats_next:
            summary = f"Done: {what_was_done} | Next: {whats_next}"

        wire_obj = HandoffWire(
            pack=pack,
            from_agent=from_agent,
            to_agent=to_agent,
            summary=summary,
            metadata={"what_was_done": what_was_done, "whats_next": whats_next},
        )

        try:
            self._manager.create_handoff(
                from_agent=from_agent,
                to_agent=to_agent,
                what_was_done=what_was_done,
                whats_next=whats_next,
                metadata={"wire_id": wire_obj.id},
            )
        except Exception:
            pass

        return wire_obj.to_wire()

    def receive_handoff_wire(self, wire: str) -> Dict[str, Any]:
        """Deserialize wire string and return unpacked data."""
        handoff = HandoffWire.from_wire(wire)
        return {
            "pack": handoff.pack,
            "prompt": handoff.pack.to_prompt(),
            "metadata": handoff.metadata,
            "from_agent": handoff.from_agent,
            "to_agent": handoff.to_agent,
            "summary": handoff.summary,
        }

    def prepare_handoff_dict(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Backward-compatible dict handoff format."""
        return self._compress_state_dict(state)

    def receive_handoff(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Backward-compatible dict receive operation."""
        return state

    def _compress_state(self, state: Dict[str, Any]) -> str:
        """Convert state dict into stable text representation."""
        import json

        lines = []
        for key, value in state.items():
            text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
            lines.append(f"{key}: {text}")
        return "\n".join(lines)

    def _compress_state_dict(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Trim dict to keep only recent keys when above keep_recent."""
        if isinstance(state, dict) and len(state) > self.keep_recent:
            keys = list(state.keys())[-self.keep_recent :]
            return {key: state[key] for key in keys}
        return state
