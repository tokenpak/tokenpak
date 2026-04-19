from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List

REQUIRED_CAPSULE_SECTIONS = (
    "session_metadata",
    "decisions_made",
    "artifacts_created",
    "action_items",
    "insights",
    "raw_transcript_reference",
)

_SECTION_WEIGHTS = {
    "decisions_made": 3.0,
    "artifacts_created": 2.5,
    "action_items": 2.0,
    "insights": 1.8,
    "session_metadata": 1.0,
    "raw_transcript_reference": 0.2,
}

_SECTION_ALIASES = {
    "session metadata": "session_metadata",
    "metadata": "session_metadata",
    "decisions made": "decisions_made",
    "decisions": "decisions_made",
    "artifacts created": "artifacts_created",
    "artifacts": "artifacts_created",
    "action items": "action_items",
    "actions": "action_items",
    "insights": "insights",
    "raw transcript reference": "raw_transcript_reference",
    "transcript": "raw_transcript_reference",
}

_HEADING_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(?P<text>.+?)\s*$")
_FM_KEY_RE = re.compile(r"^(?P<k>[A-Za-z0-9_-]+):\s*(?P<v>.+)$")


def _normalize_lines(text: str) -> List[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _parse_frontmatter(lines: List[str]) -> Dict[str, str]:
    if not lines or lines[0].strip() != "---":
        return {}

    out: Dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = _FM_KEY_RE.match(line.strip())
        if m:
            out[m.group("k").strip().lower()] = m.group("v").strip()
    return out


def _resolve_section(heading: str) -> str | None:
    key = heading.strip().lower()
    return _SECTION_ALIASES.get(key)


def _clean_value(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def build_session_capsule(raw_text: str, source_path: str = "") -> Dict[str, Any]:
    lines = _normalize_lines(raw_text)
    frontmatter = _parse_frontmatter(lines)

    sections: Dict[str, List[str]] = {k: [] for k in REQUIRED_CAPSULE_SECTIONS[:-1]}
    current_section: str | None = None

    for line in lines:
        hm = _HEADING_RE.match(line)
        if hm:
            current_section = _resolve_section(hm.group("title"))
            continue

        if current_section and current_section in sections:
            bm = _BULLET_RE.match(line)
            if bm:
                value = _clean_value(bm.group("text"))
                if value:
                    sections[current_section].append(value)
            elif line.strip() and not line.strip().startswith("---"):
                value = _clean_value(line)
                if value:
                    sections[current_section].append(value)

    metadata = {
        "source_path": source_path,
        "line_count": len(lines),
        "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    }
    metadata.update(frontmatter)

    capsule: Dict[str, Any] = {
        "session_metadata": metadata,
        "decisions_made": sections["decisions_made"],
        "artifacts_created": sections["artifacts_created"],
        "action_items": sections["action_items"],
        "insights": sections["insights"],
        "raw_transcript_reference": {
            "source_path": source_path,
            "sha256": metadata["sha256"],
            "fallback": "Use source_path + sha256 to retrieve full transcript",
        },
    }
    return capsule


def serialize_capsule(capsule: Dict[str, Any]) -> str:
    return json.dumps(capsule, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def score_capsule_sections(capsule: Dict[str, Any]) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    for section in REQUIRED_CAPSULE_SECTIONS:
        weight = _SECTION_WEIGHTS[section]
        value = capsule.get(section)
        if isinstance(value, list):
            density = float(len([v for v in value if str(v).strip()]))
        elif isinstance(value, dict):
            density = float(len([v for v in value.values() if str(v).strip()]))
        else:
            density = 1.0 if value else 0.0
        scores[section] = round(weight * density, 4)
    return scores


def capsule_retrieval_score(base_score: float, capsule: Dict[str, Any] | None) -> float:
    if not capsule:
        return base_score

    section_scores = score_capsule_sections(capsule)
    high_signal = (
        section_scores["decisions_made"]
        + section_scores["artifacts_created"]
        + section_scores["action_items"]
        + section_scores["insights"]
    )
    low_signal = section_scores["raw_transcript_reference"]
    boost = min(5.0, max(0.0, (high_signal - low_signal) / 5.0))
    return base_score + boost
