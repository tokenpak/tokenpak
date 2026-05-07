# SPDX-License-Identifier: Apache-2.0
"""`tokenpak optimize --file <path>` — analyze a prompt for token-reduction.

Free-tier feature (L21). The analyzer is a read-only linter that surfaces
reword candidates — it never modifies files. Users review the suggestions
and hand-apply the ones that make sense.

What it reports:
    - Baseline token count (char//4 heuristic).
    - Whitespace bloat: blank-line runs, trailing whitespace.
    - Repeated phrases (4+ word collocations appearing >=3 times).
    - Verbose phrasing patterns with concrete replacements
      ("in order to" -> "to", "make sure that" -> "ensure", etc.).
    - Estimated savings if every suggestion is applied.

Design notes:
    - No LLM call, no proxy dependency — runs fully offline.
    - Deterministic: same input -> same report.
    - Pro-compatible hook: ``analyze()`` returns a structured dict so Pro
      tiers can layer LLM-based rewording on top.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Verbose-phrase replacements — conservative, widely-agreed redundancies.
_VERBOSE_PATTERNS: list[tuple[str, str, str]] = [
    # (human_label, regex_pattern, replacement)
    ("in order to", r"\bin order to\b", "to"),
    ("make sure that", r"\bmake sure (?:that )?", "ensure "),
    ("due to the fact that", r"\bdue to the fact that\b", "because"),
    ("at this point in time", r"\bat this point in time\b", "now"),
    ("in the event that", r"\bin the event that\b", "if"),
    ("a number of", r"\ba number of\b", "several"),
    ("with regard to", r"\bwith regard to\b", "about"),
    ("it is important to note that", r"\bit is important to note that\b", ""),
    ("please note that", r"\bplease note that\b", ""),
    ("as a matter of fact", r"\bas a matter of fact\b", ""),
    ("for the purpose of", r"\bfor the purpose of\b", "to"),
    ("in spite of the fact that", r"\bin spite of the fact that\b", "although"),
    ("on the grounds that", r"\bon the grounds that\b", "because"),
    ("the reason why", r"\bthe reason why\b", "why"),
    ("the question as to whether", r"\bthe question as to whether\b", "whether"),
]


@dataclass
class Finding:
    kind: str            # "whitespace" | "repeated_phrase" | "verbose"
    summary: str
    evidence: str = ""
    count: int = 1
    est_chars_saved: int = 0
    suggestion: str = ""


@dataclass
class OptimizationReport:
    source: str = "<unknown>"
    original_chars: int = 0
    original_tokens: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def est_chars_saved(self) -> int:
        return sum(f.est_chars_saved for f in self.findings)

    @property
    def est_tokens_saved(self) -> int:
        return self.est_chars_saved // 4

    @property
    def est_pct_saved(self) -> float:
        if self.original_chars <= 0:
            return 0.0
        return 100.0 * self.est_chars_saved / self.original_chars

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "original_chars": self.original_chars,
            "original_tokens": self.original_tokens,
            "est_chars_saved": self.est_chars_saved,
            "est_tokens_saved": self.est_tokens_saved,
            "est_pct_saved": round(self.est_pct_saved, 1),
            "findings": [
                {
                    "kind": f.kind,
                    "summary": f.summary,
                    "count": f.count,
                    "est_chars_saved": f.est_chars_saved,
                    "suggestion": f.suggestion,
                    "evidence": f.evidence,
                }
                for f in self.findings
            ],
        }


def _analyze_whitespace(text: str) -> list[Finding]:
    findings: list[Finding] = []

    blank_runs = re.findall(r"\n{3,}", text)
    if blank_runs:
        wasted = sum(len(r) - 2 for r in blank_runs)  # keep 1 blank line per run
        findings.append(
            Finding(
                kind="whitespace",
                summary="Consecutive blank-line runs",
                count=len(blank_runs),
                est_chars_saved=wasted,
                suggestion="Collapse runs of 3+ newlines to a single blank line.",
            )
        )

    trailing = re.findall(r"[ \t]+\n", text)
    if trailing:
        wasted = sum(len(t) - 1 for t in trailing)
        findings.append(
            Finding(
                kind="whitespace",
                summary="Trailing whitespace on lines",
                count=len(trailing),
                est_chars_saved=wasted,
                suggestion="Strip trailing spaces/tabs from line endings.",
            )
        )

    return findings


def _analyze_repeated_phrases(
    text: str, min_words: int = 4, min_occurrences: int = 3
) -> list[Finding]:
    """Find collocations of min_words+ that appear min_occurrences+ times."""
    words = re.findall(r"\b\S+\b", text.lower())
    if len(words) < min_words * min_occurrences:
        return []

    counter: Counter[str] = Counter()
    for n in (min_words, min_words + 2):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i : i + n])
            if len(phrase) < 15:
                continue
            counter[phrase] += 1

    findings: list[Finding] = []
    for phrase, count in counter.most_common(10):
        if count < min_occurrences:
            break
        # Conservative savings estimate: name the concept once (~20 chars) and
        # reference N-1 more times.
        est = max(0, (len(phrase) - 20) * (count - 1))
        if est < 30:
            continue
        findings.append(
            Finding(
                kind="repeated_phrase",
                summary=f"Phrase repeated {count}x",
                evidence=phrase[:80],
                count=count,
                est_chars_saved=est,
                suggestion="Consider naming this concept once and referring back by shorter name.",
            )
        )
    return findings


def _analyze_verbose_patterns(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for label, pat, repl in _VERBOSE_PATTERNS:
        matches = list(re.finditer(pat, text, flags=re.IGNORECASE))
        if not matches:
            continue
        orig_chars = sum(m.end() - m.start() for m in matches)
        new_chars = len(repl) * len(matches)
        est = max(0, orig_chars - new_chars)
        if est < 5:
            continue
        findings.append(
            Finding(
                kind="verbose",
                summary=f'"{label}" ({len(matches)}x)',
                count=len(matches),
                est_chars_saved=est,
                suggestion=(
                    f'Replace "{label}" with "{repl.strip()}"'
                    if repl.strip()
                    else f'Drop "{label}"'
                ),
            )
        )
    return findings


def analyze(text: str, source: str = "<stdin>") -> OptimizationReport:
    """Return a structured optimization report for the given text."""
    report = OptimizationReport(source=source)
    report.original_chars = len(text)
    report.original_tokens = max(1, len(text) // 4)
    report.findings.extend(_analyze_whitespace(text))
    report.findings.extend(_analyze_repeated_phrases(text))
    report.findings.extend(_analyze_verbose_patterns(text))
    report.findings.sort(key=lambda f: f.est_chars_saved, reverse=True)
    return report


def _render(report: OptimizationReport) -> str:
    lines: list[str] = [""]
    lines.append("  TOKENPAK optimize")
    lines.append("  " + "─" * 40)
    lines.append(f"  Source       {report.source}")
    lines.append(
        f"  Size         {report.original_chars:,} chars  "
        f"(~{report.original_tokens:,} tokens)"
    )
    lines.append("")
    if not report.findings:
        lines.append("  ✅ No optimization opportunities found.")
        lines.append("")
        return "\n".join(lines)

    lines.append(
        f"  Estimated savings:  ~{report.est_chars_saved:,} chars  "
        f"(~{report.est_tokens_saved:,} tokens, {report.est_pct_saved:.1f}%)"
    )
    lines.append("")
    lines.append(f"  Findings ({len(report.findings)}):")
    shown = 0
    for f in report.findings:
        badge = {"whitespace": "⬜", "repeated_phrase": "🔁", "verbose": "✂️"}.get(f.kind, "•")
        lines.append(f"    {badge} [{f.kind}] {f.summary}")
        if f.evidence:
            lines.append(f"       evidence: {f.evidence}")
        lines.append(f"       save ~{f.est_chars_saved:,} chars — {f.suggestion}")
        shown += 1
        if shown >= 20:
            remaining = len(report.findings) - shown
            if remaining > 0:
                lines.append(f"    … +{remaining} more (use --json for full list)")
            break
    lines.append("")
    return "\n".join(lines)


def run_optimize_prompt(args: argparse.Namespace) -> int:
    """Handler for `tokenpak optimize --file <path>`."""
    import json as _json
    import sys

    try:
        if getattr(args, "file", None):
            text = Path(args.file).read_text(encoding="utf-8")
            source = str(Path(args.file))
        else:
            text = sys.stdin.read()
            source = "<stdin>"
    except FileNotFoundError as e:
        print(f"optimize: file not found: {e}", file=sys.stderr)
        return 2

    if not text.strip():
        print("optimize: empty input — nothing to analyze", file=sys.stderr)
        return 1

    report = analyze(text, source=source)

    if getattr(args, "as_json", False) or getattr(args, "json", False):
        print(_json.dumps(report.to_dict(), indent=2))
    else:
        print(_render(report))

    return 0
