# SPDX-License-Identifier: Apache-2.0
"""Shared offline session-economics payload fixtures for surface tests.

One canonical fixture is used by the status, dashboard, and companion tests
so the "identical values across surfaces" completion criterion is asserted
against literally the same payload. Fixtures are plain dicts; every test
validates them through ``SessionEconomics.from_dict`` before use, so an
invalid fixture fails loudly rather than skewing a golden.
"""

from __future__ import annotations

import copy
from typing import Any

#: A mid-session "learning" payload: observed token facts, estimated cost
#: with fresh rate provenance, available runway, forecast still learning.
LEARNING_PAYLOAD: dict[str, Any] = {
    "schema_version": "session-economics/1",
    "as_of": "2026-08-17T12:00:00+00:00",
    "session": {
        "id": "sess-fixture-1",
        "identity_state": "observed",
        "turns_observed": 3,
        "model": {"id": "claude-sonnet-4-5", "effort": "unknown"},
    },
    "facts": {
        "input_tokens": {"state": "observed", "value": 120000, "source": "provider_usage"},
        "output_tokens": {"state": "observed", "value": 8000, "source": "provider_usage"},
        "cache_read_tokens": {"state": "observed", "value": 400000, "source": "provider_usage"},
        "cache_write_tokens": {"state": "observed", "value": 20000, "source": "provider_usage"},
        "cost_usd": {
            "state": "estimated",
            "value": 1.23,
            "basis": "rate_card",
            "rate_provenance": {
                "catalog_version": "models-2026-08",
                "effective_at": "2026-08-01T00:00:00+00:00",
                "source": "tokenpak.models",
                "freshness": "fresh",
            },
        },
    },
    "state": {
        "context_tokens": {"state": "observed", "value": 130000, "source": "ledger"},
        "base_tokens": {"state": "no_data", "value": None, "reason": "not measured"},
        "context_growth_ewma": {"state": "estimated", "value": 5000, "source": "ewma"},
        "burn_tokens_per_turn": {"state": "estimated", "value": 42000, "source": "ewma"},
        "burn_usd_per_turn": {"state": "estimated", "value": 0.41, "source": "ewma"},
        "burn_slope": "up",
        "idle_seconds": {"state": "observed", "value": 12, "source": "ledger"},
        "cache_ttl_seconds": {"state": "observed", "value": 300, "source": "config"},
        "cache_state": "warm",
    },
    "runway": {
        "status": "available",
        "turns": 14,
        "binding_constraint": "context_soft",
        "guard_state": "allow",
    },
    "forecast": {
        "status": "learning",
        "remaining_tokens_likely_50": {"state": "unavailable", "low": None, "high": None},
        "remaining_tokens_ceiling_90": {"state": "unavailable", "value": None},
        "remaining_cost_usd_likely_50": {"state": "unavailable", "low": None, "high": None},
        "remaining_cost_usd_ceiling_90": {"state": "unavailable", "value": None},
        "expected_turns": {"state": "unavailable", "low": None, "high": None},
        "coverage": {
            "method": None,
            "observed": None,
            "history_n": 0,
            "drift_state": "unknown",
        },
        "predicted_block_probability": {"state": "unavailable", "value": None},
        "reason": "remaining-task forecast is not implemented",
    },
    "advisory": None,
}

#: An empty-identity payload: no session could be resolved; everything is an
#: explicit no-value state. Mirrors the engine's ``_empty_payload`` shape.
NO_DATA_PAYLOAD: dict[str, Any] = {
    "schema_version": "session-economics/1",
    "as_of": "2026-08-17T12:00:00+00:00",
    "session": {
        "id": None,
        "identity_state": "no_data",
        "turns_observed": 0,
        "model": {"id": "unknown", "effort": "unknown"},
        "reason": "stable session identity is missing",
    },
    "facts": {
        "input_tokens": {"state": "no_data", "value": None},
        "output_tokens": {"state": "no_data", "value": None},
        "cache_read_tokens": {"state": "no_data", "value": None},
        "cache_write_tokens": {"state": "no_data", "value": None},
        "cost_usd": {
            "state": "no_data",
            "value": None,
            "basis": "unknown",
            "rate_provenance": {
                "catalog_version": None,
                "effective_at": None,
                "source": None,
                "freshness": "unknown",
            },
        },
    },
    "state": {
        "context_tokens": {"state": "no_data", "value": None},
        "base_tokens": {"state": "no_data", "value": None},
        "context_growth_ewma": {"state": "no_data", "value": None},
        "burn_tokens_per_turn": {"state": "no_data", "value": None},
        "burn_usd_per_turn": {"state": "no_data", "value": None},
        "burn_slope": "unknown",
        "idle_seconds": {"state": "no_data", "value": None},
        "cache_ttl_seconds": {"state": "no_data", "value": None},
        "cache_state": "unknown",
    },
    "runway": {
        "status": "unavailable",
        "turns": None,
        "binding_constraint": "unknown",
        "guard_state": "unknown",
        "reason": "stable session identity is missing",
    },
    "forecast": {
        "status": "unavailable",
        "remaining_tokens_likely_50": {"state": "unavailable", "low": None, "high": None},
        "remaining_tokens_ceiling_90": {"state": "unavailable", "value": None},
        "remaining_cost_usd_likely_50": {"state": "unavailable", "low": None, "high": None},
        "remaining_cost_usd_ceiling_90": {"state": "unavailable", "value": None},
        "expected_turns": {"state": "unavailable", "low": None, "high": None},
        "coverage": {
            "method": None,
            "observed": None,
            "history_n": 0,
            "drift_state": "unknown",
        },
        "predicted_block_probability": {"state": "unavailable", "value": None},
        "reason": "stable session identity is missing",
    },
    "advisory": None,
}


def learning_payload() -> dict[str, Any]:
    return copy.deepcopy(LEARNING_PAYLOAD)


def no_data_payload() -> dict[str, Any]:
    return copy.deepcopy(NO_DATA_PAYLOAD)


def available_payload() -> dict[str, Any]:
    """LEARNING_PAYLOAD upgraded to an available calibrated forecast."""
    payload = copy.deepcopy(LEARNING_PAYLOAD)
    src = "walk-forward split-conformal empirical quantiles (48 sessions)"
    payload["forecast"] = {
        "status": "available",
        "remaining_tokens_likely_50": {
            "state": "estimated",
            "low": 40000,
            "high": 160000,
            "source": src,
        },
        "remaining_tokens_ceiling_90": {"state": "estimated", "value": 320000, "source": src},
        "remaining_cost_usd_likely_50": {
            "state": "estimated",
            "low": 0.41,
            "high": 1.64,
            "source": src,
        },
        "remaining_cost_usd_ceiling_90": {"state": "estimated", "value": 3.28, "source": src},
        "expected_turns": {"state": "estimated", "low": 2, "high": 9, "source": src},
        "coverage": {
            "method": "walk-forward split-conformal empirical-quantile v1",
            "observed": 0.52,
            "history_n": 48,
            "drift_state": "stable",
        },
        "predicted_block_probability": {"state": "estimated", "value": 0.08, "source": src},
    }
    return payload
