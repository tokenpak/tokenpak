"""
TokenPak Google Format Handler (Stub)

Handles Google Gemini API request/response formats.
This is a stub implementation for format translation readiness.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class GoogleContent:
    """Represents content in Google format."""

    role: str  # "user", "model"
    parts: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "parts": self.parts}

    def get_text(self) -> str:
        """Extract text content."""
        parts = []
        for p in self.parts:
            if "text" in p:
                parts.append(p["text"])
        return "\n".join(parts)


class GoogleFormat:
    """
    Handler for Google Gemini API format (stub).

    Google uses:
    - "contents" array instead of "messages"
    - "parts" array within each content
    - "systemInstruction" for system prompt
    - Different role names ("model" instead of "assistant")

    TODO: Full implementation for multi-provider support.
    """

    PROVIDER = "google"

    @staticmethod
    def parse_request(body: bytes) -> Dict[str, Any]:
        """Parse a Google API request body."""
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    @staticmethod
    def extract_model(data: Dict[str, Any]) -> str:
        """
        Extract model name.
        Note: Google embeds model in URL path, not body.
        """
        return data.get("model", "gemini-pro")

    @staticmethod
    def extract_system(data: Dict[str, Any]) -> str:
        """Extract system instruction."""
        system = data.get("systemInstruction", {})
        if isinstance(system, dict):
            parts = system.get("parts", [])
            texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
            return "\n".join(texts)
        return ""

    @staticmethod
    def extract_contents(data: Dict[str, Any]) -> List[GoogleContent]:
        """Extract contents from request."""
        contents = []
        for c in data.get("contents", []):
            if isinstance(c, dict):
                contents.append(
                    GoogleContent(
                        role=c.get("role", "user"),
                        parts=c.get("parts", []),
                    )
                )
        return contents

    @staticmethod
    def count_tokens_approx(data: Dict[str, Any]) -> int:
        """Approximate token count."""
        total = 0

        # System instruction
        system = data.get("systemInstruction", {})
        if isinstance(system, dict):
            for part in system.get("parts", []):
                if "text" in part:
                    total += len(part["text"]) // 4

        # Contents
        for content in data.get("contents", []):
            for part in content.get("parts", []):
                if isinstance(part, dict):
                    if "text" in part:
                        total += len(part["text"]) // 4
                    if "inline_data" in part:
                        total += 1000  # Approximate for media

        return total

    @staticmethod
    def is_streaming(data: Dict[str, Any]) -> bool:
        """Check if request is streaming (determined by URL, not body)."""
        # Google uses ?alt=sse for streaming
        return False  # Must be determined from URL

    @staticmethod
    def build_request(
        contents: List[Dict[str, Any]],
        system_instruction: Optional[str] = None,
        generation_config: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bytes:
        """Build a Google API request body."""
        data = {"contents": contents}

        if system_instruction:
            data["systemInstruction"] = {"parts": [{"text": system_instruction}]}  # type: ignore[assignment]

        if generation_config:
            data["generationConfig"] = generation_config  # type: ignore[assignment]

        data.update(kwargs)

        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def extract_response_tokens(body: bytes) -> int:
        """Extract output token count from response."""
        try:
            data = json.loads(body)
            usage = data.get("usageMetadata", {})
            return usage.get("candidatesTokenCount", 0)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 0
