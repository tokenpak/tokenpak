"""OpenAI Chat Completions format adapter."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping, Optional

from .base import FormatAdapter
from .canonical import CanonicalRequest


class OpenAIChatAdapter(FormatAdapter):
    source_format = "openai-chat"

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        return "/v1/chat/completions" in path

    def normalize(self, body: bytes) -> CanonicalRequest:
        data = json.loads(body)
        messages = copy.deepcopy(data.get("messages", []))

        system: Any = ""
        if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
            system = copy.deepcopy(messages[0].get("content", ""))
            messages = messages[1:]

        consumed = {"model", "messages", "tools", "functions", "stream"}
        generation: Dict[str, Any] = {}
        raw_extra: Dict[str, Any] = {}

        for key, value in data.items():
            if key in consumed:
                continue
            if key in {
                "max_tokens",
                "max_completion_tokens",
                "temperature",
                "top_p",
                "presence_penalty",
                "frequency_penalty",
                "logit_bias",
                "stop",
                "seed",
                "response_format",
                "tool_choice",
                "parallel_tool_calls",
            }:
                generation[key] = value
            else:
                raw_extra[key] = value

        tools = data.get("tools")
        if tools is None and data.get("functions") is not None:
            tools = data.get("functions")

        return CanonicalRequest(
            model=data.get("model", "unknown"),
            system=system,
            messages=messages,
            tools=copy.deepcopy(tools),
            generation=generation,
            stream=bool(data.get("stream", False)),
            raw_extra=raw_extra,
            source_format=self.source_format,
        )

    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        messages = copy.deepcopy(canonical.messages)
        if canonical.system not in (None, "", []):
            messages = [{"role": "system", "content": copy.deepcopy(canonical.system)}] + messages

        payload: Dict[str, Any] = {
            "model": canonical.model,
            "messages": messages,
            "stream": canonical.stream,
        }
        if canonical.tools is not None:
            payload["tools"] = copy.deepcopy(canonical.tools)
        payload.update(copy.deepcopy(canonical.generation))
        payload.update(copy.deepcopy(canonical.raw_extra))

        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def get_default_upstream(self) -> str:
        return "https://api.openai.com"

    def get_sse_format(self) -> str:
        return "openai-sse"
