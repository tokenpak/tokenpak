"""tokenpak.orchestration — agentic utilities (error normalization, retry, workflows).

All submodule imports are lazy to avoid loading ~420KB of orchestration code
unless actually needed.  Most callers import specific submodules directly
(e.g. ``from tokenpak.orchestration.retry import RetryEngine``).
"""


def __getattr__(name: str):
    if name == "ErrorNormalizer":
        from .error_normalizer import ErrorNormalizer
        return ErrorNormalizer
    if name == "RetryEngine":
        from .retry import RetryEngine
        return RetryEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ['ErrorNormalizer', 'RetryEngine', 'capabilities', 'case_memory', 'episode_distiller', 'error_normalizer', 'handoff', 'learning', 'locks', 'memory_promoter', 'prefetcher', 'proxy_workflow', 'registry', 'retry', 'runbook_generator', 'state_collector', 'validation_framework', 'workflow', 'workflow_budget']
