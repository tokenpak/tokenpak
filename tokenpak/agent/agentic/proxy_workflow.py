"""proxy_workflow.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.agent.agentic.proxy_workflow import (
        WorkflowState,
        WorkflowContext,
        WorkflowStep,
        Workflow,
        WorkflowManager,
    )

    __all__ = [
        "WorkflowState",
        "WorkflowContext",
        "WorkflowStep",
        "Workflow",
        "WorkflowManager",
    ]
except ImportError:
    raise ImportError(
        "proxy_workflow requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
