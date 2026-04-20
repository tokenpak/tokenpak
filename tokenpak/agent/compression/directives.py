"""

directives.py — Parse and apply server-returned compression directives.

Directive format (from Intelligence Server):
{
    "request_id": "abc123",
    "compression": [
        {"target": "segment_N", "action": "prune|collapse|dedup|reorder|prune_turns", "params": {...}},
    ],
    "model_route": {"recommended": "haiku", "confidence": 0.91, "fallback": "sonnet"},
    "context_plan": {"block_priority": [8,3,11], "drop_blocks": [6,9]},
    "pattern_hints": {"keep_last": 8, "preserve_code": true},
    "agent_dedup": {"skip_blocks": [2,5]},
    "estimated_savings": {"tokens": 955, "pct": 33.5}
}
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Segment = dict[str, Any]
VaultBlock = dict[str, Any]


# ---------------------------------------------------------------------------
# Directive Cache (5-minute TTL)
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 300  # 5 minutes


class DirectiveCache:
    """In-process cache for server directive responses with 5-minute TTL."""

    def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[dict, float]] = {}

    @staticmethod
    def _make_key(raw: dict) -> str:
        serialised = json.dumps(raw, sort_keys=True, default=str)
        return hashlib.sha256(serialised.encode()).hexdigest()[:16]

    def _is_expired(self, expires_at: float) -> bool:
        return time.monotonic() > expires_at

    def get(self, raw: dict) -> "dict | None":
        key = self._make_key(raw)
        entry = self._store.get(key)
        if entry is None:
            return None
        parsed, expires_at = entry
        if self._is_expired(expires_at):
            del self._store[key]
            return None
        return parsed

    def set(self, raw: dict, parsed: dict) -> None:
        key = self._make_key(raw)
        self._store[key] = (parsed, time.monotonic() + self._ttl)

    def invalidate(self, raw: dict) -> bool:
        key = self._make_key(raw)
        return self._store.pop(key, None) is not None

    def clear(self) -> None:
        self._store.clear()

    def purge_expired(self) -> int:
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]
        return len(expired)

    @property
    def size(self) -> int:
        return len(self._store)


# Module-level cache instance (shared across calls in same process)
_directive_cache: DirectiveCache = DirectiveCache()


@dataclass
class DirectiveResult:
    """Result returned after applying all directives."""

    segments: list[Segment] = field(default_factory=list)
    vault_blocks: list[VaultBlock] = field(default_factory=list)
    model_route: dict[str, Any] = field(default_factory=dict)
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    tokens_saved: int = 0
    estimated_savings: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

KNOWN_ACTIONS = {
    "prune",
    "collapse",
    "dedup",
    "reorder",
    "prune_turns",
    "compression_mode_change",
    "recipe_override",
    "budget_adjustment",
}


def parse_directives(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise a raw directive payload. Strips malformed entries."""
    if not isinstance(raw, dict):
        logger.warning("directives: expected dict, got %s — ignoring", type(raw).__name__)
        return {}

    cleaned: dict[str, Any] = {}

    compression = raw.get("compression")
    valid_compression: list[dict] = []
    if compression is not None:
        if not isinstance(compression, list):
            logger.warning("directives: 'compression' must be a list — skipping")
        else:
            for entry in compression:
                if not isinstance(entry, dict):
                    logger.warning(
                        "directives: compression entry is not a dict — skipping: %r", entry
                    )
                    continue
                action = entry.get("action")
                target = entry.get("target")
                if not action or not target:
                    logger.warning(
                        "directives: compression entry missing action/target — skipping: %r", entry
                    )
                    continue
                if action not in KNOWN_ACTIONS:
                    logger.warning("directives: unknown action %r — skipping", action)
                    continue
                valid_compression.append(entry)
        cleaned["compression"] = valid_compression

    model_route = raw.get("model_route")
    if model_route is not None:
        if isinstance(model_route, dict) and "recommended" in model_route:
            cleaned["model_route"] = model_route
        else:
            logger.warning("directives: invalid model_route — skipping")

    context_plan = raw.get("context_plan")
    if context_plan is not None:
        if isinstance(context_plan, dict):
            cleaned["context_plan"] = context_plan
        else:
            logger.warning("directives: invalid context_plan — skipping")

    pattern_hints = raw.get("pattern_hints")
    if isinstance(pattern_hints, dict):
        cleaned["pattern_hints"] = pattern_hints

    agent_dedup = raw.get("agent_dedup")
    if isinstance(agent_dedup, dict):
        cleaned["agent_dedup"] = agent_dedup

    savings = raw.get("estimated_savings")
    if isinstance(savings, dict):
        cleaned["estimated_savings"] = savings

    for key in ("request_id", "budget"):
        if key in raw:
            cleaned[key] = raw[key]

    return cleaned


