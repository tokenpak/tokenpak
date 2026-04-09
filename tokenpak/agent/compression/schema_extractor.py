"""
Schema Extractor — Document-type-aware schema substitution for TokenPak.

When a document's type is predictable, replaces verbose raw text with a
compact key-value extraction.  Yields large token savings for structured
docs like meeting notes, PRs, bug reports, log output, and config files.

Usage::

    from tokenpak.agent.compression.schema_extractor import SchemaExtractor

    extractor = SchemaExtractor()
    result = extractor.extract(text)
    # result.doc_type    → detected document type (or "unknown")
    # result.confidence  → float 0-1
    # result.fields      → dict of extracted key-value fields
    # result.compact     → compact string representation (token-efficient)
    # result.passthrough → True when doc_type is unknown / confidence too low

Detection is purely heuristic (regex + keyword scoring) — no external
dependencies and no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Templates — the canonical field list per document type
# ---------------------------------------------------------------------------

TEMPLATES: Dict[str, List[str]] = {
    "meeting": ["attendees", "decisions", "blockers", "follow_ups"],
    "pull_request": ["files_changed", "tests_affected", "risk_level", "dependencies"],
    "bug_report": ["symptom", "repro_steps", "expected", "actual", "environment"],
    "log_output": ["error_count", "unique_errors", "first_error", "last_error", "timespan"],
    "config_file": ["changed_keys", "added_keys", "removed_keys", "format"],
}

# Minimum confidence required to apply schema substitution
CONFIDENCE_THRESHOLD = 0.15

# ---------------------------------------------------------------------------
# Detection signals — keyword / pattern sets per type
# ---------------------------------------------------------------------------

_DETECTION_PATTERNS: Dict[str, List[str]] = {
    "meeting": [
        r"\battendee[s]?\b",
        r"\baction item[s]?\b",
        r"\bmeeting notes?\b",
        r"\bstandup\b",
        r"\bsync\b",
        r"\bfollowup\b",
        r"\bfollow.up\b",
        r"\bblocker[s]?\b",
        r"\bdecision[s]?\b",
        r"\bagenda\b",
        r"\bminutes\b",
    ],
    "pull_request": [
        r"\bpull request\b",
        r"\bpr #?\d+\b",
        r"\bmerge request\b",
        r"\bdiff\b",
        r"\bchangeset\b",
        r"\breviewer[s]?\b",
        r"\blgtm\b",
        r"\bfiles? changed\b",
        r"\btest[s]? (added|updated|failed|passed)\b",
        r"\brisk level\b",
        r"\bdependenc(y|ies)\b",
    ],
    "bug_report": [
        r"\bbug report\b",
        r"\bissue\b",
        r"\brepro(duce)?\b",
        r"\bsteps? to reproduce\b",
        r"\bexpected (behavior|result)\b",
        r"\bactual (behavior|result)\b",
        r"\bstack ?trace\b",
        r"\berror message\b",
        r"\bsymptom[s]?\b",
        r"\benvironment\b",
        r"\bplatform\b",
        r"\bos version\b",
    ],
    "log_output": [
        r"\b(ERROR|WARN|INFO|DEBUG|CRITICAL|FATAL)\b",
        r"\bstack ?trace\b",
        r"\btraceback\b",
        r"\bexception\b",
        r"\bstdout\b",
        r"\bstderr\b",
        r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\b",  # ISO timestamp
        r"\b\[\d{4}-\d{2}-\d{2}\]",
        r"\blog (output|file|entry)\b",
    ],
    "config_file": [
        r"\bconfig(uration)?\b",
        r"\bsettings?\b",
        r"\byaml\b",
        r"\btoml\b",
        r"\bjson\b",
        r"\benv(ironment)? var(iable)?[s]?\b",
        r"\bkey.?value\b",
        r"\bchanged keys?\b",
        r"\badded keys?\b",
        r"\bremoved keys?\b",
        r"^\s*[\w.-]+\s*[:=]\s*.+$",  # key: value or key=value lines
        r"^[+\-]\s*[\w_.-]+\s*:",  # diff-style +/- lines with key: value
        r"\bmax_\w+\b",  # common config key prefixes
    ],
}


# ---------------------------------------------------------------------------
# Per-type field extractors
# ---------------------------------------------------------------------------


def _extract_meeting(text: str) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}

    # Attendees — look for lines like "Attendees: Alice, Bob, Carol"
    m = re.search(r"attendees?[:\-\s]+([^\n]{3,120})", text, re.IGNORECASE)
    if m:
        fields["attendees"] = [a.strip() for a in re.split(r"[,;]", m.group(1)) if a.strip()]
    else:
        fields["attendees"] = []

    # Decisions — bullet/numbered items near "Decision" or "Decided"
    decisions = re.findall(r"(?:decision[s]?|decided)[:\-\s]+([^\n]{5,200})", text, re.IGNORECASE)
    fields["decisions"] = [d.strip() for d in decisions] or _extract_bullets_near(
        text, ["decision", "decided"]
    )

    # Blockers
    blockers = re.findall(r"blocker[s]?[:\-\s]+([^\n]{5,200})", text, re.IGNORECASE)
    fields["blockers"] = [b.strip() for b in blockers] or _extract_bullets_near(
        text, ["blocker", "blocked"]
    )

    # Follow-ups / action items
    follow_ups = re.findall(
        r"(?:follow.?up[s]?|action item[s]?)[:\-\s]+([^\n]{5,200})", text, re.IGNORECASE
    )
    fields["follow_ups"] = [f.strip() for f in follow_ups] or _extract_bullets_near(
        text, ["follow up", "followup", "action item", "todo", "to-do"]
    )

    return fields


def _extract_pull_request(text: str) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}

    # Files changed — try to extract a count or list
    m = re.search(r"files?\s+changed[:\-\s]+([^\n]{3,200})", text, re.IGNORECASE)
    if m:
        fields["files_changed"] = m.group(1).strip()
    else:
        # Count lines that look like diff paths
        paths = re.findall(r"(?:^\+\+\+|^---|\bmodified:|\bchanged:)\s+(.+)", text, re.MULTILINE)
        fields["files_changed"] = len(paths) if paths else "unknown"

    # Tests affected
    m = re.search(
        r"tests?\s+(added|updated|affected|failed|passed)[:\-\s]*([^\n]{0,100})",
        text,
        re.IGNORECASE,
    )
    fields["tests_affected"] = m.group(0).strip() if m else "unknown"

    # Risk level
    m = re.search(r"risk[_\s-]?level[:\-\s]+([^\n]{3,50})", text, re.IGNORECASE)
    if not m:
        m = re.search(r"\b(low|medium|high|critical)\s+risk\b", text, re.IGNORECASE)
    fields["risk_level"] = m.group(1).strip() if m else "unknown"

    # Dependencies
    deps = re.findall(r"dependenc(?:y|ies)[:\-\s]+([^\n]{3,200})", text, re.IGNORECASE)
    fields["dependencies"] = [d.strip() for d in deps] if deps else []

    return fields


def _extract_bug_report(text: str) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}

    # Symptom
    m = re.search(r"symptom[s]?[:\-\s]+([^\n]{5,300})", text, re.IGNORECASE)
    fields["symptom"] = m.group(1).strip() if m else _first_non_header_line(text)

    # Repro steps
    m = re.search(
        r"(?:steps?\s+to\s+reproduce|repro\s+steps?)[:\-\s]+(.+?)(?=\n\s*\n|\Z)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        steps = re.findall(r"(?:^\s*[\d\-\*\•]\s*)(.+)", m.group(1), re.MULTILINE)
        fields["repro_steps"] = [s.strip() for s in steps] if steps else [m.group(1).strip()[:200]]
    else:
        fields["repro_steps"] = []

    # Expected
    m = re.search(r"expected[:\-\s]+([^\n]{5,300})", text, re.IGNORECASE)
    fields["expected"] = m.group(1).strip() if m else "unknown"

    # Actual
    m = re.search(r"actual[:\-\s]+([^\n]{5,300})", text, re.IGNORECASE)
    fields["actual"] = m.group(1).strip() if m else "unknown"

    # Environment
    m = re.search(r"(?:environment|platform|os)[:\-\s]+([^\n]{3,100})", text, re.IGNORECASE)
    fields["environment"] = m.group(1).strip() if m else "unknown"

    return fields


def _extract_log_output(text: str) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}

    # Count errors
    errors = re.findall(r"\b(ERROR|CRITICAL|FATAL)\b", text)
    fields["error_count"] = len(errors)

    # Unique error messages — extract lines with ERROR/CRITICAL/FATAL
    error_lines = re.findall(r"^.*(?:ERROR|CRITICAL|FATAL).*$", text, re.MULTILINE)
    # Deduplicate by normalizing timestamps/pids
    normalized = set()
    for line in error_lines:
        norm = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?\b", "<ts>", line)
        norm = re.sub(r"\bpid[:=\s]\d+\b", "<pid>", norm, flags=re.IGNORECASE)
        normalized.add(norm.strip())
    fields["unique_errors"] = list(normalized)[:5]  # cap at 5

    # First and last error
    fields["first_error"] = error_lines[0].strip()[:200] if error_lines else "none"
    fields["last_error"] = error_lines[-1].strip()[:200] if error_lines else "none"

    # Timespan — earliest and latest timestamp
    timestamps = re.findall(r"\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})", text)
    if timestamps:
        fields["timespan"] = f"{timestamps[0]} → {timestamps[-1]}"
    else:
        fields["timespan"] = "unknown"

    return fields


def _extract_config_file(text: str) -> Dict[str, Any]:
    """
    Attempts to detect changed/added/removed keys by looking for
    diff-style markers (+/-) or explicit annotations.
    Falls back to listing all keys found.
    """
    fields: Dict[str, Any] = {}

    # Diff-style lines
    added = re.findall(r"^\+\s*([\w.\-]+)\s*[:=]", text, re.MULTILINE)
    removed = re.findall(r"^-\s*([\w.\-]+)\s*[:=]", text, re.MULTILINE)

    if added or removed:
        fields["added_keys"] = list(dict.fromkeys(added))
        fields["removed_keys"] = list(dict.fromkeys(removed))
        fields["changed_keys"] = []
    else:
        # Explicit annotations
        changed = re.findall(r"(?:changed|modified)\s*[:\-]\s*([\w.\-]+)", text, re.IGNORECASE)
        added_kw = re.findall(r"added?\s*[:\-]\s*([\w.\-]+)", text, re.IGNORECASE)
        removed_kw = re.findall(r"removed?\s*[:\-]\s*([\w.\-]+)", text, re.IGNORECASE)
        fields["changed_keys"] = list(dict.fromkeys(changed))
        fields["added_keys"] = list(dict.fromkeys(added_kw))
        fields["removed_keys"] = list(dict.fromkeys(removed_kw))

    # Detect file format
    if re.search(r"^\s*\[[\w.]+\]", text, re.MULTILINE):
        fmt = "toml/ini"
    elif re.search(r"^\s+[\w-]+:", text, re.MULTILINE):
        fmt = "yaml"
    elif text.strip().startswith("{") or text.strip().startswith("["):
        fmt = "json"
    elif re.search(r"^[\w_]+=", text, re.MULTILINE):
        fmt = "env"
    else:
        fmt = "unknown"
    fields["format"] = fmt

    return fields


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_bullets_near(text: str, keywords: List[str]) -> List[str]:
    """Extract bullet/numbered items that appear near keyword lines."""
    results: List[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if any(kw.lower() in line.lower() for kw in keywords):
            # Grab following bullet items
            for j in range(i + 1, min(i + 10, len(lines))):
                bullet = re.match(r"^\s*[-*•\d.]+\s+(.+)", lines[j])
                if bullet:
                    results.append(bullet.group(1).strip())
                elif lines[j].strip() == "":
                    break
    return results


def _first_non_header_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:200]
    return ""


_EXTRACTORS = {
    "meeting": _extract_meeting,
    "pull_request": _extract_pull_request,
    "bug_report": _extract_bug_report,
    "log_output": _extract_log_output,
    "config_file": _extract_config_file,
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    """Output of :meth:`SchemaExtractor.extract`."""

    doc_type: str
    confidence: float
    fields: Dict[str, Any] = field(default_factory=dict)
    compact: str = ""
    passthrough: bool = False
    original_length: int = 0

    @property
    def compression_ratio(self) -> float:
        """Approximate ratio of compact length to original length."""
        if not self.original_length or not self.compact:
            return 1.0
        return round(len(self.compact) / self.original_length, 3)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class SchemaExtractor:
    """
    Detect a document type and extract a compact schema from its content.

    Parameters
    ----------
    confidence_threshold : float
        Minimum confidence (0–1) required to apply schema substitution.
        Documents below the threshold are passed through unchanged.
    templates : dict, optional
        Override or extend the default TEMPLATES mapping.
    """

    def __init__(
        self,
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
        templates: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.templates = {**TEMPLATES, **(templates or {})}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_type(self, text: str) -> tuple[str, float]:
        """
        Detect the most likely document type for *text*.

        Returns
        -------
        (doc_type, confidence)
            doc_type is one of the TEMPLATES keys, or "unknown".
            confidence is a float 0–1 (higher = more certain).
        """
        scores: Dict[str, float] = {}
        text_lower = text.lower()

        for doc_type, patterns in _DETECTION_PATTERNS.items():
            hits = 0
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                    hits += 1
            scores[doc_type] = hits / len(patterns) if patterns else 0.0

        if not scores:
            return "unknown", 0.0

        best_type = max(scores, key=lambda k: scores[k])
        best_score = scores[best_type]

        # Require at least two signals to avoid false positives
        total_hits = sum(
            1
            for p in _DETECTION_PATTERNS.get(best_type, [])
            if re.search(p, text, re.IGNORECASE | re.MULTILINE)
        )
        if total_hits < 2:
            return "unknown", best_score

        return best_type, best_score

    def extract(self, text: str) -> ExtractionResult:
        """
        Detect the document type and extract a compact representation.

        If confidence is below :attr:`confidence_threshold` or the type
        is unknown, returns a passthrough result with the original text.

        Parameters
        ----------
        text : str
            Raw document text to analyse.

        Returns
        -------
        ExtractionResult
        """
        if not text or not text.strip():
            return ExtractionResult(
                doc_type="unknown",
                confidence=0.0,
                passthrough=True,
                original_length=len(text),
            )

        doc_type, confidence = self.detect_type(text)

        if doc_type == "unknown" or confidence < self.confidence_threshold:
            return ExtractionResult(
                doc_type=doc_type,
                confidence=confidence,
                passthrough=True,
                original_length=len(text),
            )

        extractor_fn = _EXTRACTORS.get(doc_type)
        if extractor_fn is None:
            return ExtractionResult(
                doc_type=doc_type,
                confidence=confidence,
                passthrough=True,
                original_length=len(text),
            )

        fields = extractor_fn(text)
        compact = self._render_compact(doc_type, fields)

        return ExtractionResult(
            doc_type=doc_type,
            confidence=round(confidence, 3),
            fields=fields,
            compact=compact,
            passthrough=False,
            original_length=len(text),
        )

    def extract_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply schema extraction to a single messages-list entry in place.

        If the message content can be extracted, replaces the ``content``
        value with the compact representation and adds metadata under
        ``_schema_extraction``.  Otherwise returns the message unchanged.

        Parameters
        ----------
        message : dict
            A standard OpenAI-style messages dict with ``role`` / ``content``.

        Returns
        -------
        dict
            Modified (or original) message dict.
        """
        content = message.get("content", "")
        if isinstance(content, list):
            # Multi-block content — try on concatenated text blocks
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            text = "\n".join(text_parts)
        elif isinstance(content, str):
            text = content
        else:
            return message

        result = self.extract(text)
        if result.passthrough:
            return message

        new_message = dict(message)
        new_message["content"] = result.compact
        new_message["_schema_extraction"] = {
            "doc_type": result.doc_type,
            "confidence": result.confidence,
            "fields": result.fields,
            "original_length": result.original_length,
            "compact_length": len(result.compact),
            "compression_ratio": result.compression_ratio,
        }
        return new_message

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_compact(self, doc_type: str, fields: Dict[str, Any]) -> str:
        """Render extracted fields as a compact, token-efficient string."""
        template_keys = self.templates.get(doc_type, list(fields.keys()))
        lines = [f"[{doc_type.upper()}]"]
        for key in template_keys:
            value = fields.get(key, "unknown")
            if isinstance(value, list):
                if value:
                    value_str = "; ".join(str(v) for v in value[:10])
                else:
                    value_str = "none"
            else:
                value_str = str(value) if value != "" else "unknown"
            lines.append(f"{key}: {value_str}")
        return "\n".join(lines)
