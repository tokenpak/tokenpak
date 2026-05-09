"""Protected-span detection for the route-class compression policy (TIP-05).

A *protected span* is a slice of request text that compression must preserve
byte-for-byte. The proposal (Phase 3 Component D) lists fifteen span types
that are non-negotiable for code/diff/log/config/error content. This module
provides:

    SpanType            — string constants for the canonical span type names
    ProtectedSpan       — (start, end, span_type) triple, half-open range
    ProtectedSpanDetector — runs the registered detectors over text
    detect_protected_spans(text, types=...) — convenience function

Each detector is a regex-based finder that returns ``[(start, end), ...]``
ranges for one span type. The detectors are intentionally conservative:
when in doubt, mark a region as protected. False positives cost a few bytes
of compression headroom; false negatives can corrupt code or stack traces.

Layer: services-level. No I/O, no proxy state, no platform-specific logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Span type constants (proposal Phase 3 Component D)
# ---------------------------------------------------------------------------


class SpanType:
    """Canonical protected-span type identifiers.

    These match the proposal's "Protected Span Types" list verbatim. Keep
    them as strings (not an Enum) so adapters can declare extra types without
    monkey-patching an Enum.
    """

    FILE_PATH = "file_path"
    FUNCTION_SIGNATURE = "function_signature"
    CLASS_SIGNATURE = "class_signature"
    COMMAND = "command"
    EXIT_CODE = "exit_code"
    STACK_TRACE_FRAME = "stack_trace_frame"
    EXCEPTION_MESSAGE = "exception_message"
    JSON_SCHEMA = "json_schema"
    YAML_KEY = "yaml_key"
    CONFIG_VALUE = "config_value"
    LINE_NUMBER = "line_number"
    DIFF_HUNK_HEADER = "diff_hunk_header"
    DIFF_ADDED_REMOVED_LINES = "diff_added_removed_lines"
    URL = "url"
    CREDENTIAL_PLACEHOLDER = "credential_placeholder"


ALL_SPAN_TYPES: FrozenSet[str] = frozenset({
    SpanType.FILE_PATH,
    SpanType.FUNCTION_SIGNATURE,
    SpanType.CLASS_SIGNATURE,
    SpanType.COMMAND,
    SpanType.EXIT_CODE,
    SpanType.STACK_TRACE_FRAME,
    SpanType.EXCEPTION_MESSAGE,
    SpanType.JSON_SCHEMA,
    SpanType.YAML_KEY,
    SpanType.CONFIG_VALUE,
    SpanType.LINE_NUMBER,
    SpanType.DIFF_HUNK_HEADER,
    SpanType.DIFF_ADDED_REMOVED_LINES,
    SpanType.URL,
    SpanType.CREDENTIAL_PLACEHOLDER,
})


@dataclass(frozen=True)
class ProtectedSpan:
    """A half-open range ``[start, end)`` of text marked protected."""

    start: int
    end: int
    span_type: str

    def overlaps(self, other: "ProtectedSpan") -> bool:
        return self.start < other.end and other.start < self.end


# ---------------------------------------------------------------------------
# Regex-based detectors. Each returns a list of (start, end) tuples.
# ---------------------------------------------------------------------------


# File path: absolute, relative, or home-relative; ends with a 1–8-char extension.
# Three forms:
#   - leading anchor + name.ext     (e.g. /foo.py, ~/notes.md, ./script.sh)
#   - segments + name.ext           (e.g. /etc/foo.cfg, ./recipes/x.yaml)
#   - bare relative segments + ext  (e.g. recipes/oss/x.yaml)
_RE_FILE_PATH = re.compile(
    r"(?<![\w.])"
    r"(?:"
    r"(?:[A-Za-z]:[\\/]|/|\.{1,2}/|~/)(?:[\w.-]+[\\/])*[\w.-]+\.[A-Za-z][\w]{0,7}"
    r"|"
    r"(?:[\w.-]+[\\/])+[\w.-]+\.[A-Za-z][\w]{0,7}"
    r")"
    r"(?![\w.])"
)

# Python `def name(args) -> ret:` and JS-style `function name(args)`.
_RE_PY_FUNCTION_SIG = re.compile(
    r"\b(?:async\s+)?def\s+\w+\s*\([^)]*\)\s*(?:->\s*[\w\[\],\s.|]+)?\s*:",
    re.MULTILINE,
)
_RE_JS_FUNCTION_SIG = re.compile(
    r"\bfunction\s+\w+\s*\([^)]*\)",
)

# Python class signature.
_RE_CLASS_SIG = re.compile(r"\bclass\s+\w+(?:\s*\([^)]*\))?\s*:")

# Shell command lines (very conservative — only `$ ` or `# ` prompts).
_RE_COMMAND = re.compile(r"^[ \t]*[\$#]\s+\S[^\n]*", re.MULTILINE)

# Exit code mentions (English + structured forms).
_RE_EXIT_CODE = re.compile(
    r"\b(?:exit\s*code|returncode|return\s*code|status|exit)\s*[:=]?\s*-?\d+\b",
    re.IGNORECASE,
)

# Python `File "x.py", line N, in foo` and JS `at name (x.js:1:1)`.
_RE_PY_STACK_FRAME = re.compile(
    r'File\s+"[^"\n]+",\s+line\s+\d+(?:,\s+in\s+\S+)?'
)
_RE_JS_STACK_FRAME = re.compile(
    r"\bat\s+\S[^\n(]*\([^)\n]+:\d+:\d+\)"
)

# Python exception line — at start of a line: `<TypeName>(Error|Exception): message`.
# Lazy quantifier so the suffix anchor (Error/Exception/Warning) wins.
_RE_EXCEPTION_MESSAGE = re.compile(
    r"^[A-Z][A-Za-z0-9_.]*?(?:Error|Exception|Warning)\s*:\s*[^\n]+",
    re.MULTILINE,
)

# Lightweight JSON object / array detection — top-level braces or brackets
# on a line are protected. We deliberately skip nested matching; the goal is
# "don't compress what looks like structured config", not "parse JSON".
_RE_JSON_BLOCK = re.compile(r"\{[^{}\n]*\}|\[[^\[\]\n]*\]")

# YAML key: indentation-aware; matches lines like `  foo:` or `bar: value`.
_RE_YAML_KEY = re.compile(r"^[ \t]*[A-Za-z_][\w.-]*\s*:(?:\s|$)", re.MULTILINE)

# Config-style key=value lines (INI/env style).
_RE_CONFIG_VALUE = re.compile(
    r"^[ \t]*[A-Z_][A-Z0-9_]*\s*=\s*[^\n]+", re.MULTILINE
)

# Line numbers — `line 42`, `line: 100`, or `:42:` style references.
_RE_LINE_NUMBER = re.compile(
    r"\bline\s*[#:]?\s*\d+\b|\b\d+:\d+\b",
    re.IGNORECASE,
)

# Diff hunk header.
_RE_DIFF_HUNK_HEADER = re.compile(
    r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@.*$",
    re.MULTILINE,
)

# Diff content lines: a line beginning with `+` or `-` but not `+++`/`---`.
_RE_DIFF_CONTENT_LINE = re.compile(
    r"^[+-](?![+-])[^\n]*", re.MULTILINE
)

# URL with scheme.
_RE_URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+\-.]*://[^\s)>\]\"']+")

# Credential placeholder forms.
_RE_CREDENTIAL_PLACEHOLDER = re.compile(
    r"\$\{[A-Z_][A-Z0-9_]*\}"
    r"|<[A-Z_]{2,}>"
    r"|x{3,}_REDACTED_x{3,}"
    r"|\*{6,}"
)


_DETECTORS: Dict[str, re.Pattern] = {}
_MULTI_DETECTORS: Dict[str, Tuple[re.Pattern, ...]] = {
    SpanType.FUNCTION_SIGNATURE: (_RE_PY_FUNCTION_SIG, _RE_JS_FUNCTION_SIG),
    SpanType.STACK_TRACE_FRAME: (_RE_PY_STACK_FRAME, _RE_JS_STACK_FRAME),
}
_DETECTORS[SpanType.FILE_PATH] = _RE_FILE_PATH
_DETECTORS[SpanType.CLASS_SIGNATURE] = _RE_CLASS_SIG
_DETECTORS[SpanType.COMMAND] = _RE_COMMAND
_DETECTORS[SpanType.EXIT_CODE] = _RE_EXIT_CODE
_DETECTORS[SpanType.EXCEPTION_MESSAGE] = _RE_EXCEPTION_MESSAGE
_DETECTORS[SpanType.JSON_SCHEMA] = _RE_JSON_BLOCK
_DETECTORS[SpanType.YAML_KEY] = _RE_YAML_KEY
_DETECTORS[SpanType.CONFIG_VALUE] = _RE_CONFIG_VALUE
_DETECTORS[SpanType.LINE_NUMBER] = _RE_LINE_NUMBER
_DETECTORS[SpanType.DIFF_HUNK_HEADER] = _RE_DIFF_HUNK_HEADER
_DETECTORS[SpanType.DIFF_ADDED_REMOVED_LINES] = _RE_DIFF_CONTENT_LINE
_DETECTORS[SpanType.URL] = _RE_URL
_DETECTORS[SpanType.CREDENTIAL_PLACEHOLDER] = _RE_CREDENTIAL_PLACEHOLDER


def _run_detector(text: str, span_type: str) -> List[ProtectedSpan]:
    multi = _MULTI_DETECTORS.get(span_type)
    if multi:
        out: List[ProtectedSpan] = []
        for pat in multi:
            for m in pat.finditer(text):
                out.append(ProtectedSpan(m.start(), m.end(), span_type))
        return out
    pat = _DETECTORS.get(span_type)
    if not pat:
        return []
    return [
        ProtectedSpan(m.start(), m.end(), span_type)
        for m in pat.finditer(text)
    ]


def merge_overlapping(spans: Iterable[ProtectedSpan]) -> List[ProtectedSpan]:
    """Sort + merge overlapping spans. Span_type collisions take the first.

    The output is ordered by ``start`` and contains no overlaps. When two
    spans overlap they collapse into one whose ``span_type`` is whichever
    sorted first by start (ties broken by earlier-detected type).
    """
    seq = sorted(spans, key=lambda s: (s.start, s.end))
    if not seq:
        return []
    merged: List[ProtectedSpan] = [seq[0]]
    for s in seq[1:]:
        last = merged[-1]
        if s.start < last.end:
            if s.end > last.end:
                merged[-1] = ProtectedSpan(last.start, s.end, last.span_type)
        else:
            merged.append(s)
    return merged


def detect_protected_spans(
    text: str,
    *,
    types: Optional[Iterable[str]] = None,
) -> List[ProtectedSpan]:
    """Run the requested detectors and return merged, ordered spans.

    types: iterable of SpanType strings. ``None`` runs every detector.
    """
    if types is None:
        active: FrozenSet[str] = ALL_SPAN_TYPES
    else:
        active = frozenset(t for t in types if t in ALL_SPAN_TYPES)
    out: List[ProtectedSpan] = []
    for span_type in active:
        out.extend(_run_detector(text, span_type))
    return merge_overlapping(out)


def text_is_protected(
    text: str,
    *,
    types: Optional[Iterable[str]] = None,
) -> bool:
    """Convenience: True if ANY span of the requested types matches."""
    if types is None:
        active = ALL_SPAN_TYPES
    else:
        active = frozenset(t for t in types if t in ALL_SPAN_TYPES)
    for span_type in active:
        multi = _MULTI_DETECTORS.get(span_type)
        if multi:
            for pat in multi:
                if pat.search(text):
                    return True
            continue
        pat = _DETECTORS.get(span_type)
        if pat and pat.search(text):
            return True
    return False


# ---------------------------------------------------------------------------
# Span-aware text rewrite helpers
# ---------------------------------------------------------------------------


def rewrite_outside_spans(
    text: str,
    spans: List[ProtectedSpan],
    rewrite: Callable[[str], str],
) -> str:
    """Apply ``rewrite`` to every region NOT inside a protected span.

    Spans must be sorted and non-overlapping (the output of
    ``merge_overlapping``). Protected segments are emitted byte-for-byte.
    The non-protected segments are passed through ``rewrite`` and the
    result concatenated; rewrite must be a pure function over the segment
    string.
    """
    if not spans:
        return rewrite(text)
    out: List[str] = []
    cursor = 0
    for span in spans:
        if span.start > cursor:
            out.append(rewrite(text[cursor:span.start]))
        out.append(text[span.start:span.end])
        cursor = span.end
    if cursor < len(text):
        out.append(rewrite(text[cursor:]))
    return "".join(out)


def protected_byte_count(spans: List[ProtectedSpan]) -> int:
    """Total bytes covered by the (already-merged) span list."""
    return sum(s.end - s.start for s in spans)


__all__ = [
    "SpanType",
    "ALL_SPAN_TYPES",
    "ProtectedSpan",
    "detect_protected_spans",
    "text_is_protected",
    "merge_overlapping",
    "rewrite_outside_spans",
    "protected_byte_count",
]
