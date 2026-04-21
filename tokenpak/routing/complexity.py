# SPDX-License-Identifier: Apache-2.0
"""Task complexity scoring heuristic for TokenPak Shadow Mode.

Scores a query + context on a 0.0–10.0 scale and classifies it into
one of the TaskType categories. Pure regex/keyword — no LLM required.
"""

import re
from enum import Enum
from typing import List, Optional


class TaskType(str, Enum):
    CODING = "CODING"
    REASONING = "REASONING"
    SUMMARIZATION = "SUMMARIZATION"
    QA = "QA"
    CREATIVE = "CREATIVE"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Signal vocabularies
# ---------------------------------------------------------------------------

# Coding signals
_CODING_KEYWORDS = {
    "def",
    "function",
    "class",
    "import",
    "return",
    "variable",
    "loop",
    "array",
    "dict",
    "list",
    "tuple",
    "exception",
    "error",
    "bug",
    "implement",
    "code",
    "script",
    "algorithm",
    "api",
    "endpoint",
    "refactor",
    "optimize",
    "debug",
    "test",
    "unittest",
    "pytest",
    "compile",
    "build",
    "deploy",
    "fix",
    "method",
    "module",
    "package",
    "async",
    "await",
    "thread",
    "process",
    "query",
    "sql",
    "database",
    "parse",
    "serialize",
    "format",
    "lint",
}

# Reasoning / analysis signals
_REASONING_KEYWORDS = {
    "analyze",
    "compare",
    "evaluate",
    "assess",
    "tradeoff",
    "pros",
    "cons",
    "explain",
    "reason",
    "why",
    "because",
    "therefore",
    "conclude",
    "implication",
    "consequence",
    "cause",
    "effect",
    "difference",
    "similarity",
    "relationship",
    "architecture",
    "design",
    "strategy",
    "decision",
    "choose",
    "recommend",
    "suggest",
    "approach",
}

# Summarization signals
_SUMMARIZATION_KEYWORDS = {
    "summarize",
    "summary",
    "tldr",
    "tl;dr",
    "overview",
    "brief",
    "highlight",
    "key points",
    "main points",
    "recap",
    "digest",
    "condensed",
    "abstract",
    "synopsis",
}

# Q&A signals
_QA_KEYWORDS = {
    "what is",
    "what are",
    "how do",
    "how does",
    "how can",
    "when did",
    "where is",
    "who is",
    "which",
    "tell me",
    "show me",
    "find",
    "look up",
    "check",
    "verify",
    "confirm",
}

# Creative signals
_CREATIVE_KEYWORDS = {
    "write",
    "draft",
    "compose",
    "generate",
    "create",
    "brainstorm",
    "idea",
    "story",
    "email",
    "letter",
    "blog",
    "post",
    "tweet",
    "caption",
    "name",
    "slogan",
    "tagline",
    "pitch",
}

# Multi-step complexity signals
_MULTISTEP_PATTERNS = [
    re.compile(r"\bthen\b", re.IGNORECASE),
    re.compile(r"\balso\b", re.IGNORECASE),
    re.compile(r"\band then\b", re.IGNORECASE),
    re.compile(r"\bafter that\b", re.IGNORECASE),
    re.compile(r"\bfinally\b", re.IGNORECASE),
    re.compile(r"\bstep \d+\b", re.IGNORECASE),
    re.compile(r"\b(first|second|third|fourth|fifth)\b", re.IGNORECASE),
    re.compile(r"\badditionally\b", re.IGNORECASE),
    re.compile(r"\bmoreover\b", re.IGNORECASE),
    re.compile(r"\bfurthermore\b", re.IGNORECASE),
]

# Explicit high-complexity signals
_COMPLEXITY_BOOSTERS = [
    re.compile(r"\boptimize\b", re.IGNORECASE),
    re.compile(r"\brefactor\b", re.IGNORECASE),
    re.compile(r"\bdebug\b", re.IGNORECASE),
    re.compile(r"\barchitect\b", re.IGNORECASE),
    re.compile(r"\bdesign\b", re.IGNORECASE),
    re.compile(r"\bscale\b", re.IGNORECASE),
    re.compile(r"\bperformance\b", re.IGNORECASE),
    re.compile(r"\bsecurity\b", re.IGNORECASE),
    re.compile(r"\bmigrat\b", re.IGNORECASE),
    re.compile(r"\bintegrat\b", re.IGNORECASE),
    re.compile(r"\bdecompos\b", re.IGNORECASE),
    re.compile(r"\bimplement\b", re.IGNORECASE),
    re.compile(r"\banalyze\b", re.IGNORECASE),
    re.compile(r"\banalyse\b", re.IGNORECASE),
    re.compile(r"\brewrite\b", re.IGNORECASE),
    re.compile(r"\bmulti.step\b", re.IGNORECASE),
]

