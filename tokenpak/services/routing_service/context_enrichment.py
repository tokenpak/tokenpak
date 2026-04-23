"""``ContextEnrichmentStage`` — policy-gated vault injection.

Runs in the ``routing`` slot of the pipeline. Adds retrieved vault
context to the request's ``system`` array as a separate, non-cached
block BEFORE the cache boundary, so the stable prefix upstream of it
stays hot across requests.

Gate: only runs when
  - ``ctx.policy.injection_enabled`` is True, AND
  - ``ctx.policy.body_handling == "mutate"`` (byte_preserve routes are
    forbidden from body mutation — their enrichment happens client-side
    via MCP tools the model calls explicitly, not here).

Budget: injected content is truncated to
``ctx.policy.injection_budget_chars`` to stop runaway context on prompts
that would otherwise blow past the request token ceiling.

Relevance gate: trivial prompts (below
``ctx.policy.injection_min_query_tokens``) are skipped — injecting
context for a one-word "hi" wastes tokens and pollutes the cache.

This Stage is the single vault-injection path. The old
``proxy/vault_bridge.py`` byte-splice helper is referenced for behavior
only; it is not restored.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tokenpak.services.request_pipeline.stages import PipelineContext

logger = logging.getLogger(__name__)


class ContextEnrichmentStage:
    """Pipeline Stage — enrich requests with vault context when policy allows."""

    name = "routing"

    def __init__(self, retriever: Any | None = None) -> None:
        """``retriever`` is a callable that accepts ``(query: str, top_k: int)``
        and returns an iterable of strings. Kept as a dependency so the
        Stage can be tested without a live vault index and so alternate
        retrievers (e.g. in-memory for integration tests) plug in.
        """
        self._retriever = retriever

    def _build_retriever(self) -> Any | None:
        """Lazily construct the default vault retriever.

        Returns None if the vault subsystem isn't importable or no index
        exists — the Stage becomes a no-op in that case, which is the
        correct behavior pre-vault-init and in minimal test environments.
        """
        if self._retriever is not None:
            return self._retriever
        try:
            from tokenpak.vault.blocks import BlockStore
        except Exception:  # noqa: BLE001
            return None
        try:
            store = BlockStore.default()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return None

        def _search(query: str, top_k: int) -> list[str]:
            try:
                results = store.search(query, top_k=top_k)
            except Exception:  # noqa: BLE001
                return []
            out: list[str] = []
            for r in results:
                text = getattr(r, "text", None) or getattr(r, "content", None)
                if text:
                    out.append(str(text))
            return out

        self._retriever = _search
        return self._retriever

    def _extract_query_text(self, body_bytes: bytes) -> str | None:
        """Pull the last user message from an Anthropic Messages body.

        Returns None if the body can't be parsed or there's no user
        message. This is the only place we JSON-decode the body for
        enrichment — keeping the decode localized to one function.
        """
        if not body_bytes:
            return None
        try:
            data = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        messages = data.get("messages")
        if not isinstance(messages, list):
            return None
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Concatenate text blocks.
                parts: list[str] = []
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        t = blk.get("text")
                        if isinstance(t, str):
                            parts.append(t)
                if parts:
                    return "\n".join(parts)
        return None

    def _inject_into_system(
        self,
        body_bytes: bytes,
        injected_text: str,
    ) -> bytes:
        """Splice a new volatile text block into the ``system`` array.

        Keeps ``system`` as a list (upgrading the string form if
        necessary). Inserts the injected block WITHOUT a cache_control
        marker — by contract the cache boundary sits on a stable
        sibling, not on volatile content.

        Re-serializes the JSON compactly. Loses whitespace-level byte
        identity — callers that need byte preservation must NOT hit
        this function (the Stage's Policy gate guarantees that).
        """
        try:
            data = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body_bytes
        system = data.get("system")
        new_block = {"type": "text", "text": injected_text}
        if isinstance(system, str):
            data["system"] = [{"type": "text", "text": system}, new_block]
        elif isinstance(system, list):
            data["system"] = list(system) + [new_block]
        else:
            data["system"] = [new_block]
        try:
            return json.dumps(data, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            return body_bytes

    def apply_request(self, ctx: PipelineContext) -> None:
        policy = ctx.policy
        if policy is None or not policy.injection_enabled:
            return
        if policy.body_handling != "mutate":
            # Byte-preserve routes: MCP-tool path is the only enrichment
            # surface. Record the skip so telemetry sees it wasn't a bug.
            ctx.stage_telemetry.setdefault("routing", {})[
                "enrichment_skipped"
            ] = "byte_preserve"
            return

        body = ctx.request.body or b""
        query = self._extract_query_text(body)
        if not query:
            return

        # Relevance gate. Token estimate uses the same //4 heuristic as
        # the companion hook — good enough to reject trivial prompts.
        if len(query) // 4 < policy.injection_min_query_tokens:
            ctx.stage_telemetry.setdefault("routing", {})[
                "enrichment_skipped"
            ] = "below_min_query_tokens"
            return

        retriever = self._build_retriever()
        if retriever is None:
            ctx.stage_telemetry.setdefault("routing", {})[
                "enrichment_skipped"
            ] = "no_retriever"
            return

        try:
            hits = retriever(query, 5)
        except Exception as exc:  # noqa: BLE001
            logger.warning("context_enrichment: retriever failed: %s", exc)
            return

        if not hits:
            return

        # Concatenate hits up to the budget in characters.
        budget = policy.injection_budget_chars
        pieces: list[str] = []
        used = 0
        for h in hits:
            if not h:
                continue
            # Reserve ~20 chars for the separator/header.
            remaining = budget - used - 20
            if remaining <= 0:
                break
            snippet = h if len(h) <= remaining else h[:remaining]
            pieces.append(snippet)
            used += len(snippet) + 20
        if not pieces:
            return
        injected = "[tokenpak vault context]\n" + "\n---\n".join(pieces)

        new_body = self._inject_into_system(body, injected)
        if new_body is body:
            return
        ctx.request.body = new_body
        ctx.stage_telemetry.setdefault("routing", {}).update({
            "enrichment_applied": True,
            "injected_chars": len(injected),
            "injected_hits": len(pieces),
        })

    def apply_response(self, ctx: PipelineContext) -> None:
        return


__all__ = ["ContextEnrichmentStage"]
