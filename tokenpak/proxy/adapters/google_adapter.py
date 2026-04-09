"""Google Generative AI format adapter."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Mapping, Optional

from .base import FormatAdapter
from .canonical import CanonicalRequest


class GoogleGenerativeAIAdapter(FormatAdapter):
    source_format = "google-generative-ai"

    # JSON Schema primitive type → Google type name (uppercase)
    _GOOGLE_TYPE_MAP: Dict[str, str] = {
        "string": "STRING",
        "number": "NUMBER",
        "integer": "INTEGER",
        "boolean": "BOOLEAN",
        "array": "ARRAY",
        "object": "OBJECT",
    }

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
            payload["tools"] = self._translate_tools_to_google(canonical.tools)
        elif canonical.tools is not None:
            # Empty list — preserve as-is (backward compat)
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

    def _translate_tools_to_google(self, tools: List[Any]) -> List[Dict[str, Any]]:
        """Translate OpenAI or Anthropic tool definitions to Google functionDeclarations.

        Accepts:
          - OpenAI format: [{"type": "function", "function": {"name": ..., "parameters": ...}}]
          - Anthropic format: [{"name": ..., "description": ..., "input_schema": ...}]

        Returns Google format:
          [{"functionDeclarations": [{"name": ..., "description": ..., "parameters": ...}]}]

        Raises ValueError for unrecognized tool formats or untranslatable schemas.
        """
        function_declarations: List[Dict[str, Any]] = []

        for tool in tools:
            if not isinstance(tool, dict):
                raise ValueError(
                    f"Cannot translate tool to Google functionDeclarations: "
                    f"expected a dict, got {type(tool).__name__}"
                )

            # OpenAI format: {"type": "function", "function": {...}}
            if tool.get("type") == "function" and "function" in tool:
                fn = tool["function"]
                decl: Dict[str, Any] = {"name": fn["name"]}
                if fn.get("description"):
                    decl["description"] = fn["description"]
                if "parameters" in fn:
                    decl["parameters"] = self._google_schema_freeze(fn["parameters"])
                function_declarations.append(decl)

            # Anthropic format: {"name": ..., "input_schema": {...}}
            elif "name" in tool and "input_schema" in tool:
                decl = {"name": tool["name"]}
                if tool.get("description"):
                    decl["description"] = tool["description"]
                decl["parameters"] = self._google_schema_freeze(tool["input_schema"])
                function_declarations.append(decl)

            else:
                raise ValueError(
                    f"Cannot translate tool to Google functionDeclarations: "
                    f"unrecognized tool format. Expected OpenAI (type='function') "
                    f"or Anthropic (input_schema) format. Got keys: {sorted(tool.keys())}"
                )

        return [{"functionDeclarations": function_declarations}]

    def _google_schema_freeze(self, schema: Any) -> Any:
        """Convert a JSON Schema object to Google functionDeclarations parameter format.

        - Uppercases type names (object→OBJECT, string→STRING, etc.)
        - Strips JSON Schema keys unsupported by Google's API
        - Recursively processes nested objects (properties) and arrays (items)
        - Translates T|null unions to type=T + nullable=true
        - Raises ValueError for $ref (unresolvable without a definitions context)
          or unsupported multi-type unions
        """
        if not isinstance(schema, dict):
            return schema

        if "$ref" in schema:
            raise ValueError(
                f"Cannot translate schema with '$ref' to Google format: "
                f"resolve all $ref references before calling the Google adapter. "
                f"Offending schema snippet: {json.dumps(schema)[:200]}"
            )

        result: Dict[str, Any] = {}

        # --- type -----------------------------------------------------------
        if "type" in schema:
            raw_type = schema["type"]
            if isinstance(raw_type, list):
                # JSON Schema allows ["string", "null"] — Google uses nullable
                non_null = [t for t in raw_type if t != "null"]
                if len(non_null) == 0:
                    result["type"] = "STRING"
                    result["nullable"] = True
                elif len(non_null) == 1:
                    result["type"] = self._GOOGLE_TYPE_MAP.get(non_null[0], non_null[0].upper())
                    if "null" in raw_type:
                        result["nullable"] = True
                else:
                    raise ValueError(
                        f"Cannot translate multi-type union {raw_type} to Google format: "
                        f"Google only supports T|null unions, not arbitrary multi-type unions."
                    )
            elif isinstance(raw_type, str):
                result["type"] = self._GOOGLE_TYPE_MAP.get(raw_type, raw_type.upper())

        # --- scalar fields Google supports ----------------------------------
        for key in ("description", "enum", "nullable"):
            if key in schema:
                result[key] = copy.deepcopy(schema[key])

        # --- required -------------------------------------------------------
        if "required" in schema:
            result["required"] = list(schema["required"])

        # --- properties (recurse) -------------------------------------------
        if "properties" in schema:
            result["properties"] = {
                k: self._google_schema_freeze(v)
                for k, v in schema["properties"].items()
            }

        # --- items (arrays, recurse) ----------------------------------------
        if "items" in schema:
            result["items"] = self._google_schema_freeze(schema["items"])

        return result

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