# ---------------------------------------------------------------------------
# Segment-level compression actions
# ---------------------------------------------------------------------------


def _target_index(target: str):
    m = re.fullmatch(r"segment_(\d+)", target)
    return int(m.group(1)) if m else None


def _apply_prune(segment: Segment, params: dict) -> Segment:
    keep_ratio = float(params.get("keep_ratio", 0.7))
    content = segment.get("content", "")
    lines = content.splitlines()
    keep_n = max(1, int(len(lines) * keep_ratio))
    pruned = "\n".join(lines[:keep_n])
    tokens_before = segment.get("tokens", len(content.split()))
    tokens_after = max(1, int(tokens_before * keep_ratio))
    return {**segment, "content": pruned, "tokens": tokens_after, "_compressed": "prune"}


def _apply_collapse(segment: Segment, params: dict) -> Segment:
    keep_signature = bool(params.get("keep_signature", True))
    content = segment.get("content", "")
    if keep_signature:
        lines = [l for l in content.splitlines() if l.strip()]
        collapsed = lines[0] + "\n    ..." if lines else "..."
    else:
        collapsed = "..."
    tokens_after = max(1, len(collapsed.split()))
    return {**segment, "content": collapsed, "tokens": tokens_after, "_compressed": "collapse"}


def _apply_dedup(segment: Segment, params: dict) -> Segment:
    content = segment.get("content", "")
    seen: set[str] = set()
    unique_lines = []
    for line in content.splitlines():
        key = line.strip()
        if key not in seen:
            seen.add(key)
            unique_lines.append(line)
    deduped = "\n".join(unique_lines)
    tokens_after = max(1, len(deduped.split()))
    return {**segment, "content": deduped, "tokens": tokens_after, "_compressed": "dedup"}


def _apply_reorder(segment: Segment, params: dict) -> Segment:
    order = params.get("order")
    content = segment.get("content", "")
    lines = content.splitlines()
    if order and isinstance(order, list):
        reordered = [lines[i] for i in order if 0 <= i < len(lines)]
        referenced = set(order)
        for i, line in enumerate(lines):
            if i not in referenced:
                reordered.append(line)
        content = "\n".join(reordered)
    return {**segment, "content": content, "_compressed": "reorder"}


def _apply_prune_turns(segment: Segment, params: dict) -> Segment:
    remove_indices: list[int] = params.get("remove", [])
    if not remove_indices:
        return segment
    turns: list[Any] = segment.get("turns", [])
    if turns:
        kept = [t for i, t in enumerate(turns) if i not in remove_indices]
        content_parts = []
        for t in kept:
            role = t.get("role", "")
            msg = t.get("content", "")
            content_parts.append(f"{role}: {msg}" if role else msg)
        content = "\n".join(content_parts)
        tokens_after = max(1, len(content.split()))
        return {
            **segment,
            "content": content,
            "turns": kept,
            "tokens": tokens_after,
            "_compressed": "prune_turns",
        }
    else:
        raw_turns = segment.get("content", "").split("\n\n")
        kept = [t for i, t in enumerate(raw_turns) if i not in remove_indices]
        content = "\n\n".join(kept)
        tokens_after = max(1, len(content.split()))
        return {**segment, "content": content, "tokens": tokens_after, "_compressed": "prune_turns"}


