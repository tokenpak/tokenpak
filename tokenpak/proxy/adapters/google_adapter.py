"""Google Generative AI format adapter."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Mapping, Optional

from .base import FormatAdapter
from .canonical import CanonicalRequest


class GoogleGenerativeAIAdapter(FormatAdapter):
    source_format = "google-generative-ai"

    def validate_tools(self) -> None:
        """
        Validate that function calling is supported for Google adapter.

        Google Gemini API supports function calling natively, but this adapter
        does not yet translate tool schemas. This method raises NotImplementedError
        to fail loudly rather than silently ignoring tool requests.

        See: ~/vault/01_PROJECTS/tokenpak-oss/tokenpak/docs/provider-gaps.md — Gap #1
        Status: Stub in place (2026-03-18). Real implementation in Q2 2026.
        """
        raise NotImplementedError(
            "Function calling is not yet supported in the Google adapter. "
            "This adapter does not translate tool schemas to Google "
            "function_declarations format. "
            "\n"
            "Workaround: Use the OpenAI or Anthropic adapter for tool-calling workflows. "
            "Both fully support function calling with bidirectional translation. "
            "\n"
            "Tracking: https://github.com/tokenpak/tokenpak/issues/54"
        )

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        lower_headers = {k.lower(): v for k, v in headers.items()}
        return "/v1beta/" in path or "x-goog-api-key" in lower_headers or "key=" in path

    def normalize(self, body: bytes) -> CanonicalRequest:
        data = json.loads(body)
        contents = data.get("contents", [])

        messages: List[Dict[str, Any]] = []
        for entry in contents:
            if not isinstance(entry, dict):
                continue
            role = entry.get("role", "user")
            parts = copy.deepcopy(entry.get("parts", []))
            messages.append({"role": role, "content": parts})

        system = ""
        system_instruction = data.get("systemInstruction")
        if isinstance(system_instruction, str):
            system = system_instruction
        elif isinstance(system_instruction, dict):
            system = copy.deepcopy(system_instruction.get("parts", []))

        consumed = {"model", "systemInstruction", "contents", "tools", "generationConfig", "stream"}
        generation: Dict[str, Any] = {}
        raw_extra: Dict[str, Any] = {}

        if "generationConfig" in data:
            generation["generationConfig"] = copy.deepcopy(data["generationConfig"])

        for key, value in data.items():
            if key in consumed:
                continue
            raw_extra[key] = value

        return CanonicalRequest(
            model=data.get("model", "unknown"),
            system=system,
            messages=messages,
            tools=copy.deepcopy(data.get("tools")),
            generation=generation,
            stream=bool(data.get("stream", False)),
            raw_extra=raw_extra,
            source_format=self.source_format,
        )

    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        # Fail loudly if tools are requested but not yet supported
        if canonical.tools is not None and len(canonical.tools) > 0:
            self.validate_tools()

        payload: Dict[str, Any] = {
            "contents": self._to_google_contents(canonical.messages),
            "stream": canonical.stream,
        }
        if canonical.model and canonical.model != "unknown":
            payload["model"] = canonical.model

        if canonical.system not in (None, "", []):
            payload["systemInstruction"] = {
                "parts": self._to_google_parts(canonical.system)
            }

        if canonical.tools is not None:
            payload["tools"] = copy.deepcopy(canonical.tools)

        if "generationConfig" in canonical.generation:
            payload["generationConfig"] = copy.deepcopy(canonical.generation["generationConfig"])

        payload.update(copy.deepcopy(canonical.raw_extra))
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def extract_response_tokens(self, body: bytes, is_sse: bool = False) -> int:
        if is_sse:
            return super().extract_response_tokens(body, is_sse=True)
        try:
            data = json.loads(body)
        except Exception:
            return 0
        usage = data.get("usageMetadata", {})
        if "candidatesTokenCount" in usage:
            return int(usage["candidatesTokenCount"])
        usage_fallback = data.get("usage", {})
        return int(usage_fallback.get("completion_tokens", usage_fallback.get("output_tokens", 0)))

    def get_default_upstream(self) -> str:
        return "https://generativelanguage.googleapis.com"

    def get_sse_format(self) -> str:
        return "google-ndjson"

    def _to_google_contents(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        contents: List[Dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            parts = self._to_google_parts(msg.get("content", ""))
            contents.append({"role": role, "parts": parts})
        return contents

    def _to_google_parts(self, content: Any) -> List[Dict[str, Any]]:
        if isinstance(content, str):
            return [{"text": content}]
        if isinstance(content, list):
            parts: List[Dict[str, Any]] = []
            for item in content:
                if isinstance(item, dict):
                    if "text" in item:
                        parts.append({"text": item["text"]})
                    else:
                        parts.append(copy.deepcopy(item))
                elif isinstance(item, str):
                    parts.append({"text": item})
            return parts
        if isinstance(content, dict):
            if "text" in content:
                return [{"text": content["text"]}]
            return [copy.deepcopy(content)]
        return [{"text": str(content)}]
