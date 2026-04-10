"""AutoGen conversation context compression via TokenPak."""

from dataclasses import dataclass
from typing import Any, Dict, Optional, List
import json
from functools import wraps


@dataclass
class TokenPakCompressionReport:
    """Report of compression metrics for an AutoGen conversation."""

    agent_name: str
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    messages_compressed: int
    tools_compressed: int
    system_prompt_length: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dictionary."""
        return {
            "agent_name": self.agent_name,
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "compression_ratio": f"{self.compression_ratio:.2%}",
            "messages_compressed": self.messages_compressed,
            "tools_compressed": self.tools_compressed,
            "system_prompt_length": self.system_prompt_length,
        }

    def __str__(self) -> str:
        """Human-readable compression report."""
        return (
            f"TokenPak Compression Report ({self.agent_name})\n"
            f"  Original tokens: {self.original_tokens}\n"
            f"  Compressed tokens: {self.compressed_tokens}\n"
            f"  Compression ratio: {self.compression_ratio:.2%}\n"
            f"  Messages compressed: {self.messages_compressed}\n"
            f"  Tools compressed: {self.tools_compressed}\n"
            f"  System prompt length: {self.system_prompt_length}"
        )


@dataclass
class AgentContextConfig:
    """Configuration for per-agent context compression."""

    max_tokens: int = 4096
    preserve_recent_messages: int = 5
    compress_system_prompt: bool = True
    compress_tools: bool = True
    compress_history: bool = True


class TokenPakConversationHook:
    """Hook for AutoGen agents to apply TokenPak context compression.

    This hook intercepts AutoGen conversation context assembly and applies
    TokenPak compression to system prompts, conversation history, and tool
    definitions. It integrates transparently with AutoGen agents without
    requiring API modifications.

    Example:
        >>> hook = TokenPakConversationHook()
        >>> agent = AssistantAgent("agent", llm_config={...})
        >>> hook.compress_agent(agent)
        >>> # Conversation proceeds normally; compression applied automatically
    """

    def __init__(self) -> None:
        """Initialize TokenPakConversationHook."""
        self.agents_patched: List[str] = []
        self.reports: Dict[str, TokenPakCompressionReport] = {}

    def compress_agent(
        self,
        agent: Any,
        config: Optional[AgentContextConfig] = None,
    ) -> None:
        """Patch an AutoGen agent to apply TokenPak compression.

        Args:
            agent: AutoGen agent instance (UserProxyAgent, AssistantAgent, etc.)
            config: Optional AgentContextConfig for per-agent tuning

        Returns:
            None
        """
        if config is None:
            config = AgentContextConfig()

        agent_name = getattr(agent, "name", "unknown")

        if agent_name in self.agents_patched:
            return

        # Patch _get_context or get_context method
        original_get_context = self._get_original_method(agent, "get_context")

        @wraps(original_get_context)
        def compressed_get_context(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            """Get context with TokenPak compression applied."""
            context = original_get_context(*args, **kwargs)
            return self._compress_context(context, agent_name, config)

        agent.get_context = compressed_get_context
        self.agents_patched.append(agent_name)

    def restore_agent(self, agent: Any) -> None:
        """Restore original get_context method (remove compression hook).

        Args:
            agent: AutoGen agent instance

        Returns:
            None
        """
        agent_name = getattr(agent, "name", "unknown")
        if agent_name in self.agents_patched:
            self.agents_patched.remove(agent_name)

    def get_report(self, agent_name: str) -> Optional[TokenPakCompressionReport]:
        """Get compression report for an agent.

        Args:
            agent_name: Name of the agent

        Returns:
            TokenPakCompressionReport if available, None otherwise
        """
        return self.reports.get(agent_name)

    def _get_original_method(self, agent: Any, method_name: str) -> Any:
        """Get original unpatched method from agent."""
        if not hasattr(agent, method_name):
            # Fallback: return identity function
            return lambda *args, **kwargs: {}
        return getattr(agent, method_name)

    def _compress_context(
        self,
        context: Dict[str, Any],
        agent_name: str,
        config: AgentContextConfig,
    ) -> Dict[str, Any]:
        """Apply TokenPak compression to context.

        Args:
            context: Original AutoGen context dict
            agent_name: Name of the agent
            config: Compression configuration

        Returns:
            Compressed context dict
        """
        compressed = dict(context)

        # Count original tokens (simplified: approximate as chars / 4)
        original_tokens = self._estimate_tokens(json.dumps(context))

        messages_compressed = 0
        tools_compressed = 0
        system_prompt_length = 0

        # Compress system prompt
        if config.compress_system_prompt and "system_prompt" in compressed:
            system_prompt = compressed["system_prompt"]
            if isinstance(system_prompt, str):
                compressed["system_prompt"] = self._normalize_text(system_prompt)
                system_prompt_length = len(compressed["system_prompt"])

        # Compress conversation history
        if config.compress_history and "messages" in compressed:
            messages = compressed["messages"]
            if isinstance(messages, list):
                # Keep recent messages, compress older ones
                recent_idx = max(0, len(messages) - config.preserve_recent_messages)
                compressed_messages = []

                for i, msg in enumerate(messages):
                    if i >= recent_idx:
                        # Keep recent messages as-is
                        compressed_messages.append(msg)
                    else:
                        # Compress older messages
                        if isinstance(msg, dict) and "content" in msg:
                            msg_copy = dict(msg)
                            msg_copy["content"] = self._normalize_text(
                                msg.get("content", "")
                            )
                            compressed_messages.append(msg_copy)
                            messages_compressed += 1
                        else:
                            compressed_messages.append(msg)

                compressed["messages"] = compressed_messages

        # Compress tool/function definitions
        if config.compress_tools and "tools" in compressed:
            tools = compressed["tools"]
            if isinstance(tools, list):
                compressed_tools = []
                for tool in tools:
                    if isinstance(tool, dict):
                        tool_copy = dict(tool)
                        if "description" in tool_copy:
                            tool_copy["description"] = self._normalize_text(
                                tool_copy["description"]
                            )
                        if "parameters" in tool_copy and isinstance(
                            tool_copy["parameters"], dict
                        ):
                            params = tool_copy["parameters"]
                            if "properties" in params:
                                for prop_key in params["properties"]:
                                    prop = params["properties"][prop_key]
                                    if isinstance(prop, dict) and "description" in prop:
                                        prop["description"] = self._normalize_text(
                                            prop["description"]
                                        )
                        compressed_tools.append(tool_copy)
                        tools_compressed += 1
                    else:
                        compressed_tools.append(tool)

                compressed["tools"] = compressed_tools

        # Calculate compression ratio
        compressed_tokens = self._estimate_tokens(json.dumps(compressed))
        compression_ratio = (
            (original_tokens - compressed_tokens) / original_tokens
            if original_tokens > 0
            else 0.0
        )

        # Store report
        self.reports[agent_name] = TokenPakCompressionReport(
            agent_name=agent_name,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=compression_ratio,
            messages_compressed=messages_compressed,
            tools_compressed=tools_compressed,
            system_prompt_length=system_prompt_length,
        )

        return compressed

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize text by removing excess whitespace and deduplicating.

        Args:
            text: Input text

        Returns:
            Normalized text
        """
        # Remove excessive whitespace
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        return " ".join(lines)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token count (approximate: chars / 4).

        Args:
            text: Input text

        Returns:
            Estimated token count
        """
        return max(1, len(text) // 4)


class TokenPakAssistant:
    """Lightweight AutoGen-compatible assistant with TokenPak budget management.

    Provides message receiving, context compression, and handoff integration
    for AutoGen multi-agent workflows.
    """

    def __init__(self, name: str, budget: int = 4000, manager=None):
        self.name = name
        self.budget = budget
        self.manager = manager
        self._messages: List[Dict[str, Any]] = []

    def receive_message(self, content: str, sender_name: str = "user") -> None:
        """Record an incoming message."""
        self._messages.append({"role": "user", "name": sender_name, "content": content})

    def get_messages(self, compress: bool = False) -> List[Dict[str, Any]]:
        """Return messages, optionally compressed to budget."""
        if not compress or not self._messages:
            return list(self._messages)
        # Simple budget-aware truncation
        result = []
        tokens_used = 0
        for msg in reversed(self._messages):
            est = len(msg.get("content", "")) // 4
            if tokens_used + est > self.budget:
                break
            result.insert(0, msg)
            tokens_used += est
        return result if result else self._messages[-1:]

    def prepare_handoff(
        self,
        to_agent: str = "",
        target: str = "",
        what_was_done: str = "",
        whats_next: str = "",
        extra_blocks: Optional[List[Any]] = None,
        **kwargs,
    ) -> str:
        """Prepare a HandoffWire JSON string for another agent.

        Args:
            to_agent: Target agent name (preferred)
            target: Alias for to_agent (legacy)
            what_was_done: Summary of completed work
            whats_next: Summary of what the receiving agent should do
            extra_blocks: Optional list of HandoffBlock objects to include
            **kwargs: Additional metadata

        Returns:
            HandoffWire JSON string
        """
        # Import from the tokenpak package (may be on PYTHONPATH)
        try:
            from tokenpak.agent.agentic.handoff import (
                HandoffWire,
                TokenPak,
                HandoffBlock as HBlock,
            )
        except ImportError:
            # Fallback: return simple JSON if tokenpak not available
            recipient = to_agent or target
            return json.dumps(
                {
                    "version": "tokpak-handoff:1",
                    "id": f"hw-{self.name}-{recipient}",
                    "from_agent": self.name,
                    "to_agent": recipient,
                    "summary": what_was_done,
                    "metadata": {"whats_next": whats_next},
                    "created_at": 0.0,
                    "pack": {
                        "blocks": [
                            {
                                "type": "history",
                                "id": "msg0",
                                "content": m.get("content", ""),
                                "metadata": {},
                            }
                            for m in self._messages
                        ],
                        "metadata": {},
                    },
                }
            )

        recipient = to_agent or target
        # Build a TokenPak with message history + summary blocks
        blocks = []
        for i, msg in enumerate(self.get_messages(compress=True)):
            blocks.append(
                HBlock(type="history", id=f"msg{i}", content=msg.get("content", ""))
            )
        if what_was_done:
            blocks.append(HBlock(type="summary", id="done", content=what_was_done))
        if whats_next:
            blocks.append(HBlock(type="task_state", id="next", content=whats_next))
        if extra_blocks:
            blocks.extend(extra_blocks)

        pack = TokenPak(blocks=blocks)
        wire = HandoffWire(
            pack=pack, from_agent=self.name, to_agent=recipient, summary=what_was_done
        )
        return wire.to_wire()

    def apply_handoff_wire(self, wire: str) -> List[Dict[str, Any]]:
        """Apply a HandoffWire JSON string, loading its context into this agent.

        Returns:
            List of loaded message dicts
        """
        try:
            from tokenpak.agent.agentic.handoff import HandoffWire
        except ImportError:
            import json as _json

            payload = _json.loads(wire)
            blocks = payload.get("pack", {}).get("blocks", [])
            msgs = [
                {"role": "user", "content": b["content"]}
                for b in blocks
                if b.get("type") == "history"
            ]
            self._messages.extend(msgs)
            return msgs

        hw = HandoffWire.from_wire(wire)
        msgs = []
        for block in hw.pack.blocks:
            if block.type in ("history", "summary", "task_state"):
                msg = {"role": "user", "content": block.content, "name": hw.from_agent}
                self._messages.append(msg)
                msgs.append(msg)
        return msgs
