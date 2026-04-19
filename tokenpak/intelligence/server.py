"""
TokenPak Intelligence Server — FastAPI application.

Endpoints
─────────
GET  /health              — liveness probe (no auth)
GET  /v1/status           — authenticated status / tier info
POST /v1/compress         — compress a prompt payload
POST /v1/budget           — estimate token budget
POST /v1/license/validate — validate a license token (delegated to license_endpoint)

Security
────────
* X-TokenPak-Key header required on all /v1/* endpoints.
* Rate limits: free=20/min, pro=100/min, team=500/min, enterprise=unlimited.
* CORS configured (origins configurable via env TOKENPAK_CORS_ORIGINS).
* No PII in logs (PIIScrubFilter applied at tokenpak.intelligence logger).
* All POST bodies validated with Pydantic.
* Request-ID header on every response.

Run
───
::

    uvicorn tokenpak.intelligence.server:app --host 0.0.0.0 --port 9000

or programmatically::

    from tokenpak.intelligence.server import create_app
    app = create_app()
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, List, Optional

from fastapi import FastAPI, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from ..security import sanitize_model_name
from .auth import (
    APIKeyValidator,
    LicenseTier,
    RateLimiter,
    TokenPakAuthMiddleware,
)
from .license_endpoint import router as license_router

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# CORS
# ──────────────────────────────────────────────────────────────


def _cors_origins() -> List[str]:
    raw = os.environ.get("TOKENPAK_CORS_ORIGINS", "")
    if raw == "*":
        return ["*"]
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or [
        "http://localhost:3000",
        "http://localhost:8080",
        "https://tokenpak.ai",
    ]


# ──────────────────────────────────────────────────────────────
# Request / response schemas
# ──────────────────────────────────────────────────────────────


class CompressRequest(BaseModel):
    """Request body for POST /v1/compress."""

    content: str = Field(
        ...,
        min_length=1,
        max_length=500_000,
        description="Text content to compress / optimise for LLM context.",
    )
    model: str = Field(
        "gpt-4o",
        max_length=128,
        description="Target model identifier (affects tokenisation).",
    )
    budget_tokens: Optional[int] = Field(
        None,
        ge=1,
        le=1_000_000,
        description="Optional hard token budget for the output.",
    )
    mode: str = Field(
        "hybrid",
        description="Compression mode: strict | hybrid | aggressive.",
    )

    @field_validator("model")
    @classmethod
    def _valid_model(cls, v: str) -> str:
        return sanitize_model_name(v)

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, v: str) -> str:
        allowed = {"strict", "hybrid", "aggressive"}
        if v not in allowed:
            raise ValueError(f"mode must be one of {allowed}")
        return v


class CompressResponse(BaseModel):
    compressed: str
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    model: str
    request_id: str


class BudgetRequest(BaseModel):
    """Request body for POST /v1/budget."""

    content: str = Field(..., min_length=1, max_length=500_000)
    model: str = Field("gpt-4o", max_length=128)
    target_tokens: int = Field(8_000, ge=1, le=1_000_000)

    @field_validator("model")
    @classmethod
    def _valid_model(cls, v: str) -> str:
        return sanitize_model_name(v)


class BudgetResponse(BaseModel):
    estimated_tokens: int
    fits_in_budget: bool
    overage_tokens: int
    model: str
    request_id: str


class StatusResponse(BaseModel):
    status: str
    tier: str
    rate_limit_per_minute: Any  # int or "unlimited"
    server_time: str
    version: str


# ──────────────────────────────────────────────────────────────
# Token estimation helper (no hard dependency on tiktoken)
# ──────────────────────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Quick approximation: ~4 chars per token."""
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.encoding_for_model("gpt-4o")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


# ──────────────────────────────────────────────────────────────
# App factory
# ──────────────────────────────────────────────────────────────

_VERSION = "0.1.0"