def _apply_compression_mode_change(segment: Segment, params: dict) -> Segment:
    """Switch compression mode (e.g. aggressive, conservative, lossless)."""
    mode = params.get("mode", "aggressive")
    valid_modes = {"aggressive", "conservative", "lossless", "summarize"}
    if mode not in valid_modes:
        logger.warning("directives: unknown compression_mode %r — using aggressive", mode)
        mode = "aggressive"

    content = segment.get("content", "")
    tokens_before = segment.get("tokens", len(content.split()))

    if mode == "lossless":
        # No content change; mode is recorded only
        return {**segment, "_compression_mode": mode, "_compressed": "compression_mode_change"}
    elif mode == "summarize":
        lines = content.splitlines()
        summary = lines[0] if lines else ""
        if len(lines) > 1:
            summary += f"\n[…{len(lines) - 1} lines omitted]"
        tokens_after = max(1, len(summary.split()))
        return {
            **segment,
            "content": summary,
            "tokens": tokens_after,
            "_compression_mode": mode,
            "_compressed": "compression_mode_change",
        }
    elif mode == "conservative":
        keep_ratio = float(params.get("keep_ratio", 0.85))
        lines = content.splitlines()
        keep_n = max(1, int(len(lines) * keep_ratio))
        pruned = "\n".join(lines[:keep_n])
        tokens_after = max(1, int(tokens_before * keep_ratio))
        return {
            **segment,
            "content": pruned,
            "tokens": tokens_after,
            "_compression_mode": mode,
            "_compressed": "compression_mode_change",
        }
    else:  # aggressive
        keep_ratio = float(params.get("keep_ratio", 0.5))
        lines = content.splitlines()
        keep_n = max(1, int(len(lines) * keep_ratio))
        pruned = "\n".join(lines[:keep_n])
        tokens_after = max(1, int(tokens_before * keep_ratio))
        return {
            **segment,
            "content": pruned,
            "tokens": tokens_after,
            "_compression_mode": mode,
            "_compressed": "compression_mode_change",
        }


def _apply_recipe_override(segment: Segment, params: dict) -> Segment:
    """Override local recipe settings with server-provided recipe fields."""
    recipe_name = params.get("recipe_name", "")
    max_tokens = params.get("max_tokens")
    priority_order = params.get("priority_order")
    required_blocks = params.get("required_blocks")

    overrides: dict[str, Any] = {"_compressed": "recipe_override"}
    if recipe_name:
        overrides["_recipe_name"] = recipe_name
    if max_tokens is not None:
        overrides["max_tokens"] = int(max_tokens)
    if priority_order is not None:
        overrides["_priority_order"] = priority_order
    if required_blocks is not None:
        overrides["_required_blocks"] = required_blocks

    return {**segment, **overrides}


def _apply_budget_adjustment(segment: Segment, params: dict) -> Segment:
    """Adjust token budget for this segment per server directive."""
    new_budget = params.get("max_tokens")
    reduction_pct = params.get("reduction_pct")

    content = segment.get("content", "")
    tokens = segment.get("tokens", len(content.split()))

    if new_budget is not None:
        new_budget = int(new_budget)
        if tokens > new_budget:
            # Trim content to fit within new budget (rough char estimate)
            ratio = new_budget / max(tokens, 1)
            lines = content.splitlines()
            keep_n = max(1, int(len(lines) * ratio))
            content = "\n".join(lines[:keep_n])
            tokens = new_budget
        return {
            **segment,
            "content": content,
            "tokens": tokens,
            "_budget_cap": new_budget,
            "_compressed": "budget_adjustment",
        }

    elif reduction_pct is not None:
        reduction_pct = max(0.0, min(1.0, float(reduction_pct)))
        new_tokens = max(1, int(tokens * (1.0 - reduction_pct)))
        ratio = new_tokens / max(tokens, 1)
        lines = content.splitlines()
        keep_n = max(1, int(len(lines) * ratio))
        content = "\n".join(lines[:keep_n])
        return {
            **segment,
            "content": content,
            "tokens": new_tokens,
            "_budget_reduction": reduction_pct,
            "_compressed": "budget_adjustment",
        }

    # No change if no parameters
    return {**segment, "_compressed": "budget_adjustment"}


