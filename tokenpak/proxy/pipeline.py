"""Modular request pipeline for the tokenpak proxy.

Orchestrates request-processing stages that were previously inline in
``proxy.py._proxy_to()`` (~lines 7786-8680).  Each stage is a pure
function that accepts a ``ProxyRequest`` and a ``route_policy`` dict and
returns a (possibly modified) ``ProxyRequest``.

Two top-level paths:
- **byte-preserved** (Claude Code): saves original bytes, runs vault
  search, restores bytes, applies byte-level vault splice.  JSON
  re-serialization is forbidden — Anthropic billing routes on the exact
  byte representation.
- **full pipeline** (OpenClaw / SDK): JSON-level processing with
  compaction, cache control, compression, etc.

Usage::

    from tokenpak.proxy.pipeline import process_request
    from tokenpak.proxy.route_policy import get_policy

    policy = get_policy(route)
    result = process_request(request, policy)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from tokenpak.proxy.request import ProxyRequest

# ---------------------------------------------------------------------------
# Pipeline trace — lightweight record of what each stage did
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    """What a single pipeline stage produced."""
    name: str
    skipped: bool = False
    skip_reason: str = ""
    tokens_delta: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """Outcome of a full ``process_request()`` call."""
    request: ProxyRequest
    stages: List[StageResult] = field(default_factory=list)
    vault_injection_text: str = ""


# ---------------------------------------------------------------------------
# Individual stage functions
# ---------------------------------------------------------------------------

def stage_cache_poison_removal(
    request: ProxyRequest,
    policy: Dict[str, Any],
) -> Tuple[ProxyRequest, StageResult]:
    """Strip dynamic UUIDs, timestamps, heartbeat counters from the body.

    Delegates to ``tokenpak.proxy.cache_poison.strip_cache_poisons``.
    """
    result = StageResult(name="cache_poison_removal")

    if policy.get("cache_poison_removal") != "enabled":
        result.skipped = True
        result.skip_reason = "disabled_by_policy"
        return request, result

    if not request.body:
        result.skipped = True
        result.skip_reason = "empty_body"
        return request, result

    from tokenpak.proxy.cache_poison import strip_cache_poisons

    original = request.body
    scrubbed = strip_cache_poisons(original)
    request.body = scrubbed
    result.details["changed"] = scrubbed != original
    return request, result


def stage_vault_injection(
    request: ProxyRequest,
    policy: Dict[str, Any],
    *,
    adapter: Any = None,
) -> Tuple[ProxyRequest, StageResult]:
    """Inject vault context into the request body.

    For ``byte_splice`` mode (Claude Code), this stage only *computes*
    the injection text — the actual byte splicing happens later in
    ``stage_byte_restore``.  For ``json_inject`` mode (OpenClaw/SDK),
    the injection is applied directly via ``inject_vault_context()``.

    Returns:
        (request, result) — result.details["injection_text"] is set
        when vault content was found (used by byte_restore stage).
    """
    result = StageResult(name="vault_injection")
    injection_mode = policy.get("vault_injection", "disabled")

    if injection_mode == "disabled" or not request.body:
        result.skipped = True
        result.skip_reason = "disabled_or_empty"
        return request, result

    try:
        from tokenpak.proxy.vault_bridge import inject_vault_context
    except ImportError:
        result.skipped = True
        result.skip_reason = "vault_bridge_unavailable"
        return request, result

    # vault_bridge returns (body, tokens, sources) — 3 values.
    # The monolith version returns 4 (+ raw_injection_text).
    # Handle both during the migration.
    ret = inject_vault_context(request.body, adapter=adapter, request=request)
    if len(ret) == 4:
        body, injected_tokens, injected_sources, injection_text = ret
    else:
        body, injected_tokens, injected_sources = ret
        injection_text = ""

    if injection_mode == "byte_splice":
        # Don't apply the JSON-mutated body — save the text for byte splicing
        result.details["injection_text"] = injection_text
        result.details["injected_tokens"] = injected_tokens
        result.details["injected_sources"] = injected_sources
        result.tokens_delta = injected_tokens
    else:
        # json_inject: apply the modified body
        request.body = body
        result.details["injection_text"] = injection_text
        result.details["injected_tokens"] = injected_tokens
        result.details["injected_sources"] = injected_sources
        result.tokens_delta = injected_tokens

    return request, result


def stage_header_forwarding(
    request: ProxyRequest,
    policy: Dict[str, Any],
    *,
    route: str = "",
    client_has_auth: bool = False,
) -> Tuple[ProxyRequest, StageResult]:
    """Apply per-route header allowlisting.

    Delegates to ``tokenpak.proxy.headers.forward_headers``.
    """
    result = StageResult(name="header_forwarding")

    from tokenpak.proxy.headers import forward_headers

    original_count = len(request.headers)
    request.headers = forward_headers(
        request.headers,
        route=route,
        client_has_auth=client_has_auth,
    )
    result.details["headers_before"] = original_count
    result.details["headers_after"] = len(request.headers)
    return request, result


def stage_auth_injection(
    request: ProxyRequest,
    policy: Dict[str, Any],
    *,
    client_has_auth: bool = False,
    get_pool_key: Any = None,
    get_cli_token: Any = None,
) -> Tuple[ProxyRequest, StageResult]:
    """Inject proxy-side auth credentials when the client has none.

    For ``passthrough`` mode, headers are left untouched.
    For ``inject`` mode, credentials come from the key pool or CLI token.

    Args:
        get_pool_key: Callable returning ``(key_str, key_index)`` or None.
        get_cli_token: Callable returning a token string or None.
    """
    result = StageResult(name="auth_injection")
    auth_mode = policy.get("auth", "passthrough")

    if auth_mode == "passthrough" or client_has_auth:
        result.skipped = True
        result.skip_reason = "passthrough_or_client_auth"
        return request, result

    pool_key = ""
    if get_pool_key is not None:
        try:
            pool_key, _ = get_pool_key()
        except Exception:
            pass

    if not pool_key and get_cli_token is not None:
        try:
            pool_key = get_cli_token()
        except Exception:
            pass

    if pool_key:
        request.headers["x-api-key"] = pool_key
        # Remove competing auth headers
        for k in list(request.headers.keys()):
            if k.lower() in ("authorization",):
                del request.headers[k]
        result.details["injected"] = True
    else:
        result.details["injected"] = False
        result.skip_reason = "no_credentials_available"

    return request, result


def stage_cache_control(
    request: ProxyRequest,
    policy: Dict[str, Any],
) -> Tuple[ProxyRequest, StageResult]:
    """Apply stable cache control stamps to the request body.

    Delegates to ``tokenpak.proxy.prompt_builder.apply_stable_cache_control``.
    Skipped for ``client_managed`` routes (Claude Code).
    """
    result = StageResult(name="cache_control")

    if policy.get("cache_control") == "client_managed":
        result.skipped = True
        result.skip_reason = "client_managed"
        return request, result

    if policy.get("stable_cache_stamps") != "enabled":
        result.skipped = True
        result.skip_reason = "stamps_disabled"
        return request, result

    if not request.body:
        result.skipped = True
        result.skip_reason = "empty_body"
        return request, result

    try:
        from tokenpak.proxy.prompt_builder import apply_stable_cache_control
        request.body = apply_stable_cache_control(request.body)
        result.details["applied"] = True
    except ImportError:
        result.skipped = True
        result.skip_reason = "prompt_builder_unavailable"

    return request, result


def stage_compaction(
    request: ProxyRequest,
    policy: Dict[str, Any],
    *,
    adapter: Any = None,
    compact_fn: Any = None,
) -> Tuple[ProxyRequest, StageResult]:
    """Run compaction on the request body to reduce token count.

    Skipped for ``byte_preserved`` routes (Claude Code).

    Args:
        compact_fn: Callable matching ``compact_request_body(body, adapter=)``
            signature. Injected from the monolith during migration.
    """
    result = StageResult(name="compaction")

    if policy.get("compaction") != "enabled":
        result.skipped = True
        result.skip_reason = "disabled_by_policy"
        return request, result

    if not request.body or compact_fn is None:
        result.skipped = True
        result.skip_reason = "no_body_or_no_compactor"
        return request, result

    try:
        body, sent_tokens, original_tokens, protected_tokens = compact_fn(
            request.body, adapter=adapter
        )
        request.body = body
        result.tokens_delta = -(original_tokens - sent_tokens) if original_tokens else 0
        result.details["original_tokens"] = original_tokens
        result.details["sent_tokens"] = sent_tokens
        result.details["protected_tokens"] = protected_tokens
    except Exception as e:
        result.details["error"] = str(e)

    return request, result


def stage_ttl_hotfix(
    request: ProxyRequest,
    policy: Dict[str, Any],
) -> Tuple[ProxyRequest, StageResult]:
    """Strip default-TTL cache_control blocks that precede explicit-TTL blocks.

    Anthropic rejects when a ``cache_control`` block with ``ttl="1h"``
    appears AFTER a default-ttl (5m) block in document order.

    Skipped for ``client_managed`` routes (Claude Code manages its own ordering).
    """
    result = StageResult(name="ttl_hotfix")

    if policy.get("cache_cap") != "enabled":
        result.skipped = True
        result.skip_reason = "cache_cap_disabled"
        return request, result

    if not request.body:
        result.skipped = True
        result.skip_reason = "empty_body"
        return request, result

    try:
        body_data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        result.skipped = True
        result.skip_reason = "json_parse_error"
        return request, result

    # Collect all blocks with cache_control in document order
    blocks: list = []
    for item in body_data.get("system") or []:
        if isinstance(item, dict):
            blocks.append(item)
    for item in body_data.get("tools") or []:
        if isinstance(item, dict):
            blocks.append(item)
    for msg in body_data.get("messages") or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    blocks.append(block)

    # Find the last block with an explicit TTL
    last_explicit_ttl_idx = None
    for i, block in enumerate(blocks):
        cc = block.get("cache_control") if isinstance(block, dict) else None
        if isinstance(cc, dict) and cc.get("ttl") is not None:
            last_explicit_ttl_idx = i

    if last_explicit_ttl_idx is None:
        result.skipped = True
        result.skip_reason = "no_explicit_ttl"
        return request, result

    # Strip default-TTL blocks before the last explicit-TTL block
    stripped = 0
    for i in range(last_explicit_ttl_idx):
        cc = blocks[i].get("cache_control") if isinstance(blocks[i], dict) else None
        if isinstance(cc, dict) and cc.get("ttl") is None:
            blocks[i].pop("cache_control", None)
            stripped += 1

    if stripped > 0:
        request.body = json.dumps(body_data, ensure_ascii=False).encode("utf-8")
        result.details["stripped_count"] = stripped
    else:
        result.skipped = True
        result.skip_reason = "no_default_ttl_before_explicit"

    return request, result


def stage_byte_restore(
    request: ProxyRequest,
    policy: Dict[str, Any],
    *,
    original_body: bytes = b"",
    vault_injection_text: str = "",
    max_inject_chars: int = 2000,
    min_query_len: int = 50,
    adapter: Any = None,
) -> Tuple[ProxyRequest, StageResult]:
    """Restore original body bytes and apply byte-level vault injection.

    This is the final stage for the byte-preserved path (Claude Code).
    It discards any JSON-mutated body and restores the exact original
    bytes, then splices vault context at the byte level.

    Args:
        original_body: The unmodified request body captured before pipeline.
        vault_injection_text: Text computed by ``stage_vault_injection``.
        max_inject_chars: Max chars for vault injection (env TOKENPAK_CC_INJECT_MAX_CHARS).
        min_query_len: Skip injection if user query is shorter (env TOKENPAK_CC_INJECT_MIN_QUERY).
    """
    result = StageResult(name="byte_restore")

    if policy.get("body") != "byte_preserved":
        result.skipped = True
        result.skip_reason = "not_byte_preserved"
        return request, result

    if not original_body:
        result.skipped = True
        result.skip_reason = "no_original_body"
        return request, result

    # Import byte-level injection functions (modular request.py)
    try:
        from tokenpak.proxy.request import _byte_inject_system_block
    except ImportError:
        _byte_inject_system_block = None

    if not vault_injection_text or max_inject_chars <= 0 or _byte_inject_system_block is None:
        request.body = original_body
        result.details["action"] = "restored_original"
        return request, result

    # Check query length relevance gate
    query_signal = ""
    try:
        from tokenpak.proxy.vault_bridge import extract_query_signal
        query_signal = extract_query_signal(original_body, adapter=adapter)
    except (ImportError, Exception):
        pass

    if len(query_signal) < min_query_len:
        request.body = original_body
        result.details["action"] = "restored_original_short_query"
        return request, result

    trimmed = vault_injection_text[:max_inject_chars]
    request.body = _byte_inject_system_block(original_body, trimmed)
    result.details["action"] = "byte_spliced"
    result.details["injected_chars"] = len(trimmed)
    return request, result


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

def process_request(
    request: ProxyRequest,
    policy: Dict[str, Any],
    *,
    route: str = "",
    client_has_auth: bool = False,
    adapter: Any = None,
    compact_fn: Any = None,
    get_pool_key: Any = None,
    get_cli_token: Any = None,
) -> PipelineResult:
    """Run all request-side pipeline stages based on route policy.

    Args:
        request: The incoming ``ProxyRequest``.
        policy: Route behavior dict from ``get_policy(route)``.
        route: Route classification string (for header forwarding).
        client_has_auth: Whether the client supplied its own auth.
        adapter: Token-counting adapter (passed to vault/compaction).
        compact_fn: Compaction function (injected from monolith).
        get_pool_key: Key-pool callable for auth injection.
        get_cli_token: CLI-token callable for auth injection.

    Returns:
        ``PipelineResult`` with the processed request and stage traces.
    """
    if policy.get("body") == "byte_preserved":
        return _passthrough_pipeline(
            request, policy,
            route=route,
            client_has_auth=client_has_auth,
            adapter=adapter,
            get_pool_key=get_pool_key,
            get_cli_token=get_cli_token,
        )
    return _full_pipeline(
        request, policy,
        route=route,
        client_has_auth=client_has_auth,
        adapter=adapter,
        compact_fn=compact_fn,
        get_pool_key=get_pool_key,
        get_cli_token=get_cli_token,
    )


def _passthrough_pipeline(
    request: ProxyRequest,
    policy: Dict[str, Any],
    *,
    route: str = "",
    client_has_auth: bool = False,
    adapter: Any = None,
    get_pool_key: Any = None,
    get_cli_token: Any = None,
) -> PipelineResult:
    """Byte-preserved path — Claude Code requests.

    Saves original body, computes vault injection text (via JSON parse),
    restores original bytes, applies byte-level splice.
    """
    pipeline = PipelineResult(request=request)
    original_body = bytes(request.body) if request.body else b""

    # Stage 0.9: Cache poison removal
    # (runs on a copy to compute vault query; body is restored later)
    request, stage = stage_cache_poison_removal(request, policy)
    pipeline.stages.append(stage)

    # Stage 1: Vault injection (computes injection text only — no body mutation)
    request, stage = stage_vault_injection(request, policy, adapter=adapter)
    pipeline.stages.append(stage)
    vault_text = stage.details.get("injection_text", "")
    pipeline.vault_injection_text = vault_text

    # Stage: Header forwarding
    request, stage = stage_header_forwarding(
        request, policy, route=route, client_has_auth=client_has_auth
    )
    pipeline.stages.append(stage)

    # Stage: Auth injection (passthrough for Claude Code, but included for completeness)
    request, stage = stage_auth_injection(
        request, policy,
        client_has_auth=client_has_auth,
        get_pool_key=get_pool_key,
        get_cli_token=get_cli_token,
    )
    pipeline.stages.append(stage)

    # Stage: Byte restore + vault splice
    request, stage = stage_byte_restore(
        request, policy,
        original_body=original_body,
        vault_injection_text=vault_text,
        adapter=adapter,
    )
    pipeline.stages.append(stage)

    pipeline.request = request
    return pipeline


def _full_pipeline(
    request: ProxyRequest,
    policy: Dict[str, Any],
    *,
    route: str = "",
    client_has_auth: bool = False,
    adapter: Any = None,
    compact_fn: Any = None,
    get_pool_key: Any = None,
    get_cli_token: Any = None,
) -> PipelineResult:
    """Full JSON pipeline — OpenClaw / SDK requests.

    Runs all stages: cache poison removal, vault injection,
    stable cache control, compaction, TTL hotfix, header forwarding,
    auth injection.
    """
    pipeline = PipelineResult(request=request)

    # Stage 0.9: Cache poison removal
    request, stage = stage_cache_poison_removal(request, policy)
    pipeline.stages.append(stage)

    # Stage 1: Vault injection (JSON-level)
    request, stage = stage_vault_injection(request, policy, adapter=adapter)
    pipeline.stages.append(stage)
    pipeline.vault_injection_text = stage.details.get("injection_text", "")

    # Stage: Stable cache control
    request, stage = stage_cache_control(request, policy)
    pipeline.stages.append(stage)

    # Stage 2: Compaction
    request, stage = stage_compaction(
        request, policy, adapter=adapter, compact_fn=compact_fn
    )
    pipeline.stages.append(stage)

    # Stage: TTL ordering hotfix
    request, stage = stage_ttl_hotfix(request, policy)
    pipeline.stages.append(stage)

    # Stage: Header forwarding
    request, stage = stage_header_forwarding(
        request, policy, route=route, client_has_auth=client_has_auth
    )
    pipeline.stages.append(stage)

    # Stage: Auth injection
    request, stage = stage_auth_injection(
        request, policy,
        client_has_auth=client_has_auth,
        get_pool_key=get_pool_key,
        get_cli_token=get_cli_token,
    )
    pipeline.stages.append(stage)

    pipeline.request = request
    return pipeline
