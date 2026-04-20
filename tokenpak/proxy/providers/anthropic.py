"""
TokenPak Anthropic Format Handler

Handles Anthropic Claude API request/response formats.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union


@dataclass
class AnthropicMessage:
    """Represents a message in Anthropic format."""

    role: str  # "user", "assistant"
    content: Union[str, List[Dict[str, Any]]]

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "content": self.content}

    def get_text(self) -> str:
        """Extract text content from message."""
        if isinstance(self.content, str):
            return self.content
        elif isinstance(self.content, list):
            parts = []
            for item in self.content:
                if isinstance(item, dict) and "text" in item:
                    parts.append(item["text"])
            return "\n".join(parts)
        return ""


class AnthropicFormat:
    """
    Handler for Anthropic Claude API format.

    Anthropic uses:
    - "system" field for system prompt (string or list of content blocks)
    - "messages" array with role/content pairs
    - Content can be string or list of content blocks (text, image, etc.)
    """

    PROVIDER = "anthropic"

    @staticmethod
    def parse_request(body: bytes) -> Dict[str, Any]:
        """
        Parse an Anthropic API request body.

        Args:
            body: Raw request body bytes

        Returns:
            Parsed request dict
        """
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    @staticmethod
    def extract_model(data: Dict[str, Any]) -> str:
        """Extract model name from request."""
        return data.get("model", "unknown")

    @staticmethod
    def extract_system(data: Dict[str, Any]) -> str:
        """Extract system prompt text."""
        system = data.get("system", "")
        if isinstance(system, str):
            return system
        elif isinstance(system, list):
            parts = []
            for item in system:
                if isinstance(item, dict) and "text" in item:
                    parts.append(item["text"])
            return "\n".join(parts)
        return ""

    @staticmethod
    def extract_messages(data: Dict[str, Any]) -> List[AnthropicMessage]:
        """Extract messages from request."""
        messages = []
        for msg in data.get("messages", []):
            if isinstance(msg, dict):
                messages.append(
                    AnthropicMessage(
                        role=msg.get("role", "user"),
                        content=msg.get("content", ""),
                    )
                )
        return messages

    @staticmethod
    def count_tokens_approx(data: Dict[str, Any]) -> int:
        """
        Approximate token count for request.

        Uses ~4 chars per token heuristic when tiktoken unavailable.
        """
        total = 0

        # System prompt
        system = data.get("system", "")
        if isinstance(system, str):
            total += len(system) // 4
        elif isinstance(system, list):
            for item in system:
                if isinstance(item, dict) and "text" in item:
                    total += len(item["text"]) // 4

        # Messages
        for msg in data.get("messages", []):
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content) // 4
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        if "text" in part:
                            total += len(part["text"]) // 4
                        if part.get("type") == "image":
                            total += 1000  # Approximate for images

        return total

    @staticmethod
    def is_streaming(data: Dict[str, Any]) -> bool:
        """Check if request is streaming."""
        return data.get("stream", False)

    @staticmethod
    def build_request(
        model: str,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        stream: bool = True,
        **kwargs,
    ) -> bytes:
        """
        Build an Anthropic API request body.

        Args:
            model: Model name
            messages: List of message dicts
            system: Optional system prompt
            max_tokens: Max tokens in response
            stream: Whether to stream
            **kwargs: Additional parameters

        Returns:
            Request body as bytes
        """
        data = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if system:
            data["system"] = system

        data.update(kwargs)

        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def inject_system_content(
        data: Dict[str, Any],
        content: str,
        cache_control: bool = True,
    ) -> Dict[str, Any]:
        """
        Inject additional content into system prompt.

        Used for vault context injection.

        Args:
            data: Request data dict (modified in place)
            content: Content to inject
            cache_control: Add ephemeral cache control

        Returns:
            Modified data dict
        """
        block = {"type": "text", "text": content}
        if cache_control:
            block["cache_control"] = {"type": "ephemeral"}  # type: ignore[assignment]

        system = data.get("system", "")

        if isinstance(system, str):
            data["system"] = [
                {"type": "text", "text": system},
                block,
            ]
        elif isinstance(system, list):
            data["system"].append(block)
        else:
            data["system"] = content

        return data

    @staticmethod
    def extract_response_tokens(body: bytes) -> int:
        """Extract output token count from response."""
        try:
            data = json.loads(body)
            usage = data.get("usage", {})
            return usage.get("output_tokens", 0)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 0

    @staticmethod
    def extract_cache_tokens(body: bytes) -> Dict[str, int]:
        """Extract cache token counts from response."""
        try:
            data = json.loads(body)
            usage = data.get("usage", {})
            return {
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            }
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
