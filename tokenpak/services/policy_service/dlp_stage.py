"""``DLPStage`` — policy-gated outbound secret scan.

Runs in the ``security`` slot of the canonical pipeline
(:data:`tokenpak.services.request_pipeline.stages.CANONICAL_STAGES`).
Reads :attr:`tokenpak.core.routing.policy.Policy.dlp_mode` from
``ctx.policy`` to decide:

- ``off``    — no-op
- ``warn``   — scan + log, forward unchanged
- ``redact`` — scan + rewrite body
- ``block``  — scan + short-circuit pipeline with a blocked outcome

Byte-preserve routes (e.g. ``claude-code-*``) can still use
``warn`` and ``block`` modes because those don't mutate the body.
They MUST NOT use ``redact`` — doing so would break the Anthropic
OAuth billing contract. The Stage downgrades ``redact`` to ``warn``
automatically on byte_preserve routes and records the override in
stage telemetry.
"""

from __future__ import annotations

import logging

from tokenpak.security.dlp import DLPScanner, apply_mode
from tokenpak.services.request_pipeline.stages import PipelineContext


logger = logging.getLogger(__name__)


class DLPStage:
    """Pipeline Stage — runs DLP per the active Policy."""

    name = "security"

    def __init__(self, scanner: DLPScanner | None = None) -> None:
        self._scanner = scanner or DLPScanner()

    def apply_request(self, ctx: PipelineContext) -> None:
        policy = ctx.policy
        if policy is None:
            # classify_stage must run first; if it didn't, be conservative
            # and skip rather than scan with unknown rules.
            return

        mode = policy.dlp_mode
        if mode == "off":
            return

        # Byte-preserve routes cannot accept body rewrites. Downgrade
        # 'redact' to 'warn' so the operator still sees findings.
        effective_mode = mode
        if policy.body_handling == "byte_preserve" and mode == "redact":
            effective_mode = "warn"
            ctx.stage_telemetry.setdefault("security", {})[
                "dlp_mode_downgraded"
            ] = f"{mode}->warn (byte_preserve)"

        body = ctx.request.body or b""
        findings = self._scanner.scan_bytes(body)

        outcome = apply_mode(effective_mode, body, findings)

        ctx.stage_telemetry.setdefault("security", {}).update({
            "dlp_mode": outcome.mode,
            "findings_count": len(outcome.findings),
            "blocked": outcome.blocked,
            "rules_triggered": sorted({f.rule_id for f in outcome.findings}),
        })

        if outcome.new_body is not body:
            ctx.request.body = outcome.new_body

        if outcome.blocked:
            ctx.short_circuit = True
            # Callers read .response; build a minimal error response
            # describing the block. Response shape is provider-agnostic
            # — translation to provider JSON happens in the entrypoint.
            from tokenpak.services.response import Response  # local import

            body_text = (
                "tokenpak.dlp: outbound request blocked — "
                + str(len(outcome.findings))
                + " secret(s) detected ("
                + ", ".join(sorted({f.rule_id for f in outcome.findings}))
                + ")"
            )
            try:
                ctx.response = Response(
                    status=400,
                    headers={"content-type": "text/plain"},
                    body=body_text.encode("utf-8"),
                )
            except TypeError:
                # Response dataclass may have a slightly different
                # constructor signature in older Phase 2 scaffolds;
                # ctx.short_circuit alone is still respected by the
                # pipeline dispatcher.
                ctx.extras["dlp_block_reason"] = body_text

    def apply_response(self, ctx: PipelineContext) -> None:
        # DLP is a request-side concern; no response-side action.
        return


__all__ = ["DLPStage"]
