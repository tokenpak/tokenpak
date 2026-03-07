"""
TokenPak Intelligence Server — POST /v1/license/validate endpoint.

FastAPI router ready to be mounted on the main intelligence app:

    from tokenpak.intelligence.license_endpoint import router as license_router
    app.include_router(license_router, prefix="/v1")

Environment variables:
    TOKENPAK_LICENSE_PUBLIC_KEY   — PEM-encoded RSA public key (preferred)
    TOKENPAK_LICENSE_PUBLIC_KEY_FILE — Path to PEM file (fallback)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

try:
    from fastapi import APIRouter, HTTPException, status

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

    # Stub so file is importable without FastAPI (e.g. in tests)
    class APIRouter:  # type: ignore
        def post(self, *a, **kw):
            def decorator(fn):
                return fn

            return decorator


from ..agent.license.store import LicenseStore
from ..agent.license.validator import LicenseStatus, LicenseValidator, ValidationResult

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Shared instances (initialised at import time)
# ─────────────────────────────────────────────


def _load_public_pem() -> Optional[bytes]:
    """Load RSA public key from env or file."""
    raw = os.environ.get("TOKENPAK_LICENSE_PUBLIC_KEY", "")
    if raw:
        return raw.encode()
    key_file = os.environ.get("TOKENPAK_LICENSE_PUBLIC_KEY_FILE", "")
    if key_file:
        p = Path(key_file)
        if p.exists():
            return p.read_bytes()
    return None


_public_pem = _load_public_pem()
_validator = LicenseValidator(public_pem=_public_pem)
_store = LicenseStore()

router = APIRouter(tags=["license"])


# ─────────────────────────────────────────────
# Request / response schemas
# ─────────────────────────────────────────────


class LicenseValidateRequest(BaseModel):
    token: str = Field(..., description="Signed license token (<payload>.<sig>)")
    agent_id: Optional[str] = Field(None, description="Agent ID for seat counting (Team tier)")


class LicenseValidateResponse(BaseModel):
    status: str
    tier: str
    features: list[str]
    seats: int
    seats_used: int
    expires_at: Optional[str]
    grace_expires_at: Optional[str]
    message: str


# ─────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────


@router.post(
    "/license/validate",
    response_model=LicenseValidateResponse,
    summary="Validate a TokenPak license key",
    description=(
        "Verifies the RSA signature on a license token, checks expiry and grace period, "
        "performs seat counting for Team tier, and returns tier + active features."
    ),
)
async def validate_license(request: LicenseValidateRequest) -> LicenseValidateResponse:
    """
    POST /v1/license/validate

    Body:
        { "token": "<license_token>", "agent_id": "<optional>" }

    Returns:
        status, tier, features list, seat info, expiry dates
    """
    result: ValidationResult = _validator.validate(
        token=request.token,
        agent_id=request.agent_id,
    )

    # On successful validation, update the local cache
    if result.status in (LicenseStatus.VALID, LicenseStatus.GRACE):
        try:
            _store.save(
                token=request.token,
                tier=result.tier.value,
                expires_at=result.expires_at,
            )
        except Exception as exc:
            logger.warning("Could not cache license: %s", exc)

    if result.status == LicenseStatus.INVALID:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=result.message,
        )

    if result.status == LicenseStatus.SEAT_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Seat limit reached ({result.seats_used}/{result.seats}). "
            "Upgrade to Team+ or free up a seat.",
        )

    return LicenseValidateResponse(**result.to_dict())


# ─────────────────────────────────────────────
# Offline grace check (internal helper used by agent)
# ─────────────────────────────────────────────


def check_offline_grace() -> dict:
    """
    Called by the agent when the intelligence server is unreachable.
    Returns cached license info if within 7-day grace window.
    """
    return _store.grace_status()
