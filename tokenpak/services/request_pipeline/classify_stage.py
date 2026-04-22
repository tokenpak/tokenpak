"""``classify_stage`` — the first pipeline stage.

Runs before compression, security, cache, routing, telemetry. Calls the
classifier, resolves the policy, attaches both to ``PipelineContext``.
Downstream stages read ``ctx.policy.<flag>`` instead of inspecting raw
headers or bodies themselves.

Also: extracts the ``session_id`` header named by the policy
(e.g. ``x-claude-code-session-id``) and stamps it onto
``ctx.request.metadata["session_id"]`` so telemetry rows can group
per-session.

Per Architecture §1.3 invariant 1, this is the single authoritative
place route classification lives.
"""

from __future__ import annotations

from tokenpak.services.policy_service.resolver import get_resolver
from tokenpak.services.request_pipeline.stages import PipelineContext
from tokenpak.services.routing_service.classifier import get_classifier


class ClassifyStage:
    """Attach ``route_class`` + ``policy`` to the pipeline context."""

    name = "classify"

    def apply_request(self, ctx: PipelineContext) -> None:
        # Idempotent — safe to call twice (second call is a no-op).
        if ctx.route_class is not None and ctx.policy is not None:
            return

        rc = get_classifier().classify(ctx.request)
        policy = get_resolver().resolve(rc)
        ctx.route_class = rc
        ctx.policy = policy

        # If the policy names a session-id header, capture it onto the
        # request metadata so later stages (telemetry, per-session
        # grouping) can find it in one canonical place.
        hdr = policy.capture_session_id_header
        if hdr:
            headers = ctx.request.headers or {}
            # Case-insensitive lookup — HTTP headers are not ordered.
            value = None
            for k, v in headers.items():
                if k.lower() == hdr.lower():
                    value = v
                    break
            if value:
                # Don't overwrite an explicit caller-supplied session id.
                ctx.request.metadata.setdefault("session_id", value)

        # Stamp a lightweight telemetry breadcrumb that later stages +
        # /stats can read without re-classifying.
        ctx.stage_telemetry["classify"] = {
            "route_class": rc.value,
            "profile": policy.profile,
        }

    def apply_response(self, ctx: PipelineContext) -> None:
        # Classification is a request-side concern; nothing to do on
        # the way out.
        return


__all__ = ["ClassifyStage"]
