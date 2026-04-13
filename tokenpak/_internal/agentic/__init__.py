"""tokenpak._internal.agentic — PRO-gated agentic features (workflow, handoff, capabilities)."""

# PRO features — require tokenpak-pro license
try:
    from .workflow import WorkflowEngine
    from .handoff import HandoffManager
    from .capabilities import CapabilityRegistry
    from .retry import RetryEngine  # PRO retry extensions
    __all__ = ['WorkflowEngine', 'HandoffManager', 'CapabilityRegistry', 'RetryEngine', 'capabilities', 'case_memory', 'failure_memory', 'handoff', 'learning', 'locks', 'memory_promoter', 'precondition_gates', 'prefetcher', 'proxy_workflow', 'registry', 'runbook_generator', 'skill_compiler', 'state_collector', 'validation_framework', 'workflow', 'workflow_budget', 'workflow_performance']
except ImportError:
    __all__ = ['capabilities', 'case_memory', 'failure_memory', 'handoff', 'learning', 'locks', 'memory_promoter', 'precondition_gates', 'prefetcher', 'proxy_workflow', 'registry', 'runbook_generator', 'skill_compiler', 'state_collector', 'validation_framework', 'workflow', 'workflow_budget', 'workflow_performance']
