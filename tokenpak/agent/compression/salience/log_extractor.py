"""
salience.log_extractor — Extract high-signal lines from log output.

Strategy
--------
1. Identify ERROR / FATAL / EXCEPTION lines → keep ±CONTEXT_LINES neighbours.
2. Capture unique stack-trace signatures (first unique "at …" or "File …" line
   per distinct error cluster).
3. Record timestamp range (first / last detected timestamp).
4. Return a compact representation that preserves all actionable signal while
   dropping repetitive INFO / DEBUG chatter.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

# ── tuneable constants ────────────────────────────────────────────────────

CONTEXT_LINES: int = 20          # lines before/after each error anchor
MAX_STACK_SIGS: int = 30         # de-dup cap for unique stack signatures

# ── regex patterns ────────────────────────────────────────────────────────

_ERROR_RE = re.compile(
    r"\b(?:ERROR|FATAL|CRITICAL|EXCEPTION|SEVERE)\b", re.IGNORECASE
)
_WARN_RE = re.compile(r"\b(?:WARN(?:ING)?)\b", re.IGNORECASE)

# Java / Python / JS stack frames
_STACK_FRAME_RE = re.compile(
    r"""
    (?:
        \s+at\s+[\w.$<>]+\([\w.$:]+(?::\d+)?\)  # Java
      | \s+File\s+"[^"]+",\s+line\s+\d+          # Python
      | \s+at\s+\S+\s+\(\S+:\d+:\d+\)            # Node.js
      | \s+at\s+\S+:\d+:\d+                       # short Node.js
    )
    """,
    re.VERBOSE,
)

# Timestamp patterns: ISO-8601, Apache/nginx, epoch-ms, simple HH:MM:SS
_TS_RE = re.compile(
    r"""
    (?:
        \d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?
      | \d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2}
      | \d{10,13}                                  # unix / epoch-ms
      | \d{2}:\d{2}:\d{2}(?:[.,]\d+)?
    )
    """,
    re.VERBOSE,
)


@dataclass
class LogExtractionResult:
    lines_in: int = 0
    lines_out: int = 0
    error_count: int = 0
    warn_count: int = 0
    unique_stack_sigs: int = 0
    timestamp_first: Optional[str] = None
    timestamp_last: Optional[str] = None
    extracted: str = ""

    @property
    def reduction_pct(self) -> float:
        if self.lines_in == 0:
            return 0.0
        return round((1 - self.lines_out / self.lines_in) * 100, 1)


class LogExtractor:
    """
    Extract high-signal content from log text.

    Parameters
    ----------
    context_lines : int
        Number of lines to keep before/after each error anchor.
    max_stack_sigs : int
        Maximum unique stack signatures to include.
    include_warnings : bool
        If True, WARN lines are also treated as anchors (lower priority).
    """

    def __init__(
        self,
        context_lines: int = CONTEXT_LINES,
        max_stack_sigs: int = MAX_STACK_SIGS,
        include_warnings: bool = False,
    ) -> None:
        self.context_lines = context_lines
        self.max_stack_sigs = max_stack_sigs
        self.include_warnings = include_warnings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, text: str) -> LogExtractionResult:
        """Return a :class:`LogExtractionResult` for *text*."""
        lines = text.splitlines()
        result = LogExtractionResult(lines_in=len(lines))

        # Pass 1: collect timestamps + counts
        timestamps: List[str] = []
        for line in lines:
            if _ERROR_RE.search(line):
                result.error_count += 1
            elif _WARN_RE.search(line):
                result.warn_count += 1
            ts_match = _TS_RE.search(line)
            if ts_match:
                timestamps.append(ts_match.group(0))

        if timestamps:
            result.timestamp_first = timestamps[0]
            result.timestamp_last = timestamps[-1]

        # Pass 2: identify keep-set (indices)
        keep: Set[int] = set()
        for idx, line in enumerate(lines):
            is_error = bool(_ERROR_RE.search(line))
            is_warn = self.include_warnings and bool(_WARN_RE.search(line))
            if is_error or is_warn:
                lo = max(0, idx - self.context_lines)
                hi = min(len(lines), idx + self.context_lines + 1)
                keep.update(range(lo, hi))

        # Pass 3: unique stack signatures
        seen_sigs: Set[str] = set()
        stack_sig_lines: List[Tuple[int, str]] = []
        for idx, line in enumerate(lines):
            if _STACK_FRAME_RE.match(line):
                sig = self._stack_signature(line)
                if sig not in seen_sigs and len(seen_sigs) < self.max_stack_sigs:
                    seen_sigs.add(sig)
                    stack_sig_lines.append((idx, line))

        result.unique_stack_sigs = len(seen_sigs)

        # Add unique stack frame lines to keep-set
        for idx, _ in stack_sig_lines:
            keep.add(idx)

        # Build output
        if not keep:
            # Nothing salient found — return minimal summary header
            result.extracted = self._summary_header(result, lines)
            result.lines_out = result.extracted.count("\n") + 1
            return result

        sorted_keep = sorted(keep)
        output_lines: List[str] = [self._summary_header(result, lines), ""]

        prev_idx = -2
        for idx in sorted_keep:
            if idx - prev_idx > 1:
                output_lines.append("…")
            output_lines.append(lines[idx])
            prev_idx = idx

        result.extracted = "\n".join(output_lines)
        result.lines_out = len(output_lines)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _stack_signature(line: str) -> str:
        """Deterministic fingerprint for a stack frame line."""
        normalised = re.sub(r"\$\d+", "", line.strip())
        normalised = re.sub(r"line \d+", "line N", normalised)
        normalised = re.sub(r":\d+\)", ":N)", normalised)
        normalised = re.sub(r":\d+:\d+\)", ":N:N)", normalised)
        return hashlib.md5(normalised.encode(), usedforsecurity=False).hexdigest()[:12]

    @staticmethod
    def _summary_header(result: "LogExtractionResult", lines: List[str]) -> str:
        parts = [
            f"[log-salience] {result.lines_in} lines → extracted",
            f"errors={result.error_count}",
            f"warns={result.warn_count}",
            f"unique_stack_sigs={result.unique_stack_sigs}",
        ]
        if result.timestamp_first:
            parts.append(f"ts_range=[{result.timestamp_first} … {result.timestamp_last}]")
        return "  ".join(parts)
