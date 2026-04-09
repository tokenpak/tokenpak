
import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.agentic is deprecated, use tokenpak.agentic instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

# tokenpak.agent.agentic — Agentic Layer
from .case_memory import CaseMemoryDB, CaseRecord
from .error_normalizer import ErrorNormalizer, FailureSignatureDB, MergeSuggestion
from .memory_promoter import MemoryPromoter
from .handoff import (
    DEFAULT_HANDOFF_DIR,
    REGISTERED_AGENTS,
    ContextRef,
    Handoff,
    HandoffManager,
    HandoffStatus,
)
from .learning import (
    DEFAULT_LEARNING_PATH,
    cmd_learn_status,
    get_best_model,
    get_best_quality_per_token,
    get_compression_quality_signal,
    get_effective_compression,
    learn,
    load,
    record_quality_per_token,
    reset,
)
from .locks import FileLockManager, LockConflictError, LockExpiredError
from .prefetcher import (
    DEFAULT_DIAGNOSTIC_ARTIFACTS,
    PredictivePrefetcher,
    PrefetchStore,
)
from .retry import RetryEngine, RetryExhaustedError

__all__ = [
    "CaseMemoryDB",
    "CaseRecord",
    "FileLockManager",
    "LockConflictError",
    "LockExpiredError",
    "RetryEngine",
    "RetryExhaustedError",
    "MemoryPromoter",
    "learn",
    "load",
    "reset",
    "get_best_model",
    "get_best_quality_per_token",
    "get_compression_quality_signal",
    "get_effective_compression",
    "record_quality_per_token",
    "cmd_learn_status",
    "DEFAULT_LEARNING_PATH",
    "HandoffManager",
    "HandoffStatus",
    "ContextRef",
    "Handoff",
    "DEFAULT_HANDOFF_DIR",
    "REGISTERED_AGENTS",
    "PredictivePrefetcher",
    "PrefetchStore",
    "DEFAULT_DIAGNOSTIC_ARTIFACTS",
    "ErrorNormalizer",
    "FailureSignatureDB",
    "MergeSuggestion",
]
from .state_collector import (
    SCHEMA_VERSION,
    STALE_THRESHOLD_SECONDS,
    EnvState,
    FileState,
    GitState,
    ServiceState,
    StateCollector,
    StructuredState,
    TestState,
)

__all__ += [
    "StateCollector",
    "StructuredState",
    "GitState",
    "ServiceState",
    "EnvState",
    "FileState",
    "TestState",
    "SCHEMA_VERSION",
    "STALE_THRESHOLD_SECONDS",
]
from .validation_framework import (
    FileStateValidator,
    PostActionValidator,
    RetryPolicy,
    SchemaValidator,
    ServiceHealthValidator,
    TestSuiteValidator,
    ValidationCheck,
    ValidationError,
    ValidationOrchestrator,
    ValidationResult,
    make_validated_step_handler,
)

__all__ += [
    "PostActionValidator",
    "ValidationResult",
    "ValidationCheck",
    "ValidationError",
    "RetryPolicy",
    "ValidationOrchestrator",
    "ServiceHealthValidator",
    "TestSuiteValidator",
    "FileStateValidator",
    "SchemaValidator",
    "make_validated_step_handler",
]

from .runbook_generator import (
    DEFAULT_RUNBOOKS_DIR,
    Episode,
    RunbookDB,
    RunbookEntry,
    generate_from_episode,
    get_runbook,
    maybe_generate,
    render_markdown,
    should_generate,
)

__all__ += [
    "DEFAULT_RUNBOOKS_DIR",
    "Episode",
    "RunbookDB",
    "RunbookEntry",
    "generate_from_episode",
    "get_runbook",
    "maybe_generate",
    "render_markdown",
    "should_generate",
]
from .skill_compiler import (
    DEFAULT_SKILLS_DIR,
    PROMOTION_MIN_SUCCESS_RATE,
    PROMOTION_MIN_SUCCESSFUL_EPISODES,
    PROMOTION_MIN_TOKEN_SAVINGS,
    ExtractedSkill,
    SkillCompiler,
    SkillEpisode,
    SkillStore,
)

__all__ += [
    "DEFAULT_SKILLS_DIR",
    "PROMOTION_MIN_SUCCESSFUL_EPISODES",
    "PROMOTION_MIN_SUCCESS_RATE",
    "PROMOTION_MIN_TOKEN_SAVINGS",
    "SkillEpisode",
    "ExtractedSkill",
    "SkillStore",
    "SkillCompiler",
]
