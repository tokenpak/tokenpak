# SPDX-License-Identifier: Apache-2.0
"""Token + cost projection for spend guard preflight.

Single source of truth for pricing is :mod:`tokenpak.models` — we never
duplicate the rate map (``feedback_always_dynamic``).

Text retains a cheap character heuristic. Media uses bounded header decoding
and provider sizing rules; provider usage can supply a higher input floor.
"""

from __future__ import annotations

import json
from typing import Any

from .contracts import RiskEstimate

# char-per-token heuristic. Anthropic tokens are ~3.6 chars on average
# for English; using 4 keeps us conservative-on-the-low-side.
_CHARS_PER_TOKEN = 4

# Default max_tokens when caller didn't set one. Anthropic API requires
# max_tokens to be set, so this is mostly a safety net for malformed
# requests (output cost is bounded by what the model is allowed to produce).
_DEFAULT_OUTPUT_TOKENS = 4096


def _count_text_tokens(text: str) -> int:
    """Cheap character-based token count.

    Returns 0 for empty/None inputs. Used both for the new request body and
    for prior cached context size estimation.
    """
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _content_tokens(value: Any, model: str, *, anthropic: bool) -> int:
    from .media import document_tokens, image_tokens

    total = 0
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            total += _count_text_tokens(item)
        elif isinstance(item, list):
            stack.extend(item)
        elif not isinstance(item, dict):
            total += _count_text_tokens(str(item)) if item is not None else 0
        else:
            kind = item.get("type")
            if kind in ("tool_use", "server_tool_use", "function_call", "custom_tool_call"):
                # Tool arguments are JSON text even when they describe media.
                total += _count_text_tokens(json.dumps(item, ensure_ascii=False, default=str))
            elif kind in ("image", "input_image", "image_url"):
                total += image_tokens(item, model, anthropic=anthropic)
            elif kind in ("document", "input_file", "file"):
                source = item.get("source") or {}
                if isinstance(source, dict) and source.get("type") == "text":
                    total += _count_text_tokens(source.get("data") or "")
                elif isinstance(source, dict) and source.get("type") == "content":
                    stack.append(source.get("content"))
                else:
                    total += document_tokens(item, model, anthropic=anthropic)
            elif kind in ("text", "input_text", "output_text", "summary_text"):
                total += _count_text_tokens(item.get("text") or "")
            elif kind == "thinking":
                total += _count_text_tokens(item.get("thinking") or item.get("text") or "")
            elif kind == "redacted_thinking":
                total += _count_text_tokens(item.get("data") or item.get("text") or "")
            else:
                for key, child in item.items():
                    if key not in ("cache_control", "signature", "type"):
                        total += _count_text_tokens(str(key))
                        stack.append(child)
    return total


def _walk_anthropic_body(body_json: dict[str, Any], model: str = "") -> tuple[int, int, float]:
    """Walk an Anthropic ``/v1/messages`` body.

    Returns ``(current_context_tokens, request_tokens, cache_hit_ratio)``:
    - current_context_tokens: tokens in messages that look cached
      (any block with ``cache_control`` set, or the first system block)
    - request_tokens: tokens in messages that don't carry a cache marker —
      these are "new" content to be tokenized fresh.
    - cache_hit_ratio: cached / total, 0.0..1.0.

    Best-effort. On malformed input, returns (0, total, 0.0) so the estimator
    treats the entire body as fresh — that's the safer (more expensive)
    projection.
    """
    cached = 0
    fresh = 0

    # System block(s)
    sys_block = body_json.get("system")
    if isinstance(sys_block, str):
        # String system prompt — Anthropic auto-caches large ones, so we
        # treat it as cached.
        cached += _count_text_tokens(sys_block)
    elif isinstance(sys_block, list):
        for blk in sys_block:
            if not isinstance(blk, dict):
                continue
            txt = blk.get("text", "") or ""
            if blk.get("cache_control"):
                cached += _count_text_tokens(txt)
            else:
                # Without explicit cache_control, system blocks tend to be
                # cached implicitly by Anthropic for repeat content.
                cached += _count_text_tokens(txt)

    # Messages
    for msg in body_json.get("messages", []) or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            fresh += _count_text_tokens(content)
            continue
        if not isinstance(content, list):
            continue
        for blk in content:
            if not isinstance(blk, dict):
                continue
            n = _content_tokens(blk, model or str(body_json.get("model") or ""), anthropic=True)
            if blk.get("cache_control"):
                cached += n
            else:
                fresh += n

    # Tools schema (large in long agent loops)
    tools = body_json.get("tools") or []
    if isinstance(tools, list) and tools:
        # Tools are stable across a session — almost always cached.
        cached += _count_text_tokens(json.dumps(tools, ensure_ascii=False, default=str))

    total = cached + fresh
    if total == 0:
        return 0, 0, 0.0
    ratio = cached / total
    return cached, fresh, ratio


