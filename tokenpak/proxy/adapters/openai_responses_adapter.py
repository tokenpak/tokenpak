"""OpenAI Responses API format adapter."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Mapping, Optional

from .base import FormatAdapter
from .canonical import CanonicalRequest


class OpenAIResponsesAdapter(FormatAdapter):
    source_format = "openai-responses"

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        return "/v1/responses" in path

    def normalize(self, body: bytes) -> CanonicalRequest:
        data = json.loads(body)
        input_value = data.get("input", "")

        messages: List[Dict[str, Any]] = []
        input_format = "none"

        if isinstance(input_value, str):
            input_format = "string"
            if input_value:
                messages = [{"role": "user", "content": input_value}]
        elif isinstance(input_value, list):
            if input_value and all(isinstance(item, dict) and "role" in item for item in input_value):
                input_format = "message_array"
                messages = copy.deepcopy(input_value)
            else:
                input_format = "content_array"
                messages = [{"role": "user", "content": copy.deepcopy(input_value)}]
        elif isinstance(input_value, dict):
            input_format = "single_message"
            if "role" in input_value:
                messages = [copy.deepcopy(input_value)]
            else:
                messages = [{"role": "user", "content": copy.deepcopy(input_value)}]

        consumed = {"model", "instructions", "input", "tools", "stream"}
        generation: Dict[str, Any] = {}
        raw_extra: Dict[str, Any] = {"_input_format": input_format}

        for key, value in data.items():
            if key in consumed:
                continue
            if key in {"max_output_tokens", "temperature", "top_p", "metadata", "reasoning", "text", "tool_choice"}:
                generation[key] = value
            else:
                raw_extra[key] = value

        return CanonicalRequest(
            model=data.get("model", "unknown"),
            system=copy.deepcopy(data.get("instructions", "")),
            messages=messages,
            tools=copy.deepcopy(data.get("tools")),
            generation=generation,
            stream=bool(data.get("stream", False)),
            raw_extra=raw_extra,
            source_format=self.source_format,
        )

    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        input_format = canonical.raw_extra.get("_input_format", "message_array")
        input_value: Any = []

        if input_format == "string":
            text = ""
            if canonical.messages:
                text = self._content_to_text(canonical.messages[-1].get("content", ""))
            input_value = text
        elif input_format == "content_array":
            if canonical.messages:
                content = canonical.messages[-1].get("content", [])
                if isinstance(content, list):
                    input_value = copy.deepcopy(content)
                elif isinstance(content, str):
                    input_value = [{"type": "input_text", "text": content}]
                else:
                    input_value = [{"type": "input_text", "text": self._content_to_text(content)}]
            else:
                input_value = []
        elif input_format == "single_message":
            input_value = copy.deepcopy(canonical.messages[0]) if canonical.messages else {"role": "user", "content": ""}
        else:
            input_value = copy.deepcopy(canonical.messages)

        payload: Dict[str, Any] = {
            "model": canonical.model,
            "input": input_value,
            "stream": canonical.stream,
        }
        if canonical.system not in (None, "", []):
            payload["instructions"] = copy.deepcopy(canonical.system)
        if canonical.tools is not None:
            payload["tools"] = copy.deepcopy(canonical.tools)

        payload.update(copy.deepcopy(canonical.generation))
        for key, value in canonical.raw_extra.items():
            if key == "_input_format":
                continue
            payload[key] = copy.deepcopy(value)

        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def get_default_upstream(self) -> str:
        return "https://api.openai.com"

    def get_sse_format(self) -> str:
        return "openai-responses-sse"
