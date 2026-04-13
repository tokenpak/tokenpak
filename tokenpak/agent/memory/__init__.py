"""Session memory capsule utilities and decision memory."""

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.memory is deprecated, use tokenpak._internal.memory instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from .session_capsules import (
    REQUIRED_CAPSULE_SECTIONS,
    build_session_capsule,
    capsule_retrieval_score,
    score_capsule_sections,
    serialize_capsule,
)
from .decision_memory import (
    DecisionMemoryDB,
    DecisionRecord,
)

__all__ = ['REQUIRED_CAPSULE_SECTIONS', 'build_session_capsule', 'capsule_retrieval_score', 'score_capsule_sections', 'serialize_capsule', 'DecisionMemoryDB', 'DecisionRecord', 'decision_memory', 'lesson_ingest', 'session_capsules']
