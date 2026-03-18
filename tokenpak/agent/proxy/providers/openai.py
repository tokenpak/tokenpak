"""
TokenPak OpenAI Format Handler

Handles OpenAI API request/response formats.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union


@dataclass
class OpenAIMessage:
    """Represents a message in OpenAI format."""

    role: str  # "system", "user", "assistant", "tool"
    content: Union[str, List[Dict[str, Any]], None]
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content  # type: ignore[assignment]
        if self.name:
            d["name"] = self.name
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls  # type: ignore[assignment]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d

    def get_text(self) -> str:
        """Extract text content from message."""
        if self.content is None:
            return ""
        if isinstance(self.content, str):
            return self.content
        elif isinstance(self.content, list):
            parts = []
            for item in self.content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
            return "\n".join(parts)
        return ""


class OpenAIFormat:
    """
    Handler for OpenAI API format.

    OpenAI uses:
    - First message with role="system" for system prompt
    - "messages" array with role/content pairs
    - Content can be string or array of content parts
    - Supports tool calls and function calling
    """

    PROVIDER = "openai"

    @staticmethod
    def parse_request(body: bytes) -> Dict[str, Any]:
        """Parse an OpenAI API request body."""
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
        """Extract system prompt text (first system message)."""
        for msg in data.get("messages", []):
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                    return "\n".join(parts)
        return ""

    @staticmethod
    def extract_messages(data: Dict[str, Any]) -> List[OpenAIMessage]:
        """Extract messages from request."""
        messages = []
        for msg in data.get("messages", []):
            if isinstance(msg, dict):
                messages.append(
                    OpenAIMessage(
                        role=msg.get("role", "user"),
                        content=msg.get("content"),
                        name=msg.get("name"),
                        tool_calls=msg.get("tool_calls"),
                        tool_call_id=msg.get("tool_call_id"),
                    )
                )
        return messages

    @staticmethod
    def count_tokens_approx(data: Dict[str, Any]) -> int:
        """Approximate token count for request."""
        total = 0

        for msg in data.get("messages", []):
            content = msg.get("content")
            if isinstance(content, str):
                total += len(content) // 4
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            total += len(part.get("text", "")) // 4
                        if part.get("type") == "image_url":
                            total += 1000  # Approximate for images

        # Tools/functions
        for tool in data.get("tools", []):
            total += len(json.dumps(tool)) // 4

        return total

    @staticmethod
    def is_streaming(data: Dict[str, Any]) -> bool:
        """Check if request is streaming."""
        return data.get("stream", False)

    @staticmethod
    def build_request(
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        stream: bool = True,
        **kwargs,
    ) -> bytes:
        """Build an OpenAI API request body."""
        data = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }

        if max_tokens is not None:
            data["max_tokens"] = max_tokens

        data.update(kwargs)

        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def extract_response_tokens(body: bytes) -> int:
        """Extract output token count from response."""
        try:
            data = json.loads(body)
            usage = data.get("usage", {})
            return usage.get("completion_tokens", 0)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 0
