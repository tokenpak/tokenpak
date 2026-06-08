"""Tests for the Dispatch capability registry (P-SCHEMA-01).

Verifies the §5.2 capability registry: the exact 11-entry frozenset, fail-loud
rejection of unknown capability strings at load time, and the registry-bound
validation wired into DispatchWorker and RouteStation.
"""

from __future__ import annotations

import pytest

# Dispatch is pydantic-native; deps ship via the opt-in `dispatch` extra
# (pyproject [project.optional-dependencies]). Skip cleanly on slim installs
# that lack it rather than erroring at collection time.
pytest.importorskip("pydantic")

from pydantic import ValidationError

from tokenpak.orchestration.dispatch.models import DispatchRoute, DispatchWorker
from tokenpak.orchestration.dispatch.registry.capabilities import (
    DISPATCH_CAPABILITIES,
    UnknownCapabilityError,
    is_known_capability,
    validate_capabilities,
)

EXPECTED_CAPABILITIES = {
    "answer_generation",
    "code_drafting",
    "code_editing",
    "patch_generation",
    "doc_drafting",
    "doc_review",
    "semantic_review",
    "test_planning",
    "test_execution",
    "repo_inspection",
    "artifact_packaging",
}


def test_capability_registry_is_exact_eleven_entry_frozenset():
    assert isinstance(DISPATCH_CAPABILITIES, frozenset)
    assert DISPATCH_CAPABILITIES == EXPECTED_CAPABILITIES
    assert len(DISPATCH_CAPABILITIES) == 11


def test_is_known_capability():
    assert is_known_capability("code_drafting")
    assert not is_known_capability("mine_bitcoin")


def test_validate_capabilities_accepts_known_strings():
    caps = ["code_drafting", "patch_generation"]
    assert validate_capabilities(caps) == caps


def test_validate_capabilities_rejects_unknown_at_load_time():
    with pytest.raises(UnknownCapabilityError) as exc:
        validate_capabilities(["code_drafting", "deploy_to_prod", "mine_bitcoin"])
    # Reports all offenders, not just the first.
    assert exc.value.unknown == ["deploy_to_prod", "mine_bitcoin"]


def test_unknown_capability_error_is_value_error():
    # Catchable as ValueError so Pydantic surfaces it as a validation error.
    assert issubclass(UnknownCapabilityError, ValueError)


def test_dispatch_worker_accepts_valid_capabilities():
    worker = DispatchWorker(
        id="worker.builder.default.v1",
        roles=["builder"],
        capabilities=["answer_generation", "code_drafting"],
        input_schema="station_input.v1",
        output_schema="station_result.v1",
        default_loop_policy={
            "max_iterations": 3,
            "max_tool_calls": 8,
            "max_wall_seconds": 900,
        },
        permission_profile={},
    )
    assert worker.capabilities == ["answer_generation", "code_drafting"]


def test_dispatch_worker_rejects_unknown_capability():
    with pytest.raises(ValidationError):
        DispatchWorker(
            id="worker.bad.v1",
            roles=["builder"],
            capabilities=["code_drafting", "exfiltrate_secrets"],
            input_schema="station_input.v1",
            output_schema="station_result.v1",
            default_loop_policy={
                "max_iterations": 1,
                "max_tool_calls": 1,
                "max_wall_seconds": 60,
            },
            permission_profile={},
        )


def test_route_station_rejects_unknown_required_capability():
    with pytest.raises(ValidationError):
        DispatchRoute(
            id="route.bad.v1",
            name="bad",
            description="route with a bad station capability",
            default_risk="low",
            stations=[
                {
                    "id": "build",
                    "required_role": "builder",
                    "required_capabilities": ["code_drafting", "not_a_capability"],
                    "output_schema": "station_result.v1",
                }
            ],
        )
