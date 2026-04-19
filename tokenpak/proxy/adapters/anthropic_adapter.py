"""Anthropic format adapter."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping, Optional

from .base import FormatAdapter
from .canonical import CanonicalRequest


class AnthropicAdapter(FormatAdapter):
    source_format = "anthropic-messages"

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        lower = {k.lower(): v for k, v in headers.items()}
        return (
            "/v1/messages" in path
            or "x-api-key" in lower
            or "anthropic-version" in lower
        )

    def normalize(self, body: bytes) -> CanonicalRequest:
        data = json.loads(body)

        consumed = {"model", "system", "messages", "tools", "stream"}
        generation: Dict[str, Any] = {}
        raw_extra: Dict[str, Any] = {}

        for key, value in data.items():
            if key in consumed:
                continue
            if key in {"max_tokens", "temperature", "top_p", "top_k", "stop_sequences", "metadata"}:
                generation[key] = value
            else:
                raw_extra[key] = value

        return CanonicalRequest(
            model=data.get("model", "unknown"),
            system=copy.deepcopy(data.get("system", "")),
            messages=copy.deepcopy(data.get("messages", [])),
            tools=copy.deepcopy(data.get("tools")),
            generation=generation,
            stream=bool(data.get("stream", False)),
            raw_extra=raw_extra,
            source_format=self.source_format,
        )

    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        payload: Dict[str, Any] = {
            "model": canonical.model,
            "messages": copy.deepcopy(canonical.messages),
            "stream": canonical.stream,
        }
        if canonical.system not in (None, "", []):
            payload["system"] = copy.deepcopy(canonical.system)
        if canonical.tools is not None:
            payload["tools"] = copy.deepcopy(canonical.tools)

        payload.update(copy.deepcopy(canonical.generation))
        payload.update(copy.deepcopy(canonical.raw_extra))
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def inject_system_context(self, body: bytes, injection_text: str) -> bytes:
        """
        Inject volatile content into the system prompt with correct cache boundary.

        Cache boundary strategy (Anthropic prompt caching):
          - STABLE prefix (original system blocks) → gets cache_control: ephemeral
          - VOLATILE injection (retrieval/vault content) → NO cache_control

        Placing cache_control on the volatile block causes cache churn (0% hit rate)
        because the volatile content changes every request.  The cache_control must
        anchor the STABLE prefix so Anthropic can cache up to that boundary.
        """
        canonical = self.normalize(body)
        # Volatile injection block — intentionally NO cache_control
        volatile_block: dict = {"type": "text", "text": injection_text}

        if isinstance(canonical.system, str):
            if canonical.system:
                # String system → convert to list; stable block gets the cache marker
                canonical.system = [
                    {
                        "type": "text",
                        "text": canonical.system,
                        "cache_control": {"type": "ephemeral"},
                    },
                    volatile_block,  # volatile: no cache_control
                ]
            else:
                # Empty system — just store raw text (no stable prefix to mark)
                canonical.system = injection_text
        elif isinstance(canonical.system, list):
            # Mark the LAST EXISTING block (stable prefix anchor)
            if canonical.system:
                marked = list(canonical.system)
                for i in range(len(marked) - 1, -1, -1):
                    blk = marked[i]
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        if blk.get("cache_control") != {"type": "ephemeral"}:
                            marked[i] = dict(blk, cache_control={"type": "ephemeral"})
                        break
                canonical.system = marked
            canonical.system.append(volatile_block)  # volatile: no cache_control
        else:
            canonical.system = injection_text
        return self.denormalize(canonical)

    def get_default_upstream(self) -> str:
        return "https://api.anthropic.com"

    def get_sse_format(self) -> str:
        return "anthropic-sse"
