# SPDX-License-Identifier: Apache-2.0
"""`tokenpak compress` — compress text/JSON/code and show savings.

Free-tier feature (L5). Runs fully offline — no proxy dependency. Useful
for developers to preview how tokenpak compresses arbitrary content before
wiring the proxy into their workflow.

What it does:
    - Read content from --file or stdin.
    - Run HeuristicEngine (rule-based, no ML deps) with sensible defaults.
    - If input is a JSON list of Anthropic-style messages, also run
      `dedup_messages` to strip exact/near-duplicate turns.
    - Report original vs compressed size + token delta.
    - --verbose prints the compressed output itself.
    - --json emits a structured report for downstream tooling.

Pro-compatibility hook: the compression engine is pluggable — a Pro tier
can register `LLMLinguaEngine` or Pro-specific processors via the existing
``tokenpak/compression/engines/__init__.py`` registry and this command
picks it up without further changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, cast


@dataclass
class CompressReport:
    source: str = "<stdin>"
    engine: str = "heuristic"
    original_chars: int = 0
    original_tokens: int = 0
    compressed_chars: int = 0
    compressed_tokens: int = 0
    compressed: str = ""  # included in JSON; kept out of text summary unless verbose
    dedup_turns_removed: int = 0  # non-zero only when input was a messages list

    @property
    def chars_saved(self) -> int:
        return max(0, self.original_chars - self.compressed_chars)

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)

    @property
    def pct_saved(self) -> float:
        if self.original_chars <= 0:
            return 0.0
        return 100.0 * self.chars_saved / self.original_chars

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["chars_saved"] = self.chars_saved
        d["tokens_saved"] = self.tokens_saved
        d["pct_saved"] = round(self.pct_saved, 1)
        return d


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _try_parse_messages(text: str) -> Optional[list[dict[str, Any]]]:
    """Return a messages list if the input is a JSON list of {role,content}."""
    s = text.strip()
    if not s or s[0] not in "[{":
        return None
    try:
        data = json.loads(s)
    except Exception:
        return None
    if isinstance(data, list) and all(
        isinstance(m, dict) and "role" in m and "content" in m for m in data
    ):
        return cast(list[dict[str, Any]], data)
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        msgs = data["messages"]
        if all(isinstance(m, dict) and "role" in m and "content" in m for m in msgs):
            return cast(list[dict[str, Any]], msgs)
    return None


def _messages_to_text(messages: list[dict[str, Any]]) -> str:
    """Flatten messages back to plain text for token/char counting."""
    return json.dumps(messages, ensure_ascii=False, indent=2)


def compress(text: str, source: str = "<stdin>") -> CompressReport:
    """Run the compression pipeline on arbitrary content.

    Chooses between two paths based on the input shape:
      - JSON messages list → dedup then heuristic compact (conservative)
      - plain text         → heuristic compact only
    """
    report = CompressReport(source=source)
    report.original_chars = len(text)
    report.original_tokens = _estimate_tokens(text)

    # Lazy import so --help stays fast on cold starts.
    from tokenpak.compression.engines.heuristic import HeuristicEngine

    messages = _try_parse_messages(text)
    if messages is not None:
        try:
            from tokenpak.compression.dedup import dedup_messages

            deduped = dedup_messages(messages)
            report.dedup_turns_removed = max(0, len(messages) - len(deduped))
        except Exception:
            deduped = messages
        report.compressed = _messages_to_text(deduped)
    else:
        engine = HeuristicEngine()
        try:
            report.compressed = engine.compact(text)
            report.engine = engine.name
        except Exception as exc:
            # Fail-soft: show the original so the caller doesn't get a stack trace.
            report.compressed = text
            report.engine = f"heuristic (error: {exc})"

    report.compressed_chars = len(report.compressed)
    report.compressed_tokens = _estimate_tokens(report.compressed)
    return report


def _render_text(report: CompressReport, verbose: bool) -> str:
    lines: list[str] = [""]
    lines.append("  TOKENPAK compress")
    lines.append("  " + "─" * 40)
    lines.append(f"  Source       {report.source}")
    lines.append(f"  Engine       {report.engine}")
    lines.append(
        f"  Original     {report.original_chars:,} chars  (~{report.original_tokens:,} tokens)"
    )
    lines.append(
        f"  Compressed   {report.compressed_chars:,} chars  (~{report.compressed_tokens:,} tokens)"
    )
    if report.dedup_turns_removed:
        lines.append(f"  Dedup        -{report.dedup_turns_removed} duplicate turn(s) removed")
    lines.append(
        f"  Saved        {report.chars_saved:,} chars  "
        f"({report.tokens_saved:,} tokens, {report.pct_saved:.1f}%)"
    )
    lines.append("")
    if verbose:
        lines.append("  " + "─" * 40)
        lines.append("  Compressed output:")
        lines.append("")
        # Indent for readability
        for ln in report.compressed.splitlines()[:200]:
            lines.append("    " + ln)
        if len(report.compressed.splitlines()) > 200:
            lines.append("    … (truncated — full output available via --json)")
        lines.append("")
    return "\n".join(lines)


def run_compress(args: argparse.Namespace) -> int:
    """CLI handler."""
    # Read input
    if getattr(args, "file", None):
        try:
            text = Path(args.file).read_text(encoding="utf-8")
            source = str(Path(args.file))
        except FileNotFoundError as e:
            print(f"compress: file not found: {e}", file=sys.stderr)
            return 2
    else:
        text = sys.stdin.read()
        source = "<stdin>"

    if not text.strip():
        print("compress: empty input — nothing to compress", file=sys.stderr)
        return 1

    report = compress(text, source=source)

    if getattr(args, "json", False) or getattr(args, "as_json", False):
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(_render_text(report, verbose=getattr(args, "verbose", False)))
    return 0
