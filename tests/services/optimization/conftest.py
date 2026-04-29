"""Fixtures shared across optimization-pipeline tests."""

from __future__ import annotations

import json
import os

import pytest

from tokenpak.services.optimization import (
    OptimizationPipeline,
    StageRegistry,
)
from tokenpak.services.optimization.pipeline import reset_default_pipeline


@pytest.fixture(autouse=True)
def _reset_default_pipeline():
    """Each test starts with a clean default pipeline."""
    reset_default_pipeline()
    yield
    reset_default_pipeline()


@pytest.fixture
def fresh_pipeline():
    """A pipeline backed by an empty registry."""
    return OptimizationPipeline(registry=StageRegistry())


@pytest.fixture
def openai_responses_body():
    """Realistic OpenAI Responses request body, byte-identical on re-read.

    Uses keys in non-alphabetical order on purpose — the byte-preservation
    guarantee should not depend on dict ordering.
    """
    payload = {
        "model": "gpt-4o-mini",
        "input": "Summarize the README for tokenpak.",
        "stream": False,
        "metadata": {"trace_id": "test-1"},
    }
    return json.dumps(payload, sort_keys=False).encode("utf-8")


@pytest.fixture
def codex_responses_body():
    """OpenAI Codex Responses-style body — slightly different shape."""
    payload = {
        "model": "gpt-5-codex",
        "input": [
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": "Refactor pipeline.py for clarity."},
        ],
        "stream": True,
        "tools": [{"type": "function", "name": "edit_file"}],
    }
    return json.dumps(payload).encode("utf-8")


@pytest.fixture
def env_observe(monkeypatch):
    """Activate observe-only mode for the call site under test."""
    monkeypatch.setenv("TOKENPAK_OPTIMIZATION_PIPELINE", "observe")
    yield os.environ.copy()
    monkeypatch.delenv("TOKENPAK_OPTIMIZATION_PIPELINE", raising=False)


@pytest.fixture
def env_off(monkeypatch):
    """Force the pipeline off (default state)."""
    monkeypatch.delenv("TOKENPAK_OPTIMIZATION_PIPELINE", raising=False)
    yield os.environ.copy()
