"""xAI Grok format adapter.

xAI's API is OpenAI-compatible (chat completions format), so this adapter
reuses OpenAIChatAdapter logic for normalisation/denormalisation but adds:
  - Grok-specific detection (api.x.ai host, x-api-key with grok model, Authorization bearer)
  - Default upstream: https://api.x.ai
  - Cost model: xAI published pricing (per-million tokens, USD)
  - SSE format follows OpenAI's generic SSE schema
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Mapping, Optional

from .base import FormatAdapter
from .canonical import CanonicalRequest

# xAI published pricing as of 2025-Q1 (USD per 1M tokens)
# Source: https://docs.x.ai/docs/models
_GROK_PRICING: Dict[str, Dict[str, float]] = {
    "grok-3": {"input": 3.00, "output": 15.00},
    "grok-3-fast": {"input": 5.00, "output": 25.00},
    "grok-3-mini": {"input": 0.30, "output": 0.50},
    "grok-3-mini-fast": {"input": 0.60, "output": 4.00},
    "grok-2": {"input": 2.00, "output": 10.00},
    "grok-2-mini": {"input": 0.20, "output": 0.40},
    # Legacy/vision models
    "grok-vision-beta": {"input": 5.00, "output": 15.00},
    "grok-beta": {"input": 5.00, "output": 15.00},
}

# Fallback if model not in table
_DEFAULT_PRICING: Dict[str, float] = {"input": 3.00, "output": 15.00}

# Generation params that go in CanonicalRequest.generation (mirrors OpenAI adapter)
_GENERATION_KEYS = {
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
}


class GrokAdapter(FormatAdapter):
    """Adapter for xAI Grok models via api.x.ai (OpenAI-compatible format)."""

    source_format = "xai-grok"

    # --- Detection -----------------------------------------------------------

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        """Detect Grok requests.

        Priority signals (any one is sufficient):
        1. Host header contains api.x.ai
        2. x-xai-api-key header present
        3. Model name starts with "grok-" (in body)
        """
        lower = {k.lower(): v for k, v in headers.items()}

        # Host-based detection
        host = lower.get("host", "")
        if "api.x.ai" in host:
            return True

        # xAI-specific header
        if "x-xai-api-key" in lower:
            return True

        # Model-name-based detection from body
        if body and b"grok-" in body:
            try:
                data = json.loads(body)
                model = data.get("model", "")
                if isinstance(model, str) and model.startswith("grok-"):
                    return True
            except (json.JSONDecodeError, AttributeError):
                pass

        return False

    # --- Normalise / Denormalise ---------------------------------------------

    def normalize(self, body: bytes) -> CanonicalRequest:
        """Parse xAI/OpenAI chat completions payload into CanonicalRequest."""
        data = json.loads(body)
        messages: List[Dict[str, Any]] = copy.deepcopy(data.get("messages", []))

        # Extract system message (OpenAI convention: first message with role=system)
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
            if key in _GENERATION_KEYS:
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
        """Serialise CanonicalRequest back to OpenAI-compatible JSON for xAI."""
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

    # --- Response token extraction -------------------------------------------

    def extract_response_tokens(self, body: bytes, is_sse: bool = False) -> int:
        """Extract completion token count from xAI response (OpenAI schema)."""
        if is_sse:
            return super().extract_response_tokens(body, is_sse=True)
        try:
            data = json.loads(body)
        except Exception:
            return 0
        usage = data.get("usage", {})
        return int(usage.get("completion_tokens", 0))

    # --- Cost model ----------------------------------------------------------

    def estimate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> Dict[str, float]:
        """Estimate cost in USD for a Grok API call.

        Returns a dict with keys: input_cost, output_cost, total_cost.
        Prices are per-million-token (USD).
        """
        pricing = _GROK_PRICING.get(model, _DEFAULT_PRICING)
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        return {
            "input_cost": input_cost,
            "output_cost": output_cost,
            "total_cost": input_cost + output_cost,
        }

    # --- Upstream / format config --------------------------------------------

    def get_default_upstream(self) -> str:
        return "https://api.x.ai"

    def get_sse_format(self) -> str:
        return "openai-sse"