_ACTION_HANDLERS = {
    "prune": _apply_prune,
    "collapse": _apply_collapse,
    "dedup": _apply_dedup,
    "reorder": _apply_reorder,
    "prune_turns": _apply_prune_turns,
    "compression_mode_change": _apply_compression_mode_change,
    "recipe_override": _apply_recipe_override,
    "budget_adjustment": _apply_budget_adjustment,
}


def apply_compression_directives(
    segments: list[Segment],
    directives: list[dict],
) -> tuple[list[Segment], list[str], list[str]]:
    """Apply compression directives to segments. Returns (updated_segments, applied, skipped)."""
    seg_map: dict[str, int] = {}
    for i, seg in enumerate(segments):
        sid = seg.get("id", f"segment_{i}")
        seg_map[sid] = i

    updated = list(segments)
    applied: list[str] = []
    skipped: list[str] = []

    for directive in directives:
        target = directive.get("target", "")
        action = directive.get("action", "")
        params = directive.get("params", {}) or {}
        label = f"{action}({target})"

        idx = seg_map.get(target)
        if idx is None:
            n = _target_index(target)
            if n is not None and 0 <= n < len(updated):
                idx = n
            else:
                logger.warning("directives: target %r not found — skipping", target)
                skipped.append(label)
                continue

        handler = _ACTION_HANDLERS.get(action)
        if handler is None:
            logger.warning("directives: no handler for action %r — skipping", action)
            skipped.append(label)
            continue

        try:
            updated[idx] = handler(updated[idx], params)
            applied.append(label)
            logger.debug("directives: applied %s to index %d", label, idx)
        except Exception as exc:
            logger.warning("directives: error applying %s — %s", label, exc)
            skipped.append(label)

    return updated, applied, skipped


# ---------------------------------------------------------------------------
# Context plan
# ---------------------------------------------------------------------------


def apply_context_plan(
    vault_blocks: list[VaultBlock], context_plan: dict[str, Any]
) -> list[VaultBlock]:
    """Reorder and drop vault blocks per context_plan directive."""
    if not context_plan:
        return vault_blocks

    drop_set: set[Any] = set(context_plan.get("drop_blocks", []))
    priority_order: list[Any] = context_plan.get("block_priority", [])

    remaining = [b for b in vault_blocks if b.get("id") not in drop_set]

    if priority_order:
        by_id = {b.get("id"): b for b in remaining}
        ordered: list[VaultBlock] = []
        seen_ids: set[Any] = set()
        for bid in priority_order:
            if bid in by_id:
                ordered.append(by_id[bid])
                seen_ids.add(bid)
        for b in remaining:
            if b.get("id") not in seen_ids:
                ordered.append(b)
        return ordered

    return remaining


def extract_model_route(directives: dict[str, Any]) -> dict[str, Any]:
    """Return model routing recommendation from directives, or empty dict."""
    return directives.get("model_route", {})


def apply_agent_dedup(
    vault_blocks: list[VaultBlock], agent_dedup: dict[str, Any]
) -> list[VaultBlock]:
    """Skip vault blocks flagged by agent_dedup.skip_blocks."""
    skip_set: set[Any] = set(agent_dedup.get("skip_blocks", []))
    if not skip_set:
        return vault_blocks
    return [b for b in vault_blocks if b.get("id") not in skip_set]


# ---------------------------------------------------------------------------
# Top-level pipeline entrypoint
# ---------------------------------------------------------------------------


