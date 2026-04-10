"""Google Generative AI format adapter."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, FrozenSet, List, Mapping, Optional

from .base import FormatAdapter
from .canonical import CanonicalRequest

# JSON Schema type names → Google UPPERCASE equivalents
_GOOGLE_TYPE_MAP: Dict[str, str] = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}

# JSON Schema keywords not supported by Google functionDeclarations
_GOOGLE_UNSUPPORTED_SCHEMA_KEYS: FrozenSet[str] = frozenset({
    "$schema", "$ref", "$defs", "$id", "$comment", "definitions",
    "additionalProperties", "patternProperties",
    "oneOf", "anyOf", "allOf", "not",
    "if", "then", "else",
    "examples", "default", "title", "format",
})


class GoogleGenerativeAIAdapter(FormatAdapter):
    source_format = "google-generative-ai"

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
        payload: Dict[str, Any] = {
            "contents": self._to_google_contents(canonical.messages),
            "stream": canonical.stream,
        }
        if canonical.model and canonical.model != "unknown":
            payload["model"] = canonical.model

        if canonical.system not in (None, "", []):
            payload["systemInstruction"] = {"parts": self._to_google_parts(canonical.system)}

        if canonical.tools is not None and canonical.tools:
            payload["tools"] = self._translate_tools_to_function_declarations(canonical.tools)

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

    def extract_input_tokens(self, body: bytes) -> int:
        """Extract prompt token count from Google usageMetadata.

        Returns promptTokenCount when present in usageMetadata.
        Returns 0 when usageMetadata is absent; caller should fall back to heuristic.
        """
        try:
            data = json.loads(body)
        except Exception:
            return 0
        usage = data.get("usageMetadata", {})
        if "promptTokenCount" in usage:
            return int(usage["promptTokenCount"])
        return 0

    def extract_total_tokens(self, body: bytes) -> int:
        """Extract total token count from Google usageMetadata.

        Returns totalTokenCount when present in usageMetadata.
        Returns 0 when usageMetadata is absent.
        """
        try:
            data = json.loads(body)
        except Exception:
            return 0
        usage = data.get("usageMetadata", {})
        if "totalTokenCount" in usage:
            return int(usage["totalTokenCount"])
        return 0

    def get_default_upstream(self) -> str:
        return "https://generativelanguage.googleapis.com"

    def get_sse_format(self) -> str:
        return "google-ndjson"

    def _freeze_schema_for_google(self, schema: Any) -> Any:
        """Recursively sanitize a JSON Schema dict for Google's functionDeclarations format.

        - Converts type names to UPPERCASE (e.g. "string" → "STRING").
        - Removes keywords unsupported by Google (e.g. $schema, additionalProperties).
        - Handles type arrays: picks first non-null type; sets nullable=True if null present.
        - Recurses into properties and items.
        """
        if not isinstance(schema, dict):
            return schema

        result: Dict[str, Any] = {}
        for key, value in schema.items():
            if key in _GOOGLE_UNSUPPORTED_SCHEMA_KEYS:
                continue
            if key == "type":
                if isinstance(value, list):
                    non_null = [t for t in value if t != "null"]
                    chosen = non_null[0] if non_null else "string"
                    result["type"] = _GOOGLE_TYPE_MAP.get(chosen.lower(), chosen.upper())
                    if "null" in value:
                        result["nullable"] = True
                elif isinstance(value, str):
                    result["type"] = _GOOGLE_TYPE_MAP.get(value.lower(), value.upper())
                else:
                    result["type"] = value
            elif key == "properties":
                if isinstance(value, dict):
                    result["properties"] = {
                        k: self._freeze_schema_for_google(v) for k, v in value.items()
                    }
            elif key == "items":
                result["items"] = self._freeze_schema_for_google(value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    def _translate_tools_to_function_declarations(
        self, tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Translate OpenAI or Anthropic tools array to Google functionDeclarations format.

        Formats handled:
          - OpenAI:    tools[].type == "function" with tools[].function.{name, description, parameters}
          - Anthropic: tools[].{name, description, input_schema}
          - Google:    tools[].functionDeclarations already present — passed through unchanged
          - Generic:   tools[].{name, description?, parameters?} — treated as pre-normalized

        Returns a list in Google format: [{"functionDeclarations": [...]}]

        Raises ValueError when a tool's format cannot be identified or 'name' is missing.
        """
        # If the first tool already uses Google's native format, pass all through unchanged.
        if tools and isinstance(tools[0], dict) and "functionDeclarations" in tools[0]:
            return copy.deepcopy(tools)

        declarations: List[Dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue

            if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                # OpenAI format
                fn = tool["function"]
                name = fn.get("name", "")
                description = fn.get("description", "")
                parameters = fn.get("parameters")
            elif "input_schema" in tool:
                # Anthropic format
                name = tool.get("name", "")
                description = tool.get("description", "")
                parameters = tool.get("input_schema")
            elif "name" in tool:
                # Generic / pre-normalized format
                name = tool.get("name", "")
                description = tool.get("description", "")
                parameters = tool.get("parameters")
            else:
                raise ValueError(
                    "Cannot translate tool to Google functionDeclarations: "
                    f"unrecognized format (keys: {sorted(tool.keys())}). "
                    "Expected OpenAI (type='function'), Anthropic (input_schema), "
                    "or Google (functionDeclarations) format."
                )

            if not name:
                raise ValueError(
                    "Cannot translate tool to Google functionDeclarations: "
                    "'name' field is required but was missing or empty."
                )

            decl: Dict[str, Any] = {"name": name}
            if description:
                decl["description"] = description
            if parameters is not None:
                frozen = self._freeze_schema_for_google(parameters)
                if frozen:
                    decl["parameters"] = frozen

            declarations.append(decl)

        return [{"functionDeclarations": declarations}]

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