def structural_tokens(body_json: dict[str, Any], model: str) -> tuple[int, int, int]:
    """Return total/cached/fresh input, excluding transport and output controls."""
    if "messages" in body_json:
        cached, fresh, _ = _walk_anthropic_body(body_json, model)
    else:
        cached = _content_tokens(body_json.get("instructions"), model, anthropic=False)
        if body_json.get("tools"):
            cached += _count_text_tokens(
                json.dumps(body_json["tools"], ensure_ascii=False, default=str)
            )
        fresh = _content_tokens(body_json.get("input"), model, anthropic=False)
        if "input" not in body_json:
            fresh += _content_tokens(body_json.get("prompt"), model, anthropic=False)
    return cached + fresh, cached, fresh


def estimate(
    body: bytes,
    model: str,
    *,
    fallback_session_tokens: int = 0,
) -> RiskEstimate:
    """Project tokens + cost for a single provider-bound request.

    Parameters
    ----------
    body : bytes
        Raw request body. Decoded best-effort as UTF-8 JSON.
    model : str
        Resolved model id (e.g. ``claude-opus-4-7``).
    fallback_session_tokens : int
        Provider total input plus the structural appended suffix, bound by
        the caller to this session and model. Applies even to valid JSON.
    """
    # Pricing — always dynamic (per ``feedback_always_dynamic``)
    try:
        from tokenpak.models import get_rates

        rates = get_rates(model)
    except Exception:
        # Most-expensive frontier-class default if the registry is wedged.
        # Better to over-estimate and warn than silently under-block.
        rates = {"input": 15.0, "output": 75.0, "cached": 1.50}

    # Parse body
    body_json: dict[str, Any] = {}
    body_text = ""
    try:
        body_text = body.decode("utf-8", errors="replace")
        body_json = json.loads(body_text)
        if not isinstance(body_json, dict):
            body_json = {}
    except Exception:
        body_json = {}

    if body_json:
        _, cached, fresh = structural_tokens(body_json, model)
    else:
        # Unknown shape — fall back to whole-body fresh.
        total = _count_text_tokens(body_text)
        cached = 0
        fresh = total
        denom = cached + fresh
        hit_ratio = (cached / denom) if denom else 0.0

    projected_input = max(cached + fresh, max(0, fallback_session_tokens))
    fresh = projected_input - cached
    hit_ratio = cached / projected_input if projected_input else 0.0
    projected_output = int(
        body_json.get("max_tokens")
        or body_json.get("max_output_tokens")
        or body_json.get("max_completion_tokens")
        or _DEFAULT_OUTPUT_TOKENS
    )

    # Cost components: cached input gets the ``cached`` rate (~10% of input),
    # fresh input gets full input rate, output gets output rate.
    in_per_mtok = float(rates.get("input", 3.0))
    out_per_mtok = float(rates.get("output", 15.0))
    cached_per_mtok = float(rates.get("cached", in_per_mtok * 0.1))

    cost = (
        cached * cached_per_mtok / 1_000_000
        + fresh * in_per_mtok / 1_000_000
        + projected_output * out_per_mtok / 1_000_000
    )

    return RiskEstimate(
        model=model,
        current_context_tokens=cached,
        request_tokens=fresh,
        projected_input_tokens=projected_input,
        projected_output_tokens=projected_output,
        projected_cost_usd=round(cost, 6),
        cache_hit_ratio=round(hit_ratio, 4),
        rates={
            "input": in_per_mtok,
            "output": out_per_mtok,
            "cached": cached_per_mtok,
        },
    )