def create_app(
    validator: Optional[APIKeyValidator] = None,
    limiter: Optional[RateLimiter] = None,
) -> FastAPI:
    """
    Create and configure the FastAPI intelligence server.

    Parameters
    ----------
    validator:
        Custom :class:`APIKeyValidator` (useful in tests).
    limiter:
        Custom :class:`RateLimiter` (useful in tests).
    """
    # Disable Swagger/ReDoc in production via TOKENPAK_DISABLE_DOCS=1
    _disable_docs = os.environ.get("TOKENPAK_DISABLE_DOCS", "0") == "1"
    app = FastAPI(
        title="TokenPak Intelligence Server",
        version=_VERSION,
        description="Compression, budgeting, and license validation API.",
        docs_url=None if _disable_docs else "/docs",
        redoc_url=None if _disable_docs else "/redoc",
    )

    # ── CORS ──────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["X-TokenPak-Key", "Content-Type", "X-Request-ID"],
        expose_headers=[
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
        ],
    )

    # ── Auth + rate-limit middleware ──────────────────────────
    _validator = validator or APIKeyValidator()
    _limiter = limiter or RateLimiter()
    app.add_middleware(
        TokenPakAuthMiddleware,
        validator=_validator,
        limiter=_limiter,
    )

    # ── Cost intelligence router ──────────────────────────────────
    from .cost_router import cost_router

    app.include_router(cost_router, prefix="/v1")

    # ── A/B Auto-Optimizer router ──────────────────────────────
    from .ab_router import ab_router

    app.include_router(ab_router, prefix="/v1")

    # ── License router ─────────────────────────────────────────
    app.include_router(license_router, prefix="/v1")

    # ──────────────────────────────────────────────────────────
    # Routes
    # ──────────────────────────────────────────────────────────

    @app.get("/health", tags=["system"])
    async def health(
        deep: bool = Query(default=False, description="Run deep component checks"),
    ) -> Any:
        """
        Health check — no auth required.

        - ``GET /health`` → fast liveness probe, always <10 ms.
        - ``GET /health?deep=true`` → full component check (providers, DB,
          index, memory, disk). Returns 200 for ok/degraded, 503 for error.
        """
        if not deep:
            # Fast path — liveness only
            return {"status": "ok", "version": _VERSION}

        # Deep path — run all checks
        from .deep_health import get_checker

        checker = get_checker()
        result = checker.run()
        response_body = {"version": _VERSION, **result.to_dict()}
        return JSONResponse(
            content=response_body,
            status_code=result.http_status,
        )

    @app.get(
        "/v1/status",
        response_model=StatusResponse,
        tags=["system"],
        summary="Authenticated status — returns tier + rate-limit info",
    )
    async def api_status(request: Request) -> StatusResponse:
        tier: LicenseTier = request.state.tier
        from .auth import TIER_RATE_LIMITS

        limit = TIER_RATE_LIMITS.get(tier)
        return StatusResponse(
            status="ok",
            tier=tier.value,
            rate_limit_per_minute=limit if limit is not None else "unlimited",
            server_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            version=_VERSION,
        )

    @app.post(
        "/v1/compress",
        response_model=CompressResponse,
        tags=["compression"],
        summary="Compress content for LLM context",
    )
    async def compress(body: CompressRequest, request: Request) -> CompressResponse:
        request_id: str = request.state.request_id
        logger.info("[%s] compress model=%s mode=%s", request_id, body.model, body.mode)

        original_tokens = _estimate_tokens(body.content)

        # Compression stub: in production this calls the full pipeline.
        # Ratios by mode: strict≈0.9, hybrid≈0.75, aggressive≈0.5
        ratios = {"strict": 0.90, "hybrid": 0.75, "aggressive": 0.50}
        ratio = ratios[body.mode]

        # Simple word-level drop for demo; real pipeline uses assembler.py
        words = body.content.split()
        keep = max(1, int(len(words) * ratio))
        compressed = " ".join(words[:keep])

        if body.budget_tokens is not None:
            # Trim further if needed
            while _estimate_tokens(compressed) > body.budget_tokens and len(compressed) > 1:
                compressed = compressed[: int(len(compressed) * 0.9)]

        compressed_tokens = _estimate_tokens(compressed)
        compression_ratio = round(1.0 - compressed_tokens / max(1, original_tokens), 4)

        return CompressResponse(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=compression_ratio,
            model=body.model,
            request_id=request_id,
        )

    @app.post(
        "/v1/budget",
        response_model=BudgetResponse,
        tags=["compression"],
        summary="Estimate token budget for content",
    )
    async def budget(body: BudgetRequest, request: Request) -> BudgetResponse:
        request_id: str = request.state.request_id
        logger.info("[%s] budget model=%s target=%d", request_id, body.model, body.target_tokens)

        estimated = _estimate_tokens(body.content)
        overage = max(0, estimated - body.target_tokens)

        return BudgetResponse(
            estimated_tokens=estimated,
            fits_in_budget=estimated <= body.target_tokens,
            overage_tokens=overage,
            model=body.model,
            request_id=request_id,
        )

    # ── Global exception handler ───────────────────────────────
    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.exception("[%s] Unhandled error: %s", request_id, type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "Internal Server Error",
                "request_id": request_id,
            },
        )

    return app


# Module-level app instance (used by uvicorn)
app = create_app()
