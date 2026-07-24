"""tokenpak.orchestration — agentic utilities (error normalization, retry, workflows).

All submodule imports are lazy to avoid loading ~420KB of orchestration code
unless actually needed.  Most callers import specific submodules directly
(e.g. ``from tokenpak.orchestration.retry import RetryEngine``).
"""


def __getattr__(name: str) -> object:
    _map = {
        "ErrorNormalizer": lambda: (
            __import__(
                "tokenpak.orchestration.error_normalizer", fromlist=["ErrorNormalizer"]
            ).ErrorNormalizer
        ),
        "RetryEngine": lambda: (
            __import__("tokenpak.orchestration.retry", fromlist=["RetryEngine"]).RetryEngine
        ),
        "HandoffManager": lambda: (
            __import__("tokenpak.orchestration.handoff", fromlist=["HandoffManager"]).HandoffManager
        ),
        "HandoffBlock": lambda: (
            __import__("tokenpak.orchestration.handoff", fromlist=["HandoffBlock"]).HandoffBlock
        ),
        "HandoffStatus": lambda: (
            __import__("tokenpak.orchestration.handoff", fromlist=["HandoffStatus"]).HandoffStatus
        ),
        "HandoffWire": lambda: (
            __import__("tokenpak.orchestration.handoff", fromlist=["HandoffWire"]).HandoffWire
        ),
        "TokenPak": lambda: (
            __import__("tokenpak.orchestration.handoff", fromlist=["TokenPak"]).TokenPak
        ),
        "ContextRef": lambda: (
            __import__("tokenpak.orchestration.handoff", fromlist=["ContextRef"]).ContextRef
        ),
    }
    if name in _map:
        return _map[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ErrorNormalizer",
    "RetryEngine",
    "capabilities",
    "case_memory",
    "episode_distiller",
    "error_normalizer",
    "handoff",
    "learning",
    "locks",
    "memory_promoter",
    "prefetcher",
    "proxy_workflow",
    "registry",
    "retry",
    "runbook_generator",
    "state_collector",
    "validation_framework",
    "workflow",
    "workflow_budget",
]
