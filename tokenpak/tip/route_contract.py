# SPDX-License-Identifier: Apache-2.0
"""TIP optimization route class — semantic request type taxonomy.

``OptimizationRouteClass`` classifies *what the user is trying to do*,
independent of which client or model is being used. It drives route-class
compression policies, cache eligibility, and fidelity tier selection.

This is distinct from ``tokenpak.core.routing.route_class.RouteClass``,
which classifies *who sent the request* (client identity: Claude Code,
Anthropic SDK, OpenAI SDK, generic). Both axes are useful; they address
different policy concerns.

Assignment responsibility
-------------------------
The proxy (Component B) will assign ``OptimizationRouteClass``
from intent-classification signals. Adapters may provide hints via
platform metadata. Do not hardcode per-adapter route class mappings
here — the assignment logic belongs in the proxy optimization layer.
"""

from __future__ import annotations

from enum import Enum


class OptimizationRouteClass(str, Enum):
    """Semantic taxonomy of LLM request content types.

    Inherits from ``str`` so values are naturally JSON-serialisable and
    usable as YAML/config keys without conversion.

    Policy implications (implemented in proxy/optimization/):
    - Cache eligibility varies by class (status_check: response-reusable;
      code_edit: context-reusable only).
    - Compression recipe selection is class-driven (debugging → exception
      recipes; git_diff_review → diff recipes).
    - Fidelity defaults differ (code_edit → lossless_required;
      summarization → aggressive_ok).
    """

    # Informational / low-risk (most cache-friendly)
    GENERAL_CHAT = "general_chat"
    STATUS_CHECK = "status_check"
    CONFIGURATION_INSPECTION = "configuration_inspection"

    # Code production (high fidelity, limited response reuse)
    CODE_GENERATION = "code_generation"
    CODE_EDIT = "code_edit"
    CODE_REVIEW = "code_review"

    # Diagnostic (high fidelity, specialized recipes)
    DEBUGGING = "debugging"
    TEST_FAILURE = "test_failure"
    LOG_ANALYSIS = "log_analysis"
    GIT_DIFF_REVIEW = "git_diff_review"
    SHELL_COMMAND_ANALYSIS = "shell_command_analysis"

    # Content production (more compression latitude)
    DOCUMENTATION_GENERATION = "documentation_generation"
    SUMMARIZATION = "summarization"

    # Planning / research (moderate)
    RESEARCH = "research"
    PLANNING = "planning"

    # Unresolved — classifier could not determine
    UNKNOWN = "unknown"

    @property
    def is_code_task(self) -> bool:
        """True for classes where code integrity is paramount."""
        return self in {
            OptimizationRouteClass.CODE_GENERATION,
            OptimizationRouteClass.CODE_EDIT,
            OptimizationRouteClass.CODE_REVIEW,
            OptimizationRouteClass.DEBUGGING,
            OptimizationRouteClass.TEST_FAILURE,
            OptimizationRouteClass.GIT_DIFF_REVIEW,
            OptimizationRouteClass.SHELL_COMMAND_ANALYSIS,
        }

    @property
    def allows_response_reuse_by_default(self) -> bool:
        """True for classes where response reuse is safe with conservative thresholds."""
        return self in {
            OptimizationRouteClass.STATUS_CHECK,
            OptimizationRouteClass.CONFIGURATION_INSPECTION,
            OptimizationRouteClass.SUMMARIZATION,
        }


__all__ = ["OptimizationRouteClass"]
