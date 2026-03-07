"""
TokenPakAssistant — AutoGen ConversableAgent with HandoffManager integration.

Automatically compresses messages within a token budget and supports
structured context handoff via the TokenPak wire format.
"""

from typing import Any, Dict, List, Optional

from tokenpak.agent.agentic.handoff import (
    HandoffBlock,
    HandoffManager,
    HandoffWire,
    TokenPak,
)

from .message import TokenPakMessage


class TokenPakAssistant:
    """
    AutoGen-compatible agent with TokenPak compression and handoff support.

    Compresses long conversations to stay within budget, and can produce/consume
    HandoffWire packages for structured agent-to-agent context passing.

    Example::

        alice = TokenPakAssistant(name="alice", budget=4000)
        bob   = TokenPakAssistant(name="bob",   budget=4000)

        alice.receive_message("Do research on X", sender_name="user")
        wire = alice.prepare_handoff(to_agent="bob", what_was_done="Researched X")
        bob.apply_handoff_wire(wire)

    """

    def __init__(
        self,
        name: str,
        budget: int = 4000,
        manager: Optional[HandoffManager] = None,
        **kwargs,
    ):
        self.name = name
        self.budget = budget
        self.kwargs = kwargs
        self._messages: List[Dict[str, Any]] = []
        self._manager = manager or HandoffManager()

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    def receive_message(self, message: str, sender: Any = None, sender_name: str = "") -> None:
        """Receive a message from another agent."""
        name = sender_name or (sender.name if hasattr(sender, "name") else "agent")
        self._messages.append({"role": name, "content": message})

    def get_messages(self, compress: bool = True) -> List[Dict[str, Any]]:
        """Return current messages, optionally compressed to budget."""
        if compress:
            return self._compress_messages()
        return list(self._messages)

    def generate_reply(self, prompt: str = "") -> str:
        """Stub reply generator; production integrations override this."""
        msgs = self._compress_messages()
        context = "\n".join(f"{m['role']}: {m['content']}" for m in msgs)
        return f"[{self.name} reply based on {len(msgs)} messages]"

    # ------------------------------------------------------------------
    # Handoff (wire format)
    # ------------------------------------------------------------------

    def prepare_handoff(
        self,
        to_agent: str,
        what_was_done: str = "",
        whats_next: str = "",
        extra_blocks: Optional[List[HandoffBlock]] = None,
    ) -> str:
        """Produce a HandoffWire string to pass context to another agent.

        Compresses the current conversation into the pack and records the
        handoff via HandoffManager (best-effort).

        Returns:
            JSON wire string.
        """
        pack = TokenPak()
        # Compress conversation into a block
        msgs = self._compress_messages()
        if msgs:
            conv = "\n".join(f"{m['role']}: {m['content']}" for m in msgs)
            pack.add(HandoffBlock(
                type="conversation",
                id="history",
                content=conv,
                metadata={"agent": self.name, "budget": self.budget},
            ))
        for blk in (extra_blocks or []):
            pack.add(blk)

        wire_obj = HandoffWire(
            pack=pack,
            from_agent=self.name,
            to_agent=to_agent,
            summary=f"Done: {what_was_done} | Next: {whats_next}" if (what_was_done or whats_next) else "",
            metadata={"what_was_done": what_was_done, "whats_next": whats_next},
        )

        # Persist via HandoffManager (best-effort; skip unknown agents)
        try:
            self._manager.create_handoff(
                from_agent=self.name,
                to_agent=to_agent,
                what_was_done=what_was_done,
                whats_next=whats_next,
                metadata={"wire_id": wire_obj.id},
            )
        except (ValueError, Exception):
            pass

        return wire_obj.to_wire()

    def apply_handoff_wire(self, wire: str) -> TokenPak:
        """Apply a HandoffWire string — load context into current working set.

        Returns the TokenPak so callers can inspect individual blocks.
        """
        h = HandoffWire.from_wire(wire)
        # Inject handoff context as an initial message
        prompt = h.pack.to_prompt()
        if prompt:
            self._messages.insert(0, {"role": h.from_agent, "content": prompt})
        return h.pack

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compress_messages(self) -> List[Dict[str, Any]]:
        """Compress conversation to fit within token budget."""
        compressed = []
        token_estimate = 0
        for msg in reversed(self._messages):
            est = len(msg.get("content", "")) // 4
            if token_estimate + est > self.budget:
                break
            compressed.insert(0, TokenPakMessage.compress_message(msg, max_tokens=200))
            token_estimate += est
        return compressed