def apply_directives(
    segments: list[Segment],
    vault_blocks: list[VaultBlock],
    raw_directives: dict[str, Any],
    local_recipe_fn=None,
    cache: "DirectiveCache | None" = None,
) -> DirectiveResult:
    """
    Apply all server directives to segments and vault blocks.

    Server directives take priority over local recipe when present.
    Falls back to local_recipe_fn if no server directives are available.

    Parameters
    ----------
    cache:
        Optional DirectiveCache instance. Uses module-level shared cache by default.
        Pass a fresh DirectiveCache() to opt out of caching for a single call.
    """
    if cache is None:
        cache = _directive_cache

    if raw_directives:
        cached = cache.get(raw_directives)
        if cached is not None:
            logger.debug("directives: cache hit — skipping parse")
            directives = cached
        else:
            directives = parse_directives(raw_directives)
            cache.set(raw_directives, directives)
    else:
        directives = {}

    has_server_directives = bool(
        directives.get("compression")
        or directives.get("context_plan")
        or directives.get("model_route")
        or directives.get("agent_dedup")
    )

    if not has_server_directives and local_recipe_fn is not None:
        logger.debug("directives: no server directives — falling back to local recipe")
        return local_recipe_fn(segments, vault_blocks)

    result = DirectiveResult()
    result.estimated_savings = directives.get("estimated_savings", {})

    # 1. Compression directives on segments
    compression_list = directives.get("compression", [])
    tokens_before = sum(s.get("tokens", 0) for s in segments)
    updated_segments, applied, skipped = apply_compression_directives(segments, compression_list)
    tokens_after = sum(s.get("tokens", 0) for s in updated_segments)
    result.segments = updated_segments
    result.applied.extend(applied)
    result.skipped.extend(skipped)
    result.tokens_saved += max(0, tokens_before - tokens_after)

    # 2. Agent dedup on vault blocks
    agent_dedup = directives.get("agent_dedup", {})
    remaining_blocks = apply_agent_dedup(vault_blocks, agent_dedup)
    if agent_dedup:
        result.applied.append(f"agent_dedup(skip={len(agent_dedup.get('skip_blocks', []))})")

    # 3. Context plan on vault blocks
    context_plan = directives.get("context_plan", {})
    result.vault_blocks = apply_context_plan(remaining_blocks, context_plan)
    if context_plan:
        result.applied.append(
            f"context_plan(priority={context_plan.get('block_priority', [])}, "
            f"drop={context_plan.get('drop_blocks', [])})"
        )

    # 4. Model route
    result.model_route = extract_model_route(directives)
    if result.model_route:
        result.applied.append(f"model_route({result.model_route.get('recommended')})")

    return result


# ---------------------------------------------------------------------------
# OSS Backward-Compatibility Stub
# ---------------------------------------------------------------------------

from typing import Any, Dict, List, Optional


class DirectiveApplier:
    """
    Apply compression directives to a messages list.

    OSS stub — passes messages through unmodified.
    Pro integration: replaces with rule-based directive engine.

    Parameters
    ----------
    directives : list[dict], optional
        List of directive dicts (Pro feature; ignored in OSS).
    """

    def __init__(self, directives: Optional[List[Dict[str, Any]]] = None) -> None:
        self._directives: List[Dict[str, Any]] = directives or []

    def apply(
        self,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Apply registered directives to messages.

        Parameters
        ----------
        messages : list[dict]
            Messages to process.

        Returns
        -------
        list[dict]
            Transformed messages (pass-through in OSS build).
        """
        # OSS: no directives applied
        return messages

    def add_directive(self, directive: Dict[str, Any]) -> None:
        """Register a directive (Pro feature placeholder)."""
        self._directives.append(directive)

    def clear(self) -> None:
        """Remove all registered directives."""
        self._directives.clear()

    @property
    def directive_count(self) -> int:
        return len(self._directives)
