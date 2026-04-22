"""``Policy`` — per-request capability flags resolved from ``RouteClass``.

The Policy object is computed exactly once per request (by
:class:`tokenpak.services.policy_service.resolver.PolicyResolver`) and
threaded through every pipeline stage via
:attr:`tokenpak.services.request_pipeline.stages.PipelineContext.policy`.

Architectural rule: **stages branch on Policy fields, not on RouteClass
directly.** This keeps the classifier's knowledge in one place and lets
us retune a route's capability without touching stage code (edit the
YAML preset, not the Python).

The field set is deliberately typed + exhaustive — no untyped ``dict``
soup. When a field needs to grow (new capability), add it here and bump
the preset YAML format version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


BodyHandling = Literal["byte_preserve", "mutate"]
CacheOwnership = Literal["client", "proxy", "none"]
DLPMode = Literal["off", "warn", "redact", "block"]


@dataclass(slots=True, frozen=True)
class Policy:
    """Per-request capability flags.

    Immutable — resolved at the top of the pipeline and not rewritten.

    Fields
    ------
    body_handling:
        ``byte_preserve`` → the pipeline must not mutate request bytes
        (compression + enrichment disabled). Required for Claude Code
        OAuth routes where Anthropic's billing inspects the exact
        payload bytes.

        ``mutate`` → body is free to be compressed, enriched, rewritten
        by any stage (default for SDK / API routes).

    cache_ownership:
        ``client`` → the caller placed ``cache_control`` blocks. Cache
        hits credit the client/platform, never tokenpak. Proxy does not
        add its own cache_control markers.

        ``proxy`` → tokenpak owns the cache_control layer (prompt
        caching via deterministic breakpoints).

        ``none`` → no prompt caching applies.

    injection_enabled / injection_budget_chars / injection_min_query_tokens:
        Context enrichment from vault. Only runs when
        ``body_handling == 'mutate'`` AND ``injection_enabled``.
        Budget caps the size of what's spliced in; min_query_tokens is
        the relevance gate (don't enrich trivial prompts).

    dlp_mode:
        Controls the DLP outbound secret scan (security/dlp/).
        ``off`` skips the stage entirely.

    compression_eligible:
        Gates compression stage. Decoupled from body_handling because a
        route could be mutate-eligible for enrichment but opt out of
        wire-side compression.

    ttl_ordering_enforcement:
        Enforce Anthropic's ``1h-before-5m`` cache_control ordering rule
        on outbound bodies. True for Claude Code routes (clients emit
        interleaved orderings that Anthropic rejects with HTTP 400).

    profile:
        Human-readable preset name (e.g. ``"claude-code-tui"``) — used
        by telemetry rows + dashboard panels for grouping.

    capture_session_id_header:
        If set, the value of this request header becomes
        ``Request.metadata["session_id"]``. Claude Code emits
        ``x-claude-code-session-id``.
    """

    body_handling: BodyHandling = "mutate"
    cache_ownership: CacheOwnership = "none"
    injection_enabled: bool = False
    injection_budget_chars: int = 2000
    injection_min_query_tokens: int = 50
    dlp_mode: DLPMode = "warn"
    compression_eligible: bool = True
    ttl_ordering_enforcement: bool = False
    profile: str = "generic"
    capture_session_id_header: Optional[str] = None
    # Extra preset-level metadata that's not yet a typed field but
    # travels with the Policy (e.g. "tui_status_line": true). Kept as a
    # last-resort escape hatch. New capabilities should be promoted to
    # typed fields before two stages read the same extras key.
    extras: dict = field(default_factory=dict)


# Conservative defaults — used as the fallback for ``RouteClass.GENERIC``
# and as the base for preset merging. Nothing injection-y, nothing
# ttl-enforcement-y, body is mutable, cache is none.
DEFAULT_POLICY = Policy()


__all__ = ["Policy", "DEFAULT_POLICY", "BodyHandling", "CacheOwnership", "DLPMode"]
