"""
salience.code_extractor — Extract high-signal sections from source code.

Strategy
--------
1. Parse function / method / class definitions → keep bodies of *changed*
   functions (identified by diff markers or heuristic change indicators).
2. Keep all import / require / use statements (dependency map).
3. Keep test targets (functions matching ``test_*`` / ``it("…")`` patterns
   when they contain assertion failures or FAIL markers).
4. Return a compact representation ordered: imports → changed fns → test targets.

Language support
----------------
Python, JavaScript/TypeScript, Java, Go, Rust — detected from content
patterns; no file-extension metadata required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Set, Tuple

# ── constants ─────────────────────────────────────────────────────────────

MAX_FN_BODY_LINES: int = 60  # cap per function body to avoid huge dumps

# ── regex patterns ────────────────────────────────────────────────────────

# Python
_PY_FN_RE = re.compile(r"^(?P<indent>\s*)(?:async\s+)?def\s+(?P<name>\w+)\s*\(", re.MULTILINE)
_PY_CLASS_RE = re.compile(r"^(?P<indent>\s*)class\s+(?P<name>\w+)", re.MULTILINE)

# JS / TS
_JS_FN_RE = re.compile(
    r"^(?P<indent>\s*)(?:export\s+)?(?:async\s+)?(?:function\s+(?P<name>\w+)|(?:const|let|var)\s+(?P<name2>\w+)\s*=\s*(?:async\s+)?(?:function|\())",
    re.MULTILINE,
)
_JS_ARROW_RE = re.compile(
    r"^(?P<indent>\s*)(?:export\s+)?(?:const|let)\s+(?P<name>\w+)\s*=\s*(?:async\s+)?\(",
    re.MULTILINE,
)

# Java / Kotlin
_JAVA_FN_RE = re.compile(
    r"^(?P<indent>\s*)(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(?P<name>\w+)\s*\(",
    re.MULTILINE,
)

# Go
_GO_FN_RE = re.compile(
    r"^(?P<indent>)func\s+(?:\(\w+\s+\*?\w+\)\s+)?(?P<name>\w+)\s*\(", re.MULTILINE
)

# Rust
_RUST_FN_RE = re.compile(
    r"^(?P<indent>\s*)(?:pub(?:\(\w+\))?\s+)?(?:async\s+)?fn\s+(?P<name>\w+)\s*\(", re.MULTILINE
)

# Import statements (multi-language)
_IMPORT_RE = re.compile(
    r"^\s*(?:import\s|from\s\S+\simport|require\s*\(|use\s+\w|#include\s|using\s+)", re.MULTILINE
)

# Diff change markers
_DIFF_ADDED_RE = re.compile(r"^\+(?!\+\+)", re.MULTILINE)
_DIFF_REMOVED_RE = re.compile(r"^-(?!--)", re.MULTILINE)

# Test function markers
_TEST_FN_RE = re.compile(
    r"(?:def\s+test_\w+|it\s*\(\s*[\"']|test\s*\(\s*[\"']|\bTest\w+\s*\{|#\[test\])",
    re.MULTILINE,
)

# Failure signals inside function bodies
_FAILURE_RE = re.compile(
    r"\b(?:FAIL|AssertionError|assert\s+False|expect.*toBe|panic!|assert_eq!|t\.Error|t\.Fatal)\b",
    re.IGNORECASE,
)

# Change signals in a function body (non-diff mode)
_CHANGE_SIGNAL_RE = re.compile(
    r"#\s*changed|//\s*changed|/\*\s*changed|\bTODO\b|\bFIXME\b|\bHACK\b|\bXXX\b",
    re.IGNORECASE,
)


@dataclass
class CodeExtractionResult:
    lines_in: int = 0
    lines_out: int = 0
    imports_found: int = 0
    functions_found: int = 0
    changed_functions: List[str] = field(default_factory=list)
    test_targets: List[str] = field(default_factory=list)
    is_diff: bool = False
    extracted: str = ""

    @property
    def reduction_pct(self) -> float:
        if self.lines_in == 0:
            return 0.0
        return round((1 - self.lines_out / self.lines_in) * 100, 1)


class CodeExtractor:
    """
    Extract high-signal sections from source code text.

    Parameters
    ----------
    max_fn_body_lines : int
        Maximum lines to include per function body.
    include_all_fns : bool
        If True, include all detected functions (not just changed ones).
        Useful for small files.
    """

    def __init__(
        self,
        max_fn_body_lines: int = MAX_FN_BODY_LINES,
        include_all_fns: bool = False,
    ) -> None:
        self.max_fn_body_lines = max_fn_body_lines
        self.include_all_fns = include_all_fns

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, text: str) -> CodeExtractionResult:
        """Return a :class:`CodeExtractionResult` for *text*."""
        lines = text.splitlines()
        result = CodeExtractionResult(lines_in=len(lines))

        # Detect if input is a unified diff
        result.is_diff = bool(_DIFF_ADDED_RE.search(text)) and bool(_DIFF_REMOVED_RE.search(text))

        # Collect imports
        import_lines = [l for l in lines if _IMPORT_RE.match(l)]
        result.imports_found = len(import_lines)

        # Detect all function blocks
        fn_blocks = self._extract_fn_blocks(text, lines)
        result.functions_found = len(fn_blocks)

        # Determine which functions are "changed"
        changed: List[Tuple[str, List[str]]] = []
        test_fns: List[Tuple[str, List[str]]] = []

        for name, body_lines, pre_lines in fn_blocks:
            body_text = "\n".join(body_lines)
            pre_text = "\n".join(pre_lines)
            is_changed = (
                self.include_all_fns
                or (result.is_diff and bool(_DIFF_ADDED_RE.search(body_text)))
                or bool(_CHANGE_SIGNAL_RE.search(body_text))
                or bool(_CHANGE_SIGNAL_RE.search(pre_text))
            )
            is_test = bool(_TEST_FN_RE.search(f"def {name}") or _TEST_FN_RE.search(name))
            has_failure = bool(_FAILURE_RE.search(body_text))

            if is_test and has_failure:
                test_fns.append((name, body_lines))
            elif is_changed:
                changed.append((name, body_lines))

        # de-dup test_fns by name (can be detected twice via different regexes)
        seen_test_names: Set[str] = set()
        test_fns = [
            (n, b) for n, b in test_fns if not (n in seen_test_names or seen_test_names.add(n))
        ]  # type: ignore[func-returns-value]

        result.changed_functions = [n for n, _ in changed]
        result.test_targets = [n for n, _ in test_fns]

        # Build output
        sections: List[str] = []

        # Header
        sections.append(
            f"[code-salience] {result.lines_in} lines  "
            f"imports={result.imports_found}  "
            f"fns={result.functions_found}  "
            f"changed={len(changed)}  "
            f"failing_tests={len(test_fns)}"
        )

        # Imports
        if import_lines:
            sections.append("")
            sections.append("# --- imports ---")
            sections.extend(import_lines)

        # Changed functions
        for name, body_lines in changed:
            sections.append("")
            sections.append(f"# --- fn: {name} ---")
            sections.extend(body_lines[: self.max_fn_body_lines])
            if len(body_lines) > self.max_fn_body_lines:
                sections.append(
                    f"    … ({len(body_lines) - self.max_fn_body_lines} lines truncated)"
                )

        # Failing test targets
        for name, body_lines in test_fns:
            sections.append("")
            sections.append(f"# --- failing test: {name} ---")
            sections.extend(body_lines[: self.max_fn_body_lines])

        result.extracted = "\n".join(sections)
        result.lines_out = len(sections)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    _PRE_WINDOW = 5  # lines before fn def to scan for change signals

    def _extract_fn_blocks(
        self, text: str, lines: List[str]
    ) -> List[Tuple[str, List[str], List[str]]]:
        """
        Return list of (name, body_lines, pre_lines) for each detected function/method.
        pre_lines is the small window of lines before the def (for change signal scanning).
        Works across Python, JS/TS, Java, Go, Rust.
        """
        # Combine all fn-detecting regexes into one pass
        candidates: List[Tuple[int, str, int]] = []  # (line_idx, name, indent_len)

        for pattern in (_PY_FN_RE, _PY_CLASS_RE, _JAVA_FN_RE, _GO_FN_RE, _RUST_FN_RE):
            for m in pattern.finditer(text):
                line_idx = text[: m.start()].count("\n")
                indent = len(m.group("indent"))
                name = m.group("name")
                candidates.append((line_idx, name, indent))

        # JS arrow / function expressions
        for pattern in (_JS_FN_RE, _JS_ARROW_RE):
            for m in pattern.finditer(text):
                line_idx = text[: m.start()].count("\n")
                indent = len(m.group("indent"))
                name = m.group("name") if m.group("name") else (m.group("name2") or "anonymous")
                candidates.append((line_idx, name, indent))

        if not candidates:
            return []

        # De-dup by (line_idx, name) — multiple regexes can hit the same line
        seen_def: Set[Tuple[int, str]] = set()
        unique: List[Tuple[int, str, int]] = []
        for c in candidates:
            key = (c[0], c[1])
            if key not in seen_def:
                seen_def.add(key)
                unique.append(c)

        # Sort by line number
        unique.sort(key=lambda x: x[0])

        # Extract body: from definition line to next definition at same indent
        blocks: List[Tuple[str, List[str], List[str]]] = []
        for i, (start_idx, name, indent) in enumerate(unique):
            # End: next candidate at same or lesser indent
            end_idx = len(lines)
            for j in range(i + 1, len(unique)):
                next_idx, _, next_indent = unique[j]
                if next_indent <= indent and next_idx > start_idx:
                    end_idx = next_idx
                    break
            body = lines[start_idx:end_idx]
            pre_start = max(0, start_idx - self._PRE_WINDOW)
            pre = lines[pre_start:start_idx]
            blocks.append((name, body, pre))

        return blocks
