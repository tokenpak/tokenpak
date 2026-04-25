"""Generic passthrough adapter for unknown request formats."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping, Optional

from .base import FormatAdapter
from .canonical import CanonicalRequest


class PassthroughAdapter(FormatAdapter):
    source_format = "passthrough"
    # Catch-all fallback. Byte-preserved by definition (we don't know
    # the format, so we can't safely transform anything). No opt-in
    # capabilities — the proxy treats this as "forward only".
    capabilities = frozenset({"tip.byte-preserved-passthrough"})

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        return True

    def normalize(self, body: bytes) -> CanonicalRequest:
        try:
            data = json.loads(body)
        except Exception:
            return CanonicalRequest(
                model="unknown",
                system="",
                messages=[],
                tools=None,
                generation={},
                stream=False,
                raw_extra={"_raw_body": body.decode("utf-8", errors="replace")},
                source_format=self.source_format,
            )

        model = data.get("model", "unknown") if isinstance(data, dict) else "unknown"
        prompt = data.get("prompt", "") if isinstance(data, dict) else ""
        messages = copy.deepcopy(data.get("messages", [])) if isinstance(data, dict) else []

        if not messages and isinstance(prompt, str) and prompt:
            messages = [{"role": "user", "content": prompt}]

        return CanonicalRequest(
            model=model,
            system=copy.deepcopy(data.get("system", "")) if isinstance(data, dict) else "",
            messages=messages,
            tools=copy.deepcopy(data.get("tools")) if isinstance(data, dict) else None,
            generation={},
            stream=bool(data.get("stream", False)) if isinstance(data, dict) else False,
            raw_extra={"_passthrough_payload": data} if isinstance(data, dict) else {},
            source_format=self.source_format,
        )

    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        payload = canonical.raw_extra.get("_passthrough_payload")
        if isinstance(payload, dict):
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

        fallback: Dict[str, Any] = {
            "model": canonical.model,
            "messages": canonical.messages,
            "stream": canonical.stream,
        }
        if canonical.system not in (None, "", []):
            fallback["system"] = canonical.system
        if canonical.tools is not None:
            fallback["tools"] = canonical.tools
        fallback.update(canonical.generation)
        return json.dumps(fallback, ensure_ascii=False).encode("utf-8")

    def inject_system_context(self, body: bytes, injection_text: str) -> bytes:
        return body

    def get_default_upstream(self) -> str:
        return "https://api.anthropic.com"

    def get_sse_format(self) -> str:
        return "generic"