# Question depth: subordinate clause markers
_CLAUSE_MARKERS = re.compile(
    r"\b(if|when|while|since|because|although|unless|whether|that|which|who)\b",
    re.IGNORECASE,
)

# Code block detection in context
_CODE_FENCE = re.compile(r"```[\w]*\n", re.MULTILINE)
_INLINE_CODE = re.compile(r"`[^`]+`")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _word_set(text: str) -> set:
    """Lowercase word tokens from text."""
    return set(re.findall(r"\b\w+\b", text.lower()))


def score_complexity(query: str, context_blocks: Optional[List[str]] = None) -> tuple:
    """
    Score the complexity of a query + context.

    Args:
        query:          The user's query string.
        context_blocks: Optional list of context block content strings.

    Returns:
        Tuple of (complexity_score: float, task_type: TaskType)
        Score range: 0.0–10.0
    """
    context_blocks = context_blocks or []
    combined_context = "\n".join(context_blocks)
    query_lower = query.lower()
    words = _word_set(query)

    score = 0.0

    # --- Query length contribution (0.0–1.5) ---
    query_len = len(query.split())
    if query_len < 5:
        score += 0.0
    elif query_len < 15:
        score += 0.5
    elif query_len < 40:
        score += 1.0
    else:
        score += 1.5

    # --- Multi-step indicators (0.0–3.0) — count total occurrences ---
    multistep_hits = sum(len(p.findall(query)) for p in _MULTISTEP_PATTERNS)
    score += min(3.0, multistep_hits * 0.5)

    # --- Nested clause depth (0.0–1.5) ---
    clause_hits = len(_CLAUSE_MARKERS.findall(query))
    score += min(1.5, clause_hits * 0.3)

    # --- Explicit complexity boosters (0.0–2.0) ---
    booster_hits = sum(1 for p in _COMPLEXITY_BOOSTERS if p.search(query))
    score += min(2.0, booster_hits * 0.5)

    # --- Code in context (0.0–1.5) ---
    code_fences = len(_CODE_FENCE.findall(combined_context))
    inline_code = len(_INLINE_CODE.findall(query))
    if code_fences >= 3:
        score += 1.5
    elif code_fences >= 1:
        score += 1.0
    elif inline_code >= 2:
        score += 0.5

    # --- Context volume (0.0–1.0) ---
    context_words = len(combined_context.split())
    if context_words > 2000:
        score += 1.0
    elif context_words > 500:
        score += 0.5

    score = max(0.0, min(10.0, score))

    # --- Task type classification ---
    task_type = _classify_task_type(query_lower, words, combined_context)

    return round(score, 2), task_type


def _classify_task_type(query_lower: str, words: set, context: str) -> TaskType:
    """Classify into TaskType based on dominant signal."""
    scores = {
        TaskType.CODING: len(words & _CODING_KEYWORDS),
        TaskType.REASONING: len(words & _REASONING_KEYWORDS),
        TaskType.SUMMARIZATION: len(words & _SUMMARIZATION_KEYWORDS),
        TaskType.QA: sum(1 for kw in _QA_KEYWORDS if kw in query_lower),
        TaskType.CREATIVE: len(words & _CREATIVE_KEYWORDS),
    }

    # Coding context boost
    if "```" in context or re.search(r"\.(py|js|ts|go|rs|java|cpp)\b", query_lower):
        scores[TaskType.CODING] += 3

    # Summarization boost if context is long
    if len(context.split()) > 1000:
        scores[TaskType.SUMMARIZATION] += 1

    best_type = max(scores, key=lambda t: scores[t])
    if scores[best_type] == 0:
        return TaskType.UNKNOWN
    return best_type
