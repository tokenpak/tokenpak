"""
salience.detect — lightweight content-type detection.

No external dependencies; purely heuristic (pattern scoring).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class ContentType(str, Enum):
    LOG = "log"
    CODE = "code"
    DOC = "doc"
    UNKNOWN = "unknown"


# ── signal patterns ────────────────────────────────────────────────────────

_LOG_PATTERNS = [
    r"\b(?:ERROR|FATAL|CRITICAL|EXCEPTION|WARN(?:ING)?|INFO|DEBUG)\b",
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}",
    r"Traceback \(most recent call last\)",
    r"at \w+\.\w+\(.+:\d+\)",
    r"\[?\d{2}/\w{3}/\d{4}[: ]\d{2}:\d{2}",
    r"^\s*\d+\s+(?:ERROR|WARN|INFO)",
]

_CODE_PATTERNS = [
    r"^\s*(?:def |class |async def )",
    r"^\s*(?:function |const |let |var |export )",
    r"^\s*(?:import |from \S+ import )",
    r"^\s*(?:public|private|protected)\s+(?:static\s+)?[\w<>]+\s+\w+\s*\(",
    r"^\s*(?:#include|#define|#pragma)",
    r"^\s*(?:fn |impl |use |mod |pub |struct |enum )\w",
    r"(?:=>|->)\s*[\{\[]",
]

_DOC_PATTERNS = [
    r"^#{1,6}\s",
    r"^\*{1,2}\w",
    r"\b(?:TODO|FIXME|NOTE|HACK|XXX)\b",
    r"^\s*[-*+]\s+\S",
    r"^>\s+",
    r"\[.+\]\(https?://",
]


def _score(text: str, patterns: list[str]) -> int:
    score = 0
    for pat in patterns:
        if re.search(pat, text, re.MULTILINE | re.IGNORECASE):
            score += 1
    return score


def detect_content_type(text: str) -> ContentType:
    """Return the most likely :class:`ContentType` for *text*."""
    scores = {
        ContentType.LOG: _score(text, _LOG_PATTERNS),
        ContentType.CODE: _score(text, _CODE_PATTERNS),
        ContentType.DOC: _score(text, _DOC_PATTERNS),
    }
    best_type, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score == 0:
        return ContentType.UNKNOWN
    return best_type
