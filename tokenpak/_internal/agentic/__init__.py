"""tokenpak._internal.agentic — PRO-gated agentic features (workflow, handoff, capabilities)."""

# PRO features — require tokenpak-pro license
try:
    from .workflow import WorkflowEngine
    from .handoff import HandoffManager
    from .capabilities import CapabilityRegistry
    from .retry import RetryEngine  # PRO retry extensions
    __all__ = ["WorkflowEngine", "HandoffManager", "CapabilityRegistry", "RetryEngine"]
except ImportError:
    __all__ = []
