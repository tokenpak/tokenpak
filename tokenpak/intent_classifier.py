"""Intent and complexity classifier for TokenPak Dynamic Context.

Deterministically classifies user requests into intent classes and assigns
complexity scores without LLM calls. Output feeds the Budget Controller tier selector.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class IntentClass(str, Enum):
    """User intent classification."""

    GEN_Q = "GEN_Q"  # General question, no project context
    CODE_Q = "CODE_Q"  # Explain/locate/understand code
    CODE_EDIT = "CODE_EDIT"  # Modify code
    DEBUG = "DEBUG"  # Analyze logs/errors
    DOC_EDIT = "DOC_EDIT"  # Edit prose/spec
    PLAN = "PLAN"  # Architecture/planning
    REVIEW = "REVIEW"  # Review diff/PR/design


@dataclass
class ClassificationResult:
    """Result of intent + complexity classification."""

    intent: IntentClass
    complexity_score: float  # 0.0 - 1.0
    needs_retrieval: bool  # Need to fetch context (files, history)
    needs_writeback: bool  # Need to write/modify something
    confidence: float  # 0.0 - 1.0, how certain we are


# ---------------------------------------------------------------------------
# Signal vocabularies
# ---------------------------------------------------------------------------

_CODE_Q_KEYWORDS = {
    "explain",
    "understand",
    "what",
    "how",
    "where",
    "find",
    "locate",
    "show",
    "tell",
    "look",
    "check",
    "see",
    "does",
    "work",
    "function",
    "class",
    "method",
    "code",
}

_CODE_EDIT_KEYWORDS = {
    "change",
    "add",
    "refactor",
    "fix",
    "modify",
    "update",
    "remove",
    "delete",
    "replace",
    "improve",
    "optimize",
    "rewrite",
    "implement",
    "create",
    "build",
    "write",
    "make",
    "convert",
}

_DEBUG_KEYWORDS = {
    "error",
    "exception",
    "bug",
    "crash",
    "fail",
    "broken",
    "issue",
    "problem",
    "traceback",
    "stack",
    "trace",
    "debug",
    "wrong",
    "incorrect",
    "unexpected",
    "doesn't work",
    "not working",
}

_DOC_EDIT_KEYWORDS = {
    "write",
    "draft",
    "document",
    "readme",
    "spec",
    "specification",
    "description",
    "comment",
    "docstring",
    "edit",
    "rewrite",
    "clarify",
    "explain",
    "prose",
    "text",
    "content",
    "guide",
}

_PLAN_KEYWORDS = {
    "plan",
    "design",
    "architect",
    "architecture",
    "strategy",
    "approach",
    "structure",
    "organize",
    "system",
    "framework",
    "pattern",
    "workflow",
    "pipeline",
    "refactor",
    "modularize",
    "decompose",
}

_REVIEW_KEYWORDS = {
    "review",
    "check",
    "feedback",
    "critique",
    "diff",
    "approve",
    "merge",
    "comment",
    "evaluate",
    "assess",
}

# Patterns for file paths, stack traces, etc.
_FILE_PATH_PATTERN = re.compile(
    r"\b[\w\-./]+\.(py|js|ts|go|rs|java|cpp|c|h|rb|php|jsx|tsx|vue|css|html|json|yaml|yml|md|txt|sh|bash|sql|xml)\b"
)
_STACK_TRACE_PATTERN = re.compile(
    r"(traceback|File .*line|at .*\(|exception|error:)", re.IGNORECASE
)
_FUNCTION_PATTERN = re.compile(r"\b[a-zA-Z_]\w*\(.*\)|def \w+|function \w+")
_CODE_BLOCK_PATTERN = re.compile(r"```[\w]*\n")


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------


def classify(
    query: str,
    context: Optional[str] = None,
    file_paths: Optional[List[str]] = None,
) -> ClassificationResult:
    """
    Classify a request into intent class + complexity.

    Args:
        query: The user's request text
        context: Optional surrounding context (file contents, history, etc.)
        file_paths: Optional list of file paths mentioned

    Returns:
        ClassificationResult with intent, complexity, and flags
    """
    query_lower = query.lower()
    context_lower = (context or "").lower()
    file_paths = file_paths or []

    # Detect intent
    intent = _detect_intent(query_lower, context_lower, file_paths)

    # Compute complexity (0.0 - 1.0)
    complexity = _compute_complexity(query, context or "", intent)

    # Determine retrieval + writeback needs
    needs_retrieval = _needs_retrieval(intent, query_lower, context_lower, file_paths)
    needs_writeback = _needs_writeback(intent, query_lower)

    # Confidence: lower for ambiguous cases, higher for clear signals
    confidence = _compute_confidence(query_lower, context_lower, intent)

    return ClassificationResult(
        intent=intent,
        complexity_score=complexity,
        needs_retrieval=needs_retrieval,
        needs_writeback=needs_writeback,
        confidence=confidence,
    )


def _detect_intent(query_lower: str, context_lower: str, file_paths: List[str]) -> IntentClass:
    """Detect the primary intent class."""
    scores = {
        IntentClass.CODE_Q: 0.0,
        IntentClass.CODE_EDIT: 0.0,
        IntentClass.DEBUG: 0.0,
        IntentClass.DOC_EDIT: 0.0,
        IntentClass.PLAN: 0.0,
        IntentClass.REVIEW: 0.0,
        IntentClass.GEN_Q: 0.0,
    }

    # --- Signal matching ---

    # DEBUG: Stack traces, exceptions, error messages take precedence
    if _STACK_TRACE_PATTERN.search(query_lower) or _STACK_TRACE_PATTERN.search(context_lower):
        scores[IntentClass.DEBUG] += 5.0
    if any(kw in query_lower for kw in _DEBUG_KEYWORDS):
        scores[IntentClass.DEBUG] += len([kw for kw in _DEBUG_KEYWORDS if kw in query_lower])

    # REVIEW: diff/PR/merge language (strong signals only)
    if any(phrase in query_lower for phrase in ["diff", "pull request", "merge"]):
        scores[IntentClass.REVIEW] += 3.0
    # Only count "review" if it's the main verb, not in every case
    if query_lower.startswith("review") or " review " in query_lower:
        scores[IntentClass.REVIEW] += 2.0
    # Feedback-seeking language: "what do you think", "thoughts on", "opinion on"
    if any(
        phrase in query_lower
        for phrase in [
            "what do you think",
            "what do you",
            "thoughts on",
            "opinion on",
            "feedback on",
        ]
    ):
        scores[IntentClass.REVIEW] += 1.5

    # DOC_EDIT: "write/draft/document" language (high priority)
    # "add docstring" OR "write" + "readme/doc/spec/guide"
    if "docstring" in query_lower and any(kw in query_lower for kw in ["add", "write", "update"]):
        scores[IntentClass.DOC_EDIT] += 3.0
    elif "write" in query_lower and any(
        w in query_lower for w in ["readme", "doc", "spec", "guide", "documentation"]
    ):
        scores[IntentClass.DOC_EDIT] += 3.0
    elif any(kw in query_lower for kw in _DOC_EDIT_KEYWORDS):
        scores[IntentClass.DOC_EDIT] += len([kw for kw in _DOC_EDIT_KEYWORDS if kw in query_lower])

    # PLAN: "architecture/design/plan" language (strategy talk)
    # Check for plan/design/architecture keywords
    plan_keywords_found = [kw for kw in _PLAN_KEYWORDS if kw in query_lower]
    if plan_keywords_found:
        scores[IntentClass.PLAN] += len(plan_keywords_found)

    # CODE_EDIT: "change/add/refactor" verbs
    # BUT: "add" in "add docstring" is DOC_EDIT, not CODE_EDIT
    # So check more carefully
    edit_keywords_found = [kw for kw in _CODE_EDIT_KEYWORDS if kw in query_lower]
    if edit_keywords_found:
        # "add docstring" is DOC_EDIT, not CODE_EDIT
        if not ("add" in edit_keywords_found and "docstring" in query_lower):
            scores[IntentClass.CODE_EDIT] += len(edit_keywords_found)

    # CODE_Q: "explain/understand/where/find/locate" + code signals
    # File paths are a code signal on their own
    has_code_signal = (
        _CODE_BLOCK_PATTERN.search(context_lower)
        or "def " in context_lower
        or "class " in context_lower
        or _FILE_PATH_PATTERN.search(query_lower)
        or _FUNCTION_PATTERN.search(query_lower)
        or "function" in query_lower  # "function works", "function does"
    )

    # "where is" + code keywords (decorator, class, function, etc.) = CODE_Q
    if any(kw in query_lower for kw in ["where", "find", "locate", "search"]):
        if has_code_signal or any(
            kw in query_lower for kw in ["decorator", "function", "class", "method", "module"]
        ):
            scores[IntentClass.CODE_Q] += 2.0

    if any(kw in query_lower for kw in ["explain", "understand"]):
        if has_code_signal:
            scores[IntentClass.CODE_Q] += 2.0

    # File path alone signals CODE_Q
    if _FILE_PATH_PATTERN.search(query_lower) and not (
        scores[IntentClass.CODE_EDIT] > 0 or scores[IntentClass.DOC_EDIT] > 0
    ):
        scores[IntentClass.CODE_Q] += 1.0

    # Boost CODE intent if there's code in context
    if has_code_signal:
        scores[IntentClass.CODE_Q] += 0.5
        scores[IntentClass.CODE_EDIT] += 0.5

    # Fallback to GEN_Q if no strong signals (threshold of 0.5)
    if max(scores.values()) < 0.5:
        return IntentClass.GEN_Q

    # Return the intent with highest score
    return max(scores, key=scores.get)


def _compute_complexity(query: str, context: str, intent: IntentClass) -> float:
    """Compute complexity score (0.0 - 1.0)."""
    score = 0.0
    query_lower = query.lower()

    # --- Query length (0.0 - 0.35) ---
    words = len(query.split())
    if words < 5:
        score += 0.0
    elif words < 20:
        score += 0.1
    elif words < 50:
        score += 0.25
    else:
        score += 0.35

    # --- Context volume (0.0 - 0.2) ---
    context_words = len(context.split())
    if context_words > 2000:
        score += 0.2
    elif context_words > 500:
        score += 0.1

    # --- Code block count (0.0 - 0.2) ---
    code_blocks = len(_CODE_BLOCK_PATTERN.findall(context))
    if code_blocks >= 3:
        score += 0.2
    elif code_blocks >= 1:
        score += 0.1

    # --- Clause depth / multi-step (0.0 - 0.15) ---
    clause_markers = len(re.findall(r"\b(if|when|while|because|although)\b", query, re.IGNORECASE))
    score += min(0.15, clause_markers * 0.05)

    # --- Explicit complexity indicators (0.0 - 0.1) ---
    complexity_keywords = ["refactor", "optimize", "architecture", "scale", "design"]
    if any(kw in query_lower for kw in complexity_keywords):
        score += 0.1

    # --- Intent-specific boosts ---
    if intent == IntentClass.DEBUG:
        # Stack traces add complexity
        if _STACK_TRACE_PATTERN.search(context):
            score += 0.15
    elif intent == IntentClass.CODE_EDIT:
        # Multiple files or refactoring adds complexity
        file_matches = _FILE_PATH_PATTERN.findall(query)
        if len(file_matches) >= 2:
            score += 0.1
    elif intent == IntentClass.PLAN:
        # Architecture questions are inherently complex
        score += 0.15
    elif intent == IntentClass.REVIEW:
        # Long context (diffs) adds complexity
        if context_words > 1000:
            score += 0.1

    # --- Clamp to [0.0, 1.0] ---
    return round(min(1.0, max(0.0, score)), 2)


def _needs_retrieval(
    intent: IntentClass,
    query_lower: str,
    context_lower: str,
    file_paths: List[str],
) -> bool:
    """Determine if request requires fetching context (files, history)."""
    # GEN_Q doesn't need retrieval
    if intent == IntentClass.GEN_Q:
        return False

    # CODE_Q always needs retrieval
    if intent == IntentClass.CODE_Q:
        return True

    # CODE_EDIT may need to fetch existing code
    if intent == IntentClass.CODE_EDIT and not _CODE_BLOCK_PATTERN.search(context_lower):
        return True

    # DEBUG needs error context
    if intent == IntentClass.DEBUG:
        return True

    # REVIEW often needs full diff context
    if intent == IntentClass.REVIEW:
        return True

    # If file paths are mentioned but not in context, need retrieval
    if file_paths or _FILE_PATH_PATTERN.search(query_lower):
        return True

    return False


def _needs_writeback(intent: IntentClass, query_lower: str) -> bool:
    """Determine if request requires writing/modifying files."""
    # These intents explicitly modify
    if intent in (IntentClass.CODE_EDIT, IntentClass.DOC_EDIT):
        return True

    # Some CODE_Q queries might involve changes
    if intent == IntentClass.CODE_Q and any(kw in query_lower for kw in ["implement", "create"]):
        return True

    return False


def _compute_confidence(query_lower: str, context_lower: str, intent: IntentClass) -> float:
    """Confidence in classification (0.0 - 1.0)."""
    # High confidence if multiple signals align
    signal_count = 0

    if intent == IntentClass.CODE_Q:
        signal_count += sum(1 for kw in _CODE_Q_KEYWORDS if kw in query_lower)
    elif intent == IntentClass.CODE_EDIT:
        signal_count += sum(1 for kw in _CODE_EDIT_KEYWORDS if kw in query_lower)
    elif intent == IntentClass.DEBUG:
        signal_count += sum(1 for kw in _DEBUG_KEYWORDS if kw in query_lower)
    elif intent == IntentClass.DOC_EDIT:
        signal_count += sum(1 for kw in _DOC_EDIT_KEYWORDS if kw in query_lower)
    elif intent == IntentClass.PLAN:
        signal_count += sum(1 for kw in _PLAN_KEYWORDS if kw in query_lower)
    elif intent == IntentClass.REVIEW:
        signal_count += sum(1 for kw in _REVIEW_KEYWORDS if kw in query_lower)

    # Scale: 0 signals = 0.5, 1 signal = 0.6, 2+ signals = 0.9+
    if signal_count == 0:
        confidence = 0.5
    elif signal_count == 1:
        confidence = 0.65
    else:
        confidence = min(1.0, 0.7 + (signal_count - 2) * 0.1)

    # Code context boosts confidence
    if _CODE_BLOCK_PATTERN.search(context_lower):
        confidence = min(1.0, confidence + 0.1)

    return round(confidence, 2)
