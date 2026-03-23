"""precondition_gates.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.agent.agentic.precondition_gates import (
        Gate,
        GateResult,
        GateSet,
        ResourceGate,
        HealthGate,
        CustomGate,
        PreconditionGates,
        SUPPORTED_GATE_TYPES,
        AUTO_PROMOTE_THRESHOLD,
        _check_env_check,
        _check_file_exists,
        _check_service_running,
        _check_test_passing,
        _check_diff_clean,
    )

    __all__ = [
        "Gate",
        "GateResult",
        "GateSet",
        "ResourceGate",
        "HealthGate",
        "CustomGate",
        "PreconditionGates",
        "SUPPORTED_GATE_TYPES",
        "AUTO_PROMOTE_THRESHOLD",
        "_check_env_check",
        "_check_file_exists",
        "_check_service_running",
        "_check_test_passing",
        "_check_diff_clean",
    ]
except ImportError:
    raise ImportError(
        "precondition_gates requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
