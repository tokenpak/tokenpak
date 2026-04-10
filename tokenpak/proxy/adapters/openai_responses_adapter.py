"""OpenAI Responses API format adapter."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import OrderedDict
from typing import Any, Dict, List, Mapping, Optional

from .base import FormatAdapter
from .canonical import CanonicalRequest

_VOLATILE_EXTRA_KEYS = {
    "metadata",
    "user",
    "conversation",
    "conversation_id",
    "request_id",
    "run_id",
    "trace_id",
    "session_id",
    "timestamp",
    "created_at",
    "updated_at",
}


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
            if input_value and all(
                isinstance(item, dict) and "role" in item for item in input_value
            ):
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
            if key in {
                "max_output_tokens",
                "temperature",
                "top_p",
                "metadata",
                "reasoning",
                "text",
                "tool_choice",
                "prompt_cache_key",
                "prompt_cache_retention",
            }:
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
            input_value = (
                copy.deepcopy(canonical.messages[0])
                if canonical.messages
                else {"role": "user", "content": ""}
            )
        else:
            input_value = copy.deepcopy(canonical.messages)

        payload: "OrderedDict[str, Any]" = OrderedDict()
        payload["model"] = canonical.model
        if canonical.system not in (None, "", []):
            payload["instructions"] = copy.deepcopy(canonical.system)
        if canonical.tools is not None:
            payload["tools"] = self._stable_tools(canonical.tools)

        payload["input"] = input_value
        payload["stream"] = canonical.stream

        generation = copy.deepcopy(canonical.generation)
        prompt_cache_key = generation.pop("prompt_cache_key", None)
        prompt_cache_retention = generation.pop("prompt_cache_retention", None)

        if prompt_cache_key is None:
            prompt_cache_key = self._build_prompt_cache_key(canonical)
        if prompt_cache_key:
            payload["prompt_cache_key"] = prompt_cache_key
        if prompt_cache_retention is not None:
            payload["prompt_cache_retention"] = prompt_cache_retention

        for key in (
            "max_output_tokens",
            "temperature",
            "top_p",
            "reasoning",
            "text",
            "tool_choice",
            "metadata",
        ):
            if key in generation:
                payload[key] = generation.pop(key)

        for key, value in generation.items():
            payload[key] = value

        stable_extra: Dict[str, Any] = {}
        volatile_extra: Dict[str, Any] = {}
        for key, value in canonical.raw_extra.items():
            if key == "_input_format":
                continue
            if key in _VOLATILE_EXTRA_KEYS:
                volatile_extra[key] = copy.deepcopy(value)
            else:
                stable_extra[key] = copy.deepcopy(value)

        for key in sorted(stable_extra):
            payload[key] = stable_extra[key]
        for key in sorted(volatile_extra):
            payload[key] = volatile_extra[key]

        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def get_default_upstream(self) -> str:
        return "https://api.openai.com"

    def get_sse_format(self) -> str:
        return "openai-responses-sse"

    def _stable_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def _tool_name(tool: Dict[str, Any]) -> str:
            if not isinstance(tool, dict):
                return ""
            if isinstance(tool.get("name"), str):
                return tool["name"]
            fn = tool.get("function")
            if isinstance(fn, dict) and isinstance(fn.get("name"), str):
                return fn["name"]
            return ""

        return sorted(copy.deepcopy(tools), key=lambda t: (_tool_name(t), json.dumps(t, sort_keys=True, ensure_ascii=False)))

    def _build_prompt_cache_key(self, canonical: CanonicalRequest) -> str:
        stable_payload = OrderedDict()
        stable_payload["model"] = canonical.model
        stable_payload["instructions"] = copy.deepcopy(canonical.system)
        stable_payload["tools"] = self._stable_tools(canonical.tools or [])
        stable_payload["input_prefix"] = self._stable_message_prefix(canonical.messages)
        stable_payload["input_format"] = canonical.raw_extra.get("_input_format", "message_array")

        for key in sorted(k for k in canonical.raw_extra.keys() if k not in _VOLATILE_EXTRA_KEYS and not k.startswith("_")):
            stable_payload[key] = copy.deepcopy(canonical.raw_extra[key])

        digest = hashlib.sha256(
            json.dumps(stable_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:32]
        return f"tokenpak:openai-prefix:{digest}"

    def _stable_message_prefix(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not messages:
            return []
        prefix = copy.deepcopy(messages)
        while prefix and prefix[-1].get("role") == "user":
            prefix.pop()
            break
        return prefix
