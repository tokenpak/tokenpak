"""tokenpak/agent/ingest/api.py

Phase 5A: Ingest API
====================
Provides HTTP endpoints for agents to write usage entries into the vault index.

Endpoints:
  POST /ingest        — ingest a single entry
  POST /ingest/batch  — ingest a list of entries

Storage:
  ~/vault/.tokenpak/entries/YYYY-MM-DD.jsonl  (append-only, one entry per line)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

VAULT_ENTRIES_DIR = Path(os.path.expanduser("~/vault/.tokenpak/entries"))


def _entries_file(date_str: Optional[str] = None) -> Path:
    """Return path to today's (or given date's) JSONL file."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    VAULT_ENTRIES_DIR.mkdir(parents=True, exist_ok=True)
    return VAULT_ENTRIES_DIR / f"{date_str}.jsonl"


def _write_entry(entry: dict[str, Any]) -> str:
    """Append a single entry to the JSONL file, return its id."""
    import uuid

    entry_id = entry.setdefault("id", str(uuid.uuid4()))
    date_str = None
    # Use timestamp date if provided, else today
    ts = entry.get("timestamp")
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    path = _entries_file(date_str)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())
    logger.debug("Wrote entry %s to %s", entry_id, path)
    return entry_id


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Entry(BaseModel):
    """A single ingest entry from an agent."""

    model: str = Field(..., description="Model name (e.g. claude-haiku)")
    tokens: int = Field(..., ge=0, description="Total tokens used")
    cost: float = Field(..., ge=0.0, description="Cost in USD")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 timestamp",
    )
    agent: Optional[str] = Field(None, description="Agent name (optional)")
    provider: Optional[str] = Field(None, description="Provider (optional)")
    session_id: Optional[str] = Field(None, description="Session id (optional)")
    extra: Optional[dict[str, Any]] = Field(None, description="Additional metadata")

    @field_validator("timestamp")
    @classmethod
    def _validate_ts(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid ISO 8601 timestamp: {v!r}")
        return v

    model_config = {"extra": "allow"}


class IngestResponse(BaseModel):
    status: str = "ok"
    ids: List[str]


class ErrorResponse(BaseModel):
    status: str = "error"
    detail: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["ingest"])


@router.post("/ingest", response_model=IngestResponse)
def ingest_single(entry: Entry) -> IngestResponse:
    """Ingest a single usage entry."""
    try:
        data = entry.model_dump()
        entry_id = _write_entry(data)
        return IngestResponse(status="ok", ids=[entry_id])
    except Exception as exc:
        logger.exception("Failed to write ingest entry: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/ingest/batch", response_model=IngestResponse)
def ingest_batch(entries: List[Entry]) -> IngestResponse:
    """Ingest a batch of usage entries."""
    if not entries:
        raise HTTPException(status_code=400, detail="entries list is empty")
    if len(entries) > 1000:
        raise HTTPException(status_code=400, detail="batch too large (max 1000)")
    ids = []
    errors = []
    for i, entry in enumerate(entries):
        try:
            data = entry.model_dump()
            entry_id = _write_entry(data)
            ids.append(entry_id)
        except Exception as exc:
            errors.append(f"entry[{i}]: {exc}")
    if errors and not ids:
        raise HTTPException(status_code=500, detail="; ".join(errors))
    return IngestResponse(status="ok", ids=ids)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_ingest_app(prefix: str = "") -> Any:
    """Create a standalone FastAPI app with ingest + query routes."""
    from fastapi import FastAPI

    app = FastAPI(
        title="TokenPak API",
        version="5.0.0",
        description="Phase 5A+5B: Agent usage data ingest and query",
    )
    app.include_router(router, prefix=prefix)

    # Mount Phase 5B query router if available
    try:
        from tokenpak.agent.query.api import router as query_router

        app.include_router(query_router, prefix=prefix)
    except ImportError:
        pass

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "tokenpak-ingest"}

    return app
