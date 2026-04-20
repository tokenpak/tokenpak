# SPDX-License-Identifier: Apache-2.0
"""tokenpak/precompute.py — Intent-specific precomputation pipeline.

At index time, detects document types and generates intent-ready artifacts
stored in ~/.tokenpak/artifacts/{intent_type}/{block_id}.json.

Artifact types
--------------
fact_card        — Key facts extracted in compact Q&A format (for Q&A intents)
feature_table    — Normalized comparison table (for comparison/explain intents)
error_signature  — Deduplicated error patterns + causes (for debug intents)
project_snapshot — Current status, blockers, next steps (for plan intents)

Integration
-----------
At index time: call ``precompute_for_block(block_id, content, risk_class)``
At retrieval: call ``get_precomputed_artifact(block_id, intent)`` — returns
artifact content or None (fallback to raw).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_ARTIFACTS_DIR = Path.home() / ".tokenpak" / "artifacts"


# ---------------------------------------------------------------------------
# Document type detection
# ---------------------------------------------------------------------------


class DocType(str, Enum):
    """Broad document category used for artifact routing."""

    CODE = "code"
    CONFIG = "config"
    NARRATIVE = "narrative"
    CHANGELOG = "changelog"
    ERROR_LOG = "error_log"
    COMPARISON = "comparison"
    PROJECT_PLAN = "project_plan"
    UNKNOWN = "unknown"


_ERROR_PATTERNS = re.compile(
    r"\b(traceback|exception|error:|errno|stacktrace|fatal|panic|segfault"
    r"|at line \d+|caused by|exception in thread)\b",
    re.IGNORECASE,
)
_COMPARISON_PATTERNS = re.compile(
    r"\b(vs\.?|versus|compared? to|comparison|pros?|cons?|tradeoff|benchmark)\b",
    re.IGNORECASE,
)
_PLAN_PATTERNS = re.compile(
    r"\b(todo|tasks?|milestone|sprint|epic|blockers?|next steps?|action items?|roadmap|objective)\b",
    re.IGNORECASE,
)
_CHANGELOG_PATTERNS = re.compile(
    r"\b(changelog|release notes?|version \d+\.\d+|added|changed|deprecated|removed|fixed|security)\b",
    re.IGNORECASE,
)


def detect_doc_type(
    content: str,
    risk_class: str = "narrative",
    source_path: str = "",
) -> DocType:
    """Detect document type from content and metadata.

    Args:
        content:     Raw document text.
        risk_class:  risk_class from block metadata (code/config/narrative/…).
        source_path: Relative source path (used for extension hints).

    Returns:
        DocType enum value.
    """
    if risk_class == "code":
        return DocType.CODE
    if risk_class == "config":
        return DocType.CONFIG

    # Heuristic content analysis
    sample = content[:4000]  # Only look at first 4k chars

    error_hits = len(_ERROR_PATTERNS.findall(sample))
    compare_hits = len(_COMPARISON_PATTERNS.findall(sample))
    plan_hits = len(_PLAN_PATTERNS.findall(sample))
    changelog_hits = len(_CHANGELOG_PATTERNS.findall(sample))

    # Changelog: high keyword density or changelog filename
    name = Path(source_path).name.lower()
    if "changelog" in name or "release" in name or changelog_hits >= 4:
        return DocType.CHANGELOG

    # Error log: significant error density
    if error_hits >= 3:
        return DocType.ERROR_LOG

    # Comparison: comparison keywords present
    if compare_hits >= 2:
        return DocType.COMPARISON

    # Project plan: task/planning keywords
    if plan_hits >= 3:
        return DocType.PROJECT_PLAN

    if risk_class == "narrative":
        return DocType.NARRATIVE

    return DocType.UNKNOWN


# ---------------------------------------------------------------------------
# Artifact schemas
# ---------------------------------------------------------------------------


@dataclass
class PrecomputedArtifact:
    """An intent-ready precomputed artifact."""

    block_id: str
    artifact_type: str  # fact_card | feature_table | error_signature | project_snapshot
    intent: str  # Matching intent (query | explain | debug | plan)
    content: str  # Rendered artifact text (compact, context-ready)
    doc_type: str
    source_path: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    token_estimate: int = 0
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "block_id": self.block_id,
            "artifact_type": self.artifact_type,
            "intent": self.intent,
            "content": self.content,
            "doc_type": self.doc_type,
            "source_path": self.source_path,
            "created_at": self.created_at,
            "token_estimate": self.token_estimate,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "PrecomputedArtifact":
        return cls(
            block_id=d["block_id"],
            artifact_type=d["artifact_type"],
            intent=d["intent"],
            content=d["content"],
            doc_type=d["doc_type"],
            source_path=d.get("source_path", ""),
            created_at=d.get("created_at", ""),
            token_estimate=d.get("token_estimate", 0),
            metadata=d.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Artifact generators
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    """Quick token estimate: chars / 4."""
    return max(1, len(text) // 4)


def generate_fact_card(
    block_id: str, content: str, doc_type: DocType, source_path: str = ""
) -> PrecomputedArtifact:
    """Extract key facts in compact Q&A format.

    Scans for headings, bold terms, and definition-like sentences.
    Produces ≤20 fact pairs suitable for Q&A retrieval.
    """
    facts: List[str] = []

    # Extract from markdown headings + first sentence after them
    lines = content.splitlines()
    i = 0
    while i < len(lines) and len(facts) < 20:
        line = lines[i].strip()
        # Markdown heading
        heading_m = re.match(r"^#{1,3}\s+(.+)$", line)
        if heading_m:
            heading_text = heading_m.group(1).rstrip(":")
            # Grab first non-empty line after heading as answer
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                answer = lines[j].strip().lstrip("-*• ").rstrip(".")
                if answer and len(answer) > 10:
                    facts.append(f"Q: What is {heading_text}?\nA: {answer}")
        # Bullet items that look like definitions (contains ":")
        elif re.match(r"^[-*•]\s+\*\*.+\*\*:", line) or re.match(r"^[-*•]\s+.+:\s+.{10,}", line):
            clean = re.sub(r"\*\*", "", line.lstrip("-*• "))
            facts.append(f"• {clean}")
        i += 1

    # Fallback: grab first 5 non-empty sentences from the document
    if len(facts) < 3:
        sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\n+", " ", content))
        for sent in sentences[:10]:
            sent = sent.strip()
            if 20 < len(sent) < 300 and not sent.startswith("#"):
                facts.append(sent)
                if len(facts) >= 5:
                    break

    rendered = f"[FACT CARD: {source_path or block_id}]\n\n" + "\n\n".join(facts[:20])
    return PrecomputedArtifact(
        block_id=block_id,
        artifact_type="fact_card",
        intent="query",
        content=rendered,
        doc_type=doc_type.value,
        source_path=source_path,
        token_estimate=_estimate_tokens(rendered),
        metadata={"fact_count": len(facts)},
    )


def generate_feature_table(
    block_id: str, content: str, doc_type: DocType, source_path: str = ""
) -> PrecomputedArtifact:
    """Build a normalized feature/comparison table.

    Parses markdown tables and bullet comparisons into a canonical format.
    """
    rows: List[str] = []
    headers: List[str] = []

    # Parse markdown tables
    in_table = False
    for line in content.splitlines():
        line = line.rstrip()
        if "|" in line and not re.match(r"^\s*\|[-:| ]+\|\s*$", line):
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if cells:
                if not in_table:
                    headers = cells
                    in_table = True
                else:
                    rows.append(" | ".join(cells))
        else:
            if in_table and rows:
                break  # End of table
            in_table = False

    # Fallback: extract comparison bullet points
    if not rows:
        for line in content.splitlines():
            m = re.match(r"^[-*•]\s+\*?\*?(.+?)\*?\*?:\s*(.+)", line)
            if m:
                rows.append(f"{m.group(1).strip()} | {m.group(2).strip()}")
                if len(rows) >= 20:
                    break

    header_line = " | ".join(headers) if headers else "Feature | Value"
    table_body = "\n".join(rows[:20]) if rows else "(no structured comparison data found)"
    rendered = (
        f"[FEATURE TABLE: {source_path or block_id}]\n\n"
        f"{header_line}\n"
        f"{'-' * len(header_line)}\n"
        f"{table_body}"
    )
    return PrecomputedArtifact(
        block_id=block_id,
        artifact_type="feature_table",
        intent="explain",
        content=rendered,
        doc_type=doc_type.value,
        source_path=source_path,
        token_estimate=_estimate_tokens(rendered),
        metadata={"row_count": len(rows), "has_headers": bool(headers)},
    )


def generate_error_signature(
    block_id: str, content: str, doc_type: DocType, source_path: str = ""
) -> PrecomputedArtifact:
    """Extract and deduplicate error patterns with likely causes.

    Scans for error lines, tracebacks, and exception signatures.
    """
    # Patterns that flag error lines
    error_line_re = re.compile(
        r"(?:Error|Exception|Traceback|FATAL|CRITICAL|errno|panic)[\w\s]*:.*",
        re.IGNORECASE,
    )
    cause_re = re.compile(
        r"(?:caused by|because|due to|reason:|fix:|solution:)\s*(.+)",
        re.IGNORECASE,
    )

    seen: set = set()
    signatures: List[str] = []

    lines = content.splitlines()
    for i, line in enumerate(lines):
        line = line.rstrip()
        m = error_line_re.search(line)
        if m:
            sig = m.group(0).strip()[:200]
            if sig not in seen:
                seen.add(sig)
                # Look ahead for cause
                cause = ""
                for j in range(i + 1, min(i + 5, len(lines))):
                    cm = cause_re.search(lines[j])
                    if cm:
                        cause = cm.group(1).strip()[:150]
                        break
                if cause:
                    signatures.append(f"ERROR: {sig}\nCAUSE: {cause}")
                else:
                    signatures.append(f"ERROR: {sig}")
                if len(signatures) >= 15:
                    break

    if not signatures:
        signatures.append("(no distinct error signatures detected in this document)")

    rendered = f"[ERROR SIGNATURES: {source_path or block_id}]\n\n" + "\n\n".join(signatures)
    return PrecomputedArtifact(
        block_id=block_id,
        artifact_type="error_signature",
        intent="debug",
        content=rendered,
        doc_type=doc_type.value,
        source_path=source_path,
        token_estimate=_estimate_tokens(rendered),
        metadata={"signature_count": len(signatures)},
    )


def generate_project_snapshot(
    block_id: str, content: str, doc_type: DocType, source_path: str = ""
) -> PrecomputedArtifact:
    """Extract current status, blockers, and next steps from planning docs."""
    status_lines: List[str] = []
    blockers: List[str] = []
    next_steps: List[str] = []

    status_re = re.compile(r"^#{1,3}\s*(status|current state|progress)", re.IGNORECASE)
    blocker_re = re.compile(r"(blocker|blocked by|issue:|problem:|\[x\]|\[ \])", re.IGNORECASE)
    next_re = re.compile(
        r"(next steps?|action items?|todo|milestone|upcoming|planned)",
        re.IGNORECASE,
    )

    lines = content.splitlines()
    section = None
    for line in lines:
        stripped = line.strip()
        if status_re.search(stripped):
            section = "status"
        elif re.search(r"^#{1,3}\s*(blocker|risk|issue)", stripped, re.IGNORECASE):
            section = "blocker"
        elif re.search(r"^#{1,3}\s*(next|todo|action|plan|upcoming)", stripped, re.IGNORECASE):
            section = "next"
        elif stripped.startswith("#"):
            section = None

        if section == "status" and stripped and not stripped.startswith("#"):
            status_lines.append(stripped)
        elif section == "blocker" and stripped and not stripped.startswith("#"):
            blockers.append(stripped)
        elif section == "next" and stripped and not stripped.startswith("#"):
            next_steps.append(stripped)
        else:
            # Inline keywords even outside sections
            if blocker_re.search(stripped) and stripped not in blockers:
                blockers.append(stripped[:150])
            elif next_re.search(stripped) and stripped not in next_steps and len(next_steps) < 10:
                next_steps.append(stripped[:150])

    # Fallback: first paragraph as status
    if not status_lines:
        paras = re.split(r"\n{2,}", content.strip())
        if paras:
            status_lines = [paras[0].replace("\n", " ")[:300]]

    parts = [f"[PROJECT SNAPSHOT: {source_path or block_id}]"]
    parts.append(
        "\n## Status\n" + "\n".join(status_lines[:5])
        if status_lines
        else "\n## Status\n(not found)"
    )
    parts.append(
        "\n## Blockers\n" + "\n".join(blockers[:5])
        if blockers
        else "\n## Blockers\nNone identified"
    )
    parts.append(
        "\n## Next Steps\n" + "\n".join(next_steps[:8])
        if next_steps
        else "\n## Next Steps\n(not found)"
    )

    rendered = "\n".join(parts)
    return PrecomputedArtifact(
        block_id=block_id,
        artifact_type="project_snapshot",
        intent="plan",
        content=rendered,
        doc_type=doc_type.value,
        source_path=source_path,
        token_estimate=_estimate_tokens(rendered),
        metadata={
            "has_status": bool(status_lines),
            "blocker_count": len(blockers),
            "next_step_count": len(next_steps),
        },
    )


# ---------------------------------------------------------------------------
# Intent → artifact type mapping
# ---------------------------------------------------------------------------

_INTENT_TO_ARTIFACT: Dict[str, str] = {
    "query": "fact_card",
    "explain": "feature_table",
    "debug": "error_signature",
    "plan": "project_snapshot",
    # Aliases / related intents
    "search": "fact_card",
    "summarize": "fact_card",
    "create": "project_snapshot",
}

_DOC_TYPE_TO_ARTIFACTS: Dict[DocType, List[str]] = {
    DocType.CODE: ["fact_card", "error_signature"],
    DocType.CONFIG: ["fact_card", "feature_table"],
    DocType.NARRATIVE: ["fact_card", "project_snapshot"],
    DocType.CHANGELOG: ["fact_card"],
    DocType.ERROR_LOG: ["error_signature", "fact_card"],
    DocType.COMPARISON: ["feature_table", "fact_card"],
    DocType.PROJECT_PLAN: ["project_snapshot", "fact_card"],
    DocType.UNKNOWN: ["fact_card"],
}

_GENERATORS = {
    "fact_card": generate_fact_card,
    "feature_table": generate_feature_table,
    "error_signature": generate_error_signature,
    "project_snapshot": generate_project_snapshot,
}


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------


class PrecomputeStore:
    """Persist and retrieve precomputed artifacts on disk.

    Layout: {artifacts_dir}/{artifact_type}/{block_id}.json
    """

    def __init__(self, artifacts_dir: Optional[Path] = None):
        self.artifacts_dir = artifacts_dir or DEFAULT_ARTIFACTS_DIR
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, artifact_type: str, block_id: str) -> Path:
        type_dir = self.artifacts_dir / artifact_type
        type_dir.mkdir(parents=True, exist_ok=True)
        return type_dir / f"{block_id}.json"

    def save(self, artifact: PrecomputedArtifact) -> Path:
        """Write artifact to disk. Returns the saved path."""
        p = self._path(artifact.artifact_type, artifact.block_id)
        p.write_text(json.dumps(artifact.to_dict(), indent=2), encoding="utf-8")
        return p

    def load(self, artifact_type: str, block_id: str) -> Optional[PrecomputedArtifact]:
        """Load artifact from disk. Returns None if not found."""
        p = self._path(artifact_type, block_id)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return PrecomputedArtifact.from_dict(d)
        except (json.JSONDecodeError, KeyError):
            return None

    def exists(self, artifact_type: str, block_id: str) -> bool:
        return self._path(artifact_type, block_id).exists()

    def delete(self, artifact_type: str, block_id: str) -> bool:
        p = self._path(artifact_type, block_id)
        if p.exists():
            p.unlink()
            return True
        return False

    def list_block_artifacts(self, block_id: str) -> List[str]:
        """Return all artifact types available for a block_id."""
        found = []
        for atype in _GENERATORS:
            if self.exists(atype, block_id):
                found.append(atype)
        return found


# ---------------------------------------------------------------------------
# Precomputation pipeline
# ---------------------------------------------------------------------------


def precompute_for_block(
    block_id: str,
    content: str,
    risk_class: str = "narrative",
    source_path: str = "",
    artifacts_dir: Optional[Path] = None,
    force: bool = False,
) -> List[PrecomputedArtifact]:
    """Generate and store all relevant artifacts for a single block.

    Called at index time for each file. Idempotent — skips existing artifacts
    unless ``force=True``.

    Args:
        block_id:      Unique block identifier (from index.json).
        content:       Raw file content.
        risk_class:    Block risk class (code/config/narrative/protected).
        source_path:   Original source file path (for display).
        artifacts_dir: Override default artifacts directory.
        force:         Regenerate even if artifact already exists.

    Returns:
        List of PrecomputedArtifact objects generated (may be empty if all
        already existed and force=False).
    """
    # Protected content → skip
    if risk_class == "protected":
        return []

    store = PrecomputeStore(artifacts_dir)
    doc_type = detect_doc_type(content, risk_class, source_path)

    target_types = _DOC_TYPE_TO_ARTIFACTS.get(doc_type, ["fact_card"])
    generated: List[PrecomputedArtifact] = []

    for atype in target_types:
        if not force and store.exists(atype, block_id):
            continue  # Already computed
        generator = _GENERATORS.get(atype)
        if generator is None:
            continue
        artifact = generator(block_id, content, doc_type, source_path)
        store.save(artifact)
        generated.append(artifact)

    return generated


def get_precomputed_artifact(
    block_id: str,
    intent: str,
    artifacts_dir: Optional[Path] = None,
) -> Optional[PrecomputedArtifact]:
    """Retrieve a precomputed artifact matching block_id + intent.

    Returns None if no artifact exists (caller should fallback to raw content).

    Args:
        block_id:     Block identifier.
        intent:       Canonical intent string (e.g. "query", "debug", "plan").
        artifacts_dir: Override default artifacts directory.
    """
    artifact_type = _INTENT_TO_ARTIFACT.get(intent)
    if artifact_type is None:
        return None

    store = PrecomputeStore(artifacts_dir)
    return store.load(artifact_type, block_id)


# ---------------------------------------------------------------------------
# Bulk recompute helper (for CLI / rebuild scripts)
# ---------------------------------------------------------------------------


def recompute_all(
    blocks: Dict,
    blocks_dir: Path,
    artifacts_dir: Optional[Path] = None,
    force: bool = False,
    on_progress=None,
) -> Dict[str, int]:
    """Run precompute_for_block over all blocks in an index.

    Args:
        blocks:       The ``blocks`` dict from index.json.
        blocks_dir:   Path to the blocks/ directory (content .txt files).
        artifacts_dir: Override artifacts directory.
        force:        Regenerate all artifacts.
        on_progress:  Optional callback(block_id, artifact_count).

    Returns:
        Stats dict: {generated, skipped, errors}.
    """
    stats = {"generated": 0, "skipped": 0, "errors": 0}

    for block_id, meta in blocks.items():
        block_file = blocks_dir / f"{block_id}.txt"
        if not block_file.exists():
            stats["skipped"] += 1
            continue
        try:
            content = block_file.read_text(encoding="utf-8", errors="ignore")
            artifacts = precompute_for_block(
                block_id=block_id,
                content=content,
                risk_class=meta.get("risk_class", "narrative"),
                source_path=meta.get("source_path", ""),
                artifacts_dir=artifacts_dir,
                force=force,
            )
            stats["generated"] += len(artifacts)
            if not artifacts:
                stats["skipped"] += 1
            if on_progress:
                on_progress(block_id, len(artifacts))
        except Exception:
            stats["errors"] += 1

    return stats
