"""TokenPakAssistant for AutoGen-style messaging and handoffs."""

from typing import Any, Dict, List, Optional

from tokenpak.agent.agentic.handoff import HandoffBlock, HandoffManager, HandoffWire, TokenPak

from .message import TokenPakMessage


class TokenPakAssistant:
    """AutoGen-compatible assistant with compression and handoff helpers."""

    def __init__(self, name: str, budget: int = 4000, manager: Optional[HandoffManager] = None, **kwargs):
        self.name = name
        self.budget = budget
        self.kwargs = kwargs
        self._messages: List[Dict[str, Any]] = []
        self._manager = manager or HandoffManager()

    def receive_message(self, message: str, sender: Any = None, sender_name: str = "") -> None:
        """Receive message from another participant."""
        role = sender_name or (sender.name if hasattr(sender, "name") else "agent")
        self._messages.append({"role": role, "content": str(message)})

    def get_messages(self, compress: bool = True) -> List[Dict[str, Any]]:
        """Return current messages."""
        if compress:
            return self._compress_messages()
        return list(self._messages)

    def generate_reply(self, prompt: str = "") -> str:
        """Produce a deterministic placeholder reply string."""
        _ = prompt
        messages = self._compress_messages()
        return f"[{self.name} reply based on {len(messages)} messages]"

    def prepare_handoff(
        self,
        to_agent: str,
        what_was_done: str = "",
        whats_next: str = "",
        extra_blocks: Optional[List[HandoffBlock]] = None,
    ) -> str:
        """Create a handoff wire for downstream assistant."""
        pack = TokenPak()
        compressed = self._compress_messages()
        if compressed:
            conversation = "\n".join(f"{m['role']}: {m['content']}" for m in compressed)
            pack.add(
                HandoffBlock(
                    type="conversation",
                    id="history",
                    content=conversation,
                    metadata={"agent": self.name, "budget": self.budget},
                )
            )

        for block in (extra_blocks or []):
            pack.add(block)

        summary = ""
        if what_was_done or whats_next:
            summary = f"Done: {what_was_done} | Next: {whats_next}"

        wire_obj = HandoffWire(
            pack=pack,
            from_agent=self.name,
            to_agent=to_agent,
            summary=summary,
            metadata={"what_was_done": what_was_done, "whats_next": whats_next},
        )

        try:
            self._manager.create_handoff(
                from_agent=self.name,
                to_agent=to_agent,
                what_was_done=what_was_done,
                whats_next=whats_next,
                metadata={"wire_id": wire_obj.id},
            )
        except Exception:
            pass

        return wire_obj.to_wire()

    def apply_handoff_wire(self, wire: str) -> TokenPak:
        """Apply handoff wire as first message and return its pack."""
        handoff = HandoffWire.from_wire(wire)
        prompt = handoff.pack.to_prompt()
        if prompt:
            self._messages.insert(0, {"role": handoff.from_agent, "content": prompt})
        return handoff.pack

    def _compress_messages(self) -> List[Dict[str, Any]]:
        """Return tail of conversation that fits budget; compress each message."""
        compressed: List[Dict[str, Any]] = []
        token_estimate = 0
        for message in reversed(self._messages):
            est = len(message.get("content", "")) // 4
            if token_estimate + est > self.budget:
                break
            compressed.insert(0, TokenPakMessage.compress_message(message, max_tokens=200))
            token_estimate += est
        return compressed
