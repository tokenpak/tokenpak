"""
A/B Auto-Optimizer API router — Pro+ feature.

Routes
──────
POST /v1/ab/experiments               — create a new experiment
GET  /v1/ab/experiments               — list experiments (filter: active|completed|failed|cancelled)
GET  /v1/ab/experiments/{id}          — get experiment details
POST /v1/ab/experiments/{id}/report   — record an observation for a variant
GET  /v1/ab/experiments/{id}/results  — get significance results
POST /v1/ab/experiments/{id}/promote  — manual winner override
POST /v1/ab/experiments/{id}/cancel   — cancel experiment

All routes require Pro+ license (tier ≠ free).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .ab_optimizer import ABOptimizerStore, ExperimentStatus
from .auth import LicenseTier

ab_router = APIRouter(tags=["ab-optimizer"])

# ---------------------------------------------------------------------------
# Shared store (singleton per process)
# ---------------------------------------------------------------------------

_DB_PATH = os.environ.get("TOKENPAK_AB_DB", "")


def _get_store() -> ABOptimizerStore:
    if not hasattr(_get_store, "_instance"):
        _get_store._instance = ABOptimizerStore(_DB_PATH or ABOptimizerStore.__init__.__defaults__)  # type: ignore[attr-defined, arg-type]
    return _get_store._instance  # type: ignore[attr-defined]


def _store() -> ABOptimizerStore:
    if not hasattr(ab_router, "_store_instance"):
        from .ab_optimizer import DEFAULT_DB_PATH

        db = _DB_PATH or str(DEFAULT_DB_PATH)
        ab_router._store_instance = ABOptimizerStore(db)  # type: ignore[attr-defined]
    return ab_router._store_instance  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Pro+ guard
# ---------------------------------------------------------------------------

_PRO_TIERS = {LicenseTier.PRO, LicenseTier.TEAM, LicenseTier.ENTERPRISE}


def _require_pro(request: Request) -> Optional[JSONResponse]:
    tier: LicenseTier = getattr(request.state, "tier", LicenseTier.FREE)
    if tier not in _PRO_TIERS:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "error": "Forbidden",
                "detail": (
                    "A/B Auto-Optimizer is a Pro+ feature. Upgrade at https://tokenpak.ai/pricing"
                ),
            },
        )
    return None


def _err(code: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=code, content={"error": msg})


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CreateExperimentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=1000)
    control_name: str = Field("control", min_length=1, max_length=100)
    treatment_name: str = Field("treatment", min_length=1, max_length=100)
    tags: List[str] = Field(default_factory=list)

    @field_validator("control_name", "treatment_name")
    @classmethod
    def no_spaces(cls, v: str) -> str:
        if " " in v:
            raise ValueError("Variant names cannot contain spaces")
        return v


class ReportObservationRequest(BaseModel):
    variant: str = Field(..., min_length=1, max_length=100)
    token_savings: float = Field(..., ge=0.0, le=1.0, description="Fraction of tokens saved (0-1)")
    quality_score: float = Field(..., ge=0.0, le=1.0, description="Quality score (0-1)")
    latency_ms: float = Field(..., ge=0.0, description="Latency in milliseconds")


class PromoteRequest(BaseModel):
    variant: str = Field(..., min_length=1, max_length=100)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@ab_router.post("/ab/experiments", status_code=status.HTTP_201_CREATED)
async def create_experiment(
    body: CreateExperimentRequest,
    request: Request,
) -> JSONResponse:
    """Create a new A/B experiment."""
    if guard := _require_pro(request):
        return guard
    try:
        exp = _store().create_experiment(
            name=body.name,
            description=body.description,
            control_name=body.control_name,
            treatment_name=body.treatment_name,
            tags=body.tags,
        )
        return JSONResponse(status_code=201, content=exp.to_dict())
    except Exception as exc:
        return _err(400, str(exc))


@ab_router.get("/ab/experiments")
async def list_experiments(
    request: Request,
    filter: Optional[str] = Query(
        None,
        description="Filter by status: active|completed|failed|cancelled",
    ),
) -> JSONResponse:
    """List all experiments, optionally filtered by status."""
    if guard := _require_pro(request):
        return guard
    valid = {s.value for s in ExperimentStatus}
    if filter and filter not in valid:
        return _err(400, f"Invalid filter '{filter}'. Choose from: {', '.join(sorted(valid))}")
    experiments = _store().list_experiments(status_filter=filter)
    return JSONResponse(
        content={
            "experiments": [e.to_dict() for e in experiments],
            "count": len(experiments),
            "filter": filter,
        }
    )


@ab_router.get("/ab/experiments/{exp_id}")
async def get_experiment(exp_id: str, request: Request) -> JSONResponse:
    """Get experiment details."""
    if guard := _require_pro(request):
        return guard
    exp = _store().get_experiment(exp_id)
    if not exp:
        return _err(404, f"Experiment {exp_id!r} not found")
    return JSONResponse(content=exp.to_dict())


@ab_router.post("/ab/experiments/{exp_id}/report")
async def report_observation(
    exp_id: str,
    body: ReportObservationRequest,
    request: Request,
) -> JSONResponse:
    """Record one observation for a variant. Returns significance result when available."""
    if guard := _require_pro(request):
        return guard
    try:
        sig = _store().record_observation(
            exp_id=exp_id,
            variant=body.variant,
            token_savings=body.token_savings,
            quality_score=body.quality_score,
            latency_ms=body.latency_ms,
        )
        resp: Dict[str, Any] = {"recorded": True, "experiment_id": exp_id}
        if sig is not None:
            resp["significance"] = sig.to_dict()
            if sig.significant and sig.winner:
                resp["auto_promoted"] = sig.winner
        return JSONResponse(content=resp)
    except ValueError as exc:
        return _err(400, str(exc))
    except Exception as exc:
        return _err(500, str(exc))


@ab_router.get("/ab/experiments/{exp_id}/results")
async def get_results(exp_id: str, request: Request) -> JSONResponse:
    """Get full significance results for an experiment."""
    if guard := _require_pro(request):
        return guard
    try:
        results = _store().get_results(exp_id)
        return JSONResponse(content=results)
    except ValueError as exc:
        return _err(404, str(exc))


@ab_router.post("/ab/experiments/{exp_id}/promote")
async def promote_winner(
    exp_id: str,
    body: PromoteRequest,
    request: Request,
) -> JSONResponse:
    """Manual override: force a variant as the winner."""
    if guard := _require_pro(request):
        return guard
    try:
        exp = _store().force_winner(exp_id=exp_id, variant=body.variant)
        return JSONResponse(
            content={
                "promoted": True,
                "winner": body.variant,
                "experiment": exp.to_dict(),
            }
        )
    except ValueError as exc:
        return _err(400, str(exc))


@ab_router.post("/ab/experiments/{exp_id}/cancel")
async def cancel_experiment(exp_id: str, request: Request) -> JSONResponse:
    """Cancel an active experiment."""
    if guard := _require_pro(request):
        return guard
    try:
        exp = _store().cancel_experiment(exp_id)
        return JSONResponse(content={"cancelled": True, "experiment": exp.to_dict()})
    except ValueError as exc:
        return _err(400, str(exc))
