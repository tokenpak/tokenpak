"""Document-type schema converter for TokenPak ingest/index flows."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

SCHEMAS: Dict[str, List[str]] = {
    "contract": ["parties", "dates", "obligations", "payment_terms", "termination", "exceptions"],
    "research_paper": ["question", "method", "dataset", "findings", "limitations", "metrics"],
    "proposal": ["client", "objective", "scope", "timeline", "price", "exclusions"],
    "design_doc": ["problem", "architecture", "components", "constraints", "open_issues", "decisions"],
    "meeting_notes": ["attendees", "decisions", "action_items", "blockers", "next_meeting"],
    "bug_report": ["symptom", "repro_steps", "expected", "actual", "environment"],
    "changelog": ["version", "date", "added", "changed", "fixed", "removed"],
}

_PATTERNS = {
    "contract": [r"\bparty\b", r"\btermination\b", r"\bwhereas\b", r"payment terms"],
    "research_paper": [r"\babstract\b", r"\bmethod(?:ology)?\b", r"\bfindings\b", r"\blimitations\b"],
    "proposal": [r"\bscope\b", r"\btimeline\b", r"\bprice\b", r"\bproposal\b"],
    "design_doc": [r"\barchitecture\b", r"\bcomponents\b", r"\bconstraints\b", r"\bdecision\b"],
    "meeting_notes": [r"\battendees\b", r"\baction items?\b", r"\bblockers?\b", r"meeting notes"],
    "bug_report": [r"\brepro(?:duction)? steps\b", r"\bexpected\b", r"\bactual\b", r"\benvironment\b"],
    "changelog": [r"\bchangelog\b", r"\badded\b", r"\bchanged\b", r"\bfixed\b", r"\bremoved\b"],
}

_FILENAME_HINTS = {
    "contract": ["contract", "agreement"],
    "research_paper": ["paper", "research", "study"],
    "proposal": ["proposal", "quote", "rfp"],
    "design_doc": ["design", "architecture", "adr"],
    "meeting_notes": ["meeting", "notes", "minutes"],
    "bug_report": ["bug", "incident", "issue"],
    "changelog": ["changelog", "release-notes", "release_notes"],
}

_KEY_ALIASES = {
    "payment_terms": ["payment terms", "payment", "terms"],
    "open_issues": ["open issues", "risks", "todos", "to do"],
    "action_items": ["action items", "actions", "next actions"],
    "repro_steps": ["repro steps", "reproduction", "steps"],
    "next_meeting": ["next meeting", "follow-up", "follow up"],
}


def detect_document_type(content: str, filename: str = "") -> Optional[str]:
    """Detect a known document type using filename hints + content patterns."""
    lowered = content.lower()
    fname = Path(filename).name.lower()

    scores: Dict[str, int] = {k: 0 for k in SCHEMAS}

    for doc_type, hints in _FILENAME_HINTS.items():
        if any(h in fname for h in hints):
            scores[doc_type] += 3

    for doc_type, patterns in _PATTERNS.items():
        for pat in patterns:
            if re.search(pat, lowered):
                scores[doc_type] += 1

    best_type = max(scores, key=scores.get)
    if scores[best_type] <= 0:
        return None
    return best_type


def extract_schema(content: str, doc_type: str) -> Optional[Dict[str, str]]:
    """Extract schema fields from known document type; returns None for unknown types."""
    fields = SCHEMAS.get(doc_type)
    if not fields:
        return None

    lines = [line.strip() for line in content.splitlines()]
    lowered_lines = [line.lower() for line in lines]

    extracted: Dict[str, str] = {}
    for field in fields:
        candidates = [field.replace("_", " ")] + _KEY_ALIASES.get(field, [])

        value = ""
        for i, lower in enumerate(lowered_lines):
            matched = next((c for c in candidates if lower.startswith(c + ":") or lower.startswith(c + " -")), None)
            if matched:
                line = lines[i]
                if ":" in line:
                    value = line.split(":", 1)[1].strip()
                elif "-" in line:
                    value = line.split("-", 1)[1].strip()
                break

        if not value:
            key = candidates[0]
            section = _extract_section(content, key)
            if section:
                value = section

        extracted[field] = value

    return extracted


def _extract_section(content: str, key: str) -> str:
    key_re = re.escape(key)
    pat = re.compile(
        rf"(?ims)(?:^#+\s*{key_re}\s*$|^\s*{key_re}\s*:?\s*$)(.*?)(?=^#|\Z)",
    )
    m = pat.search(content)
    if not m:
        return ""
    return " ".join(x.strip() for x in m.group(1).splitlines() if x.strip())[:600]


def convert_document(content: str, filename: str = "") -> Dict[str, object]:
    """Detect + extract schema; fallback to passthrough for unknown types."""
    doc_type = detect_document_type(content, filename=filename)
    if not doc_type:
        return {"doc_type": None, "schema": None}

    return {
        "doc_type": doc_type,
        "schema": extract_schema(content, doc_type),
    }


def should_serve_schema(intent: Optional[str]) -> bool:
    """Return True when request intent can use schema summary over raw/compressed text."""
    if not intent:
        return True
    lowered = intent.lower()
    blocklist = ("exact", "quote", "verbatim", "raw")
    return not any(x in lowered for x in blocklist)
