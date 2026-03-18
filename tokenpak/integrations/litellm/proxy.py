"""ASGI proxy endpoint handler for ``/tokenpak``.

Adds a ``/tokenpak`` endpoint to any Starlette/FastAPI app that accepts
a TokenPak JSON body and forwards a compiled prompt to the configured LLM.

Wire format accepted::

    POST /tokenpak
    {
        "model": "gpt-4",
        "tokenpak": {
            "version": "1.0",
            "blocks": [
                {"ref": "docs", "type": "text", "content": "...", "tokens": 200}
            ],
            "policies": {"compaction": "balanced", "budget": 8000}
        },
        "messages": [  // optional extra messages appended after system block
            {"role": "user", "content": "Summarize the docs."}
        ]
    }

Response is the raw LiteLLM response JSON, with an additional
``tokenpak_stats`` field.

Usage with Starlette::

    from starlette.applications import Starlette
    from starlette.routing import Route
    from tokenpak.integrations.litellm import ProxyHandler

    handler = ProxyHandler(default_model="gpt-4", budget=8000)

    app = Starlette(routes=[
        Route("/tokenpak", handler.handle, methods=["POST"]),
    ])
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from .formatter import compile_pack


class ProxyHandler:
    """ASGI-compatible handler for the ``/tokenpak`` proxy endpoint.

    Args:
        default_model: Fallback model if request doesn't specify one.
        budget: Default token budget.
        compaction: Default compaction strategy.
        litellm_kwargs: Extra kwargs forwarded to every ``litellm.completion`` call.
    """

    def __init__(
        self,
        default_model: str = "gpt-4",
        budget: int = 8000,
        compaction: str = "balanced",
        **litellm_kwargs: Any,
    ) -> None:
        self.default_model = default_model
        self.budget = budget
        self.compaction = compaction
        self.litellm_kwargs = litellm_kwargs

    async def handle(self, request: Any) -> Any:
        """Starlette-compatible request handler.

        Can also be called with a plain dict for testing::

            response = await handler.handle({"model": "gpt-4", "tokenpak": {...}})
        """
        # Support both Starlette Request and plain dict (for testing)
        if isinstance(request, dict):
            body = request
        else:
            raw = await request.body()
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as exc:
                return _json_error(400, f"Invalid JSON: {exc}")

        return await self._process(body)

    async def _process(self, body: Dict[str, Any]) -> Any:
        """Core processing logic."""
        # Extract TokenPak first (validate before importing litellm)
        pack_data = body.get("tokenpak")
        if pack_data is None:
            return _json_error(
                400,
                "Missing required field: 'tokenpak'. "
                "Request body must include: {'model': '...', 'tokenpak': {...}, 'messages': [...]}"
            )

        try:
            import litellm
        except ImportError:
            return _json_error(
                500,
                "LiteLLM not installed. Run: pip install litellm",
            )

        model = body.get("model") or self.default_model
        if not model:
            return _json_error(
                400,
                "No model specified. Provide 'model' in request body or set default in proxy config."
            )

        extra_messages = body.get("messages", [])

        # Parse budget/compaction from pack policies or top-level keys
        policies = pack_data.get("policies", {}) if isinstance(pack_data, dict) else {}
        budget = body.get("tokenpak_budget") or policies.get("budget") or self.budget
        compaction = (
            body.get("tokenpak_compaction") or policies.get("compaction") or self.compaction
        )

        # Validate compaction strategy early
        if compaction not in ("none", "balanced", "aggressive"):
            return _json_error(
                400,
                f"Invalid compaction strategy: {compaction!r}. "
                f"Choose from: 'none', 'balanced', 'aggressive'"
            )

        # Compile pack → messages
        t0 = time.perf_counter()
        try:
            messages = compile_pack(
                pack_data,
                budget=int(budget),
                compaction=str(compaction),
                existing_messages=extra_messages,
            )
        except TypeError as exc:
            return _json_error(
                400,
                f"Invalid TokenPak format: {str(exc)}. "
                f"tokenpak must be a dict, list of blocks, or BlockRegistry object."
            )
        except ValueError as exc:
            return _json_error(
                400,
                f"TokenPak compilation error: {str(exc)}. "
                f"Check 'budget' and 'compaction' values."
            )
        except Exception as exc:
            return _json_error(
                500,
                f"Unexpected error during pack compilation. Please retry. Error: {type(exc).__name__}"
            )
        
        compile_ms = round((time.perf_counter() - t0) * 1000, 1)

        # Forward to litellm
        call_kwargs = {**self.litellm_kwargs, "model": model, "messages": messages}
        # Remove any extra unknown keys that may have been passed
        for k in ("tokenpak", "tokenpak_budget", "tokenpak_compaction", "policies"):
            call_kwargs.pop(k, None)

        try:
            response = await litellm.acompletion(**call_kwargs)
        except Exception as exc:
            # Classify the error for better guidance
            exc_str = str(exc)
            if "401" in exc_str or "authentication" in exc_str.lower():
                return _json_error(
                    401,
                    f"Authentication failed. Check that API key for '{model}' is valid and has sufficient permissions."
                )
            elif "rate_limit" in exc_str.lower() or "429" in exc_str:
                return _json_error(
                    429,
                    f"Rate limit exceeded. Wait before retrying. May also indicate quota exhaustion."
                )
            elif "timeout" in exc_str.lower():
                return _json_error(
                    504,
                    f"Request timeout. The API took too long to respond. Increase timeout or try again."
                )
            elif "connection" in exc_str.lower() or "unreachable" in exc_str.lower():
                return _json_error(
                    503,
                    f"Connection failed. Check network and that API endpoint is reachable."
                )
            else:
                return _json_error(
                    502,
                    f"API returned error: {type(exc).__name__}. Check logs for details. If persistent, verify API endpoint and credentials."
                )

        # Attach stats
        try:
            usage = getattr(response, "usage", None)
            system_tokens = sum(
                len(m.get("content", "")) // 4 for m in messages if m.get("role") == "system"
            )
            response.tokenpak_stats = {
                "compile_ms": compile_ms,
                "budget": budget,
                "compaction": compaction,
                "system_tokens": system_tokens,
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
            }
        except Exception:
            pass

        return response


def _json_error(status: int, message: str) -> Dict:
    """Return a simple error dict (caller is responsible for HTTP wrapping)."""
    return {"error": {"status": status, "message": message}}
