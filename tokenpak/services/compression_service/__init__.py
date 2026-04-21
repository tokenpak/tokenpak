"""Compression-stage orchestration (Phase 2 stage wrapper).

Drives the compression primitive from inside the services pipeline.
Thin-wraps the existing compression/ subsystem once it exposes a public
API; pass-through (no-op) until then, per the D1 debt migration note in
Architecture §10. This is the pipeline-stage side of the work; the
compression algorithm itself is unchanged.

Public surface: ``Stage`` class, compatible with
``services.request_pipeline.stages.Stage``.

P2-02 acceptance: this module replaces the orchestration code that
currently lives scattered across ``proxy/``, ``agent/``, and elsewhere
in the v1.0.3 pre-migration tree. As code consolidates into the
canonical ``compression/`` subsystem per D1, this stage gains real
apply_request/apply_response logic.
"""

from __future__ import annotations

from ..request_pipeline.stages import PipelineContext


class Stage:
    """Compression pipeline stage.

    Pass-through today; gains logic as ``compression/`` gets a public
    API surface.
    """

    name = "compression"

    def apply_request(self, ctx: PipelineContext) -> None:
        """Run compression on ``ctx.request.body`` before dispatch.

        Phase 2 pass-through. Real implementation reads
        ``tokenpak.compression`` (once its ``__init__.py`` exports a
        public compressor) and compresses the body, setting
        ``ctx.extras['compression_ms']`` + ``ctx.extras['saved_tokens']``.
        """
        return None

    def apply_response(self, ctx: PipelineContext) -> None:
        """Emit compression-side telemetry on the way out.

        Phase 2 pass-through.
        """
        return None
