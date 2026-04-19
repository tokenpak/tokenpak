"""Base adapter contract for request/response format handling."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Callable, Mapping, Optional, Tuple

from .canonical import CanonicalRequest

TokenCounter = Callable[[str], int]


class FormatAdapter(ABC):
    """Abstract format adapter for provider-specific payloads."""

    source_format: str = "unknown"

    @abstractmethod
    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def normalize(self, body: bytes) -> CanonicalRequest:
        raise NotImplementedError

    @abstractmethod
    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def get_default_upstream(self) -> str:
        raise NotImplementedError

    def get_sse_format(self) -> str:
        return "generic"

    def extract_request_tokens(
        self,
        body: bytes,
        token_counter: Optional[TokenCounter] = None,
    ) -> Tuple[str, int]:
        try:
            canonical = self.normalize(body)
        except Exception:
            return "unknown", 0

        counter = token_counter or (lambda text: len(text) // 4)
        tokens = 0

        system = canonical.system
        if isinstance(system, str):
            tokens += counter(system)
        elif isinstance(system, list):
            for part in system:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    tokens += counter(part["text"])

        for msg in canonical.messages:
            content = msg.get("content", "")
            tokens += self._count_content_tokens(content, counter)

        return canonical.model or "unknown", tokens

    def extract_response_tokens(self, body: bytes, is_sse: bool = False) -> int:
        if is_sse:
            return self._extract_sse_output_tokens(body)

        try:
            data = json.loads(body)
        except Exception:
            return 0

        usage = data.get("usage", {})
        if "output_tokens" in usage:
            return int(usage["output_tokens"])
        if "completion_tokens" in usage:
            return int(usage["completion_tokens"])
        if "candidatesTokenCount" in usage:
            return int(usage["candidatesTokenCount"])
        return 0

    def extract_query_signal(self, body: bytes) -> str:
        try:
            canonical = self.normalize(body)
        except Exception:
            return ""

        last_user = ""
        for msg in reversed(canonical.messages):
            if msg.get("role") == "user":
                last_user = self._content_to_text(msg.get("content", ""))
                if last_user:
                    break

        words = last_user.split()
        if len(words) > 50:
            last_user = " ".join(words[:50])
        return last_user

    def inject_system_context(self, body: bytes, injection_text: str) -> bytes:
        canonical = self.normalize(body)
        system = canonical.system

        if isinstance(system, str):
            canonical.system = f"{system}\n\n{injection_text}" if system else injection_text
        elif isinstance(system, list):
            system.append({"type": "text", "text": injection_text})
        else:
            canonical.system = injection_text

        return self.denormalize(canonical)

    def _extract_sse_output_tokens(self, sse_bytes: bytes) -> int:
        try:
            text = sse_bytes.decode("utf-8", errors="replace")
        except Exception:
            return 0

        output_tokens = 0
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                continue
            try:
                event = json.loads(data_str)
            except Exception:
                continue

            usage = event.get("usage", {})
            if "output_tokens" in usage:
                output_tokens = int(usage["output_tokens"])
            elif "completion_tokens" in usage:
                output_tokens = int(usage["completion_tokens"])
            elif event.get("type") == "message_delta":
                usage = event.get("usage", {})
                if "output_tokens" in usage:
                    output_tokens = int(usage["output_tokens"])

        return output_tokens

    def _count_content_tokens(self, content: Any, counter: TokenCounter) -> int:
        if isinstance(content, str):
            return counter(content)
        if isinstance(content, list):
            total = 0
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        total += counter(text)
                    if part.get("type") == "image":
                        total += 1000
            return total
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str):
                return counter(text)
        return 0

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    if isinstance(part.get("text"), str):
                        parts.append(part["text"])
                    elif isinstance(part.get("input_text"), str):
                        parts.append(part["input_text"])
            return " ".join(parts)
        if isinstance(content, dict):
            if isinstance(content.get("text"), str):
                return content["text"]
        return ""
