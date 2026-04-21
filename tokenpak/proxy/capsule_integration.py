"""
tokenpak.proxy.capsule_integration
========================================

Wire the CapsuleBuilder into the proxy request pipeline.

This module provides a request hook that invokes the capsule builder
on incoming request bodies, compressing verbose historical context
before forwarding to the upstream model.

Feature Flag
------------
- Env: ``TOKENPAK_CAPSULE_BUILDER=1`` to enable (default: off)
- Can also be set via ``tokenpak config set capsule_builder.enabled true``

Usage
-----
::

    from tokenpak.proxy.capsule_integration import get_capsule_request_hook
    from tokenpak.proxy.server import start_proxy

    # Get the capsule-enabled request hook
    hook = get_capsule_request_hook()

    # Or wrap an existing hook
    hook = get_capsule_request_hook(base_hook=my_existing_hook)

    start_proxy(request_hook=hook)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Callable, Optional, Tuple

if TYPE_CHECKING:
    from .server import PipelineTrace, StageTrace

logger = logging.getLogger(__name__)

# Feature flag (env var takes precedence, then config file)
_CAPSULE_BUILDER_ENABLED: Optional[bool] = None


def _is_capsule_enabled() -> bool:
    """Check if capsule builder is enabled via env or config."""
    global _CAPSULE_BUILDER_ENABLED

    # Cached for performance (checked on every request)
    if _CAPSULE_BUILDER_ENABLED is not None:
        return _CAPSULE_BUILDER_ENABLED

    # Env var takes precedence
    env_val = os.environ.get("TOKENPAK_CAPSULE_BUILDER")
    if env_val is not None:
        _CAPSULE_BUILDER_ENABLED = env_val == "1"
        return _CAPSULE_BUILDER_ENABLED

    # Check config file
    try:
        from tokenpak.agent.config import load_config

        config = load_config()
        capsule_cfg = config.get("capsule_builder", {})
        _CAPSULE_BUILDER_ENABLED = capsule_cfg.get("enabled", False)
    except Exception:
        _CAPSULE_BUILDER_ENABLED = False

    return _CAPSULE_BUILDER_ENABLED


def _create_stage_trace(name: str, enabled: bool = True) -> "StageTrace":
    """Create a StageTrace object (import deferred to avoid circular imports)."""
    from .server import StageTrace

    return StageTrace(name=name, enabled=enabled)


def capsule_request_hook(
    body: bytes,
    model: str,
    trace: Optional["PipelineTrace"] = None,
    *,
    base_hook: Optional[Callable] = None,
) -> Tuple[bytes, int, int, int]:
    """
    Request hook that applies capsule compression.

    Parameters
    ----------
    body : bytes
        Raw request body (JSON).
    model : str
        Model name being requested.
    trace : PipelineTrace, optional
        Pipeline trace for logging/debugging.
    base_hook : callable, optional
        Another request hook to chain after capsule building.

    Returns
    -------
    (body, sent_tokens, raw_tokens, protected_tokens)
        Modified body and token counts.
    """
    import time

    raw_tokens = _estimate_tokens(body)
    sent_tokens = raw_tokens
    protected_tokens = 0

    # Check feature flag
    if not _is_capsule_enabled():
        if trace:
            stage = _create_stage_trace("capsule_builder", enabled=False)
            stage.details["skip_reason"] = "disabled"
            trace.stages.append(stage)

        # ── Tool schema normalization (even when capsule builder is disabled) ──
        try:
            from tokenpak.proxy.tool_schema_registry import get_registry as _get_registry

            body, _schema_changed = _get_registry().normalize_request(body)
        except Exception as _exc:
            logger.debug("tool_schema_registry: normalization skipped (%s)", _exc)

        # Chain to base hook if present
        if base_hook:
            return base_hook(body, model, trace)
        return body, sent_tokens, raw_tokens, protected_tokens

    # Run capsule builder
    t0 = time.monotonic()
    try:
        from tokenpak.compression.capsules.builder import CapsuleBuilder

        builder = CapsuleBuilder(enabled=True)
        new_body, stats = builder.process(body)

        duration_ms = (time.monotonic() - t0) * 1000

        # Log at INFO level when capsules are created
        if stats.get("blocks_capsulized", 0) > 0:
            logger.info(
                "Capsule builder: %d blocks compressed, ratio=%.2f, duration=%.1fms",
                stats["blocks_capsulized"],
                stats["ratio"],
                duration_ms,
            )

        # Update trace if provided
        if trace:
            stage = _create_stage_trace("capsule_builder", enabled=True)
            stage.input_tokens = stats.get("chars_in", 0) // 4  # rough estimate
            stage.output_tokens = stats.get("chars_out", 0) // 4
            stage.tokens_delta = stage.input_tokens - stage.output_tokens
            stage.duration_ms = round(duration_ms, 3)
            stage.details = {
                "blocks_capsulized": stats.get("blocks_capsulized", 0),
                "ratio": stats.get("ratio", 1.0),
                "skip_reason": stats.get("skip_reason"),
            }
            trace.stages.append(stage)

        # Update body if modified
        if not stats.get("skipped", True):
            body = new_body
            sent_tokens = _estimate_tokens(body)

    except Exception as exc:
        logger.warning("Capsule builder error (falling back to original): %s", exc)
        if trace:
            stage = _create_stage_trace("capsule_builder", enabled=True)
            stage.details = {"error": str(exc)}
            trace.stages.append(stage)

    # ── Tool schema normalization (Poison 3: freeze schemas for cache stability) ──
    # Normalize tool schemas to bit-for-byte identical bytes on every request.
    # This prevents cache misses caused by non-deterministic tool schema ordering.
    try:
        from tokenpak.proxy.tool_schema_registry import get_registry as _get_registry

        body, _schema_changed = _get_registry().normalize_request(body)
        if _schema_changed:
            sent_tokens = _estimate_tokens(body)
    except Exception as _exc:
        logger.debug("tool_schema_registry: normalization skipped (%s)", _exc)

    # Chain to base hook if present
    if base_hook:
        return base_hook(body, model, trace)

    return body, sent_tokens, raw_tokens, protected_tokens


def get_capsule_request_hook(
    base_hook: Optional[Callable] = None,
) -> Callable:
    """
    Get a request hook with capsule builder integration.

    Parameters
    ----------
    base_hook : callable, optional
        Another request hook to chain after capsule building.

    Returns
    -------
    callable
        Request hook suitable for ProxyServer.request_hook.
    """

    def hook(body: bytes, model: str, trace: Optional["PipelineTrace"] = None):
        return capsule_request_hook(body, model, trace, base_hook=base_hook)

    return hook


def _estimate_tokens(body: bytes) -> int:
    """Rough token estimate (chars / 4)."""
    try:
        import json

        data = json.loads(body)
        messages = data.get("messages", [])
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        total_chars += len(part.get("text", ""))
        return total_chars // 4
    except Exception:
        return len(body) // 4


def clear_cache() -> None:
    """Clear the cached feature flag state (for testing)."""
    global _CAPSULE_BUILDER_ENABLED
    _CAPSULE_BUILDER_ENABLED = None
