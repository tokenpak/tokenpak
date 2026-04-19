"""TokenPakMiddleware for LiteLLM Router.

Drop-in middleware that intercepts LiteLLM ``Router.completion`` and
``Router.acompletion`` calls, detects TokenPak inputs, compiles them to
wire-format messages, and then forwards the enriched request to the model.

Usage::

    from litellm import Router
    from tokenpak.integrations.litellm import TokenPakMiddleware

    router = Router(
        model_list=[{"model_name": "gpt-4", "litellm_params": {...}}],
        middleware=[TokenPakMiddleware(compaction="balanced", budget=8000)],
    )

    pack = BlockRegistry(...)
    response = await router.acompletion(model="gpt-4", tokenpak=pack)
    print(response.tokenpak_stats)

Also works as a standalone call wrapper via ``TokenPakMiddleware.wrap_kwargs()``
for use with plain ``litellm.completion()``::

    from tokenpak.integrations.litellm import TokenPakMiddleware

    mw = TokenPakMiddleware()
    patched_kwargs = mw.wrap_kwargs(tokenpak=pack, model="gpt-4")
    response = litellm.completion(**patched_kwargs)
"""

from __future__ import annotations

import time
from typing import Any, Dict

from .formatter import compile_pack
from .parser import (
    extract_budget_from_kwargs,
    extract_compaction_from_kwargs,
    parse_tokenpak_request,
)


class TokenPakMiddleware:
    """LiteLLM Router middleware that compiles TokenPak packs before sending.

    Args:
        compaction: Default compaction strategy for all calls.
            ``"none"`` — no compaction (raw blocks concatenated)
            ``"balanced"`` — heuristic compaction (default)
            ``"aggressive"`` — hard-truncate to fit budget
        budget: Default token budget.  Per-call ``tokenpak_budget=`` overrides this.
        telemetry: Whether to attach ``tokenpak_stats`` to responses.
    """

    def __init__(
        self,
        compaction: str = "balanced",
        budget: int = 8000,
        telemetry: bool = True,
    ) -> None:
        if compaction not in ("none", "balanced", "aggressive"):
            raise ValueError(
                f"Invalid compaction strategy: {compaction!r}. "
                f"Valid options: 'none' (no compaction), "
                f"'balanced' (recommended, ~30% savings), "
                f"'aggressive' (maximum compression, ~40%+ savings)"
            )
        self.compaction = compaction
        self.budget = budget
        self.telemetry = telemetry

    # ------------------------------------------------------------------
    # LiteLLM Router middleware interface
    # ------------------------------------------------------------------

    def pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: Dict[str, Any],
        call_type: str,
    ) -> Dict[str, Any]:
        """Called by LiteLLM Router before forwarding to provider.

        Detects and compiles any TokenPak in ``data``, replacing it with a
        compiled ``messages`` list.
        """
        pack, cleaned = parse_tokenpak_request(data)
        if pack is None:
            return cleaned

        budget = extract_budget_from_kwargs(cleaned) or self.budget
        compaction = extract_compaction_from_kwargs(cleaned) or self.compaction

        existing = cleaned.get("messages", [])
        t0 = time.perf_counter()
        messages = compile_pack(
            pack, budget=budget, compaction=compaction, existing_messages=existing
        )
        compile_ms = round((time.perf_counter() - t0) * 1000, 1)

        cleaned["messages"] = messages

        # Stash stats for post_call_hook
        if self.telemetry:
            cleaned["_tokenpak_meta"] = {
                "compile_ms": compile_ms,
                "budget": budget,
                "compaction": compaction,
                "system_tokens": sum(
                    len(m.get("content", "")) // 4 for m in messages if m.get("role") == "system"
                ),
            }

        return cleaned

    def post_call_success_hook(
        self,
        data: Dict[str, Any],
        user_api_key_dict: Any,
        response: Any,
    ) -> Any:
        """Attach ``tokenpak_stats`` to the response object if telemetry is on."""
        if not self.telemetry:
            return response

        meta = data.pop("_tokenpak_meta", {})
        if meta:
            usage = getattr(response, "usage", None)
            prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
            compressed = meta.get("system_tokens", 0)
            stats = {
                "compile_ms": meta["compile_ms"],
                "budget": meta["budget"],
                "compaction": meta["compaction"],
                "system_tokens": compressed,
                "prompt_tokens": prompt_tokens,
            }
            if prompt_tokens and compressed:
                savings_pct = round((1 - compressed / max(prompt_tokens, 1)) * 100, 1)
                stats["savings_pct"] = max(0.0, savings_pct)
            try:
                response.tokenpak_stats = stats
            except Exception:
                pass  # Some response types may be immutable

        return response

    # ------------------------------------------------------------------
    # Standalone wrapper (for plain litellm.completion)
    # ------------------------------------------------------------------

    def wrap_kwargs(self, **kwargs: Any) -> Dict[str, Any]:
        """Pre-process kwargs for ``litellm.completion(**wrapped)``.

        Compiles any ``tokenpak=`` kwarg into ``messages``.  Drops
        middleware-internal keys so litellm never sees them.

        Example::

            mw = TokenPakMiddleware()
            response = litellm.completion(**mw.wrap_kwargs(
                model="gpt-4",
                tokenpak=pack,
            ))
        """
        pack, cleaned = parse_tokenpak_request(kwargs)
        if pack is None:
            return cleaned

        budget = extract_budget_from_kwargs(cleaned) or self.budget
        compaction = extract_compaction_from_kwargs(cleaned) or self.compaction
        existing = cleaned.pop("messages", [])

        cleaned["messages"] = compile_pack(
            pack,
            budget=budget,
            compaction=compaction,
            existing_messages=existing,
        )

        # Remove internal keys litellm doesn't understand
        for key in ("tokenpak_budget", "tokenpak_compaction", "_raw_body", "_tokenpak_meta"):
            cleaned.pop(key, None)

        return cleaned
