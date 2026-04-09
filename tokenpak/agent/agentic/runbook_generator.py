"""TokenPak — Self-Generated Runbook Engine

After a task is completed and validated, this module converts the experience
into a reusable runbook artifact stored in ~/.tokenpak/runbooks/.

Key concepts
------------
- RunbookEntry: dataclass capturing title, trigger symptoms, ordered steps,
  validation instructions, error class, and success tracking.
- RunbookDB: CRUD + indexing + duplicate detection.
  Persists to ~/.tokenpak/runbooks/<slug>.md (markdown) and an index JSON.
- Trigger guard: only generates runbooks when conditions are met
  (success + validation passed + seen ≥2 times + specific enough).
- Retrieval: by error_class, task_type, or free-text keyword search.

Runbook Markdown Template
--------------------------
# <Title>
## Trigger
<Symptoms that indicate this runbook applies>
## Steps
1. <Step>
2. ...
## Validation
<How to verify the fix worked>
## Context
- First seen: <ISO date>
- Success rate: <N/M>
- Avg cost: <tokens> tokens

Usage
-----
    from tokenpak.agent.agentic.runbook_generator import RunbookDB, RunbookEntry, maybe_generate

    db = RunbookDB()
    # After a successful task completion:
    maybe_generate(db, episode)

    # Retrieve relevant runbook:
    rb = db.find_by_error_class("port_bind_failure")
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_RUNBOOKS_DIR = Path.home() / ".tokenpak" / "runbooks"
DEFAULT_INDEX_PATH = DEFAULT_RUNBOOKS_DIR / "_index.json"

# Minimum occurrences of a similar task before we create a runbook
MIN_OCCURRENCES = 2

# Minimum confidence that the pattern is specific (non-trivial)
MIN_SPECIFICITY_LEN = 10  # chars in trigger description


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str) -> str:
    """Convert text to a safe filename slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:64]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RunbookEntry:
    """A reusable runbook artifact derived from a successful task episode."""

    runbook_id: str
    title: str
    error_class: str  # e.g. "port_bind_failure", "auth_error"
    task_type: str  # e.g. "proxy_restart", "token_refresh"
    trigger_symptoms: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    validation: str = ""
    first_seen: str = field(default_factory=_now_iso)
    last_seen: str = field(default_factory=_now_iso)
    success_count: int = 1
    total_count: int = 1
    avg_cost_tokens: float = 0.0
    keywords: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.first_seen:
            self.first_seen = _now_iso()
        if not self.last_seen:
            self.last_seen = _now_iso()

    @property
    def success_rate_str(self) -> str:
        return f"{self.success_count}/{self.total_count}"

    @property
    def slug(self) -> str:
        return _slugify(self.title)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_markdown(rb: RunbookEntry) -> str:
    """Render a RunbookEntry to the standard runbook markdown template."""
    symptoms = (
        "\n".join(f"- {s}" for s in rb.trigger_symptoms)
        if rb.trigger_symptoms
        else "- (not specified)"
    )
    steps = (
        "\n".join(f"{i + 1}. {s}" for i, s in enumerate(rb.steps))
        if rb.steps
        else "1. (not specified)"
    )
    validation = rb.validation or "(not specified)"
    avg_cost = f"{rb.avg_cost_tokens:.0f}" if rb.avg_cost_tokens else "unknown"

    return f"""# {rb.title}

## Trigger
{symptoms}

## Steps
{steps}

## Validation
{validation}

## Context
- First seen: {rb.first_seen}
- Success rate: {rb.success_rate_str}
- Avg cost: {avg_cost} tokens
- Error class: {rb.error_class}
- Task type: {rb.task_type}
- Keywords: {", ".join(rb.keywords) if rb.keywords else "none"}
"""


# ---------------------------------------------------------------------------
# Index persistence
# ---------------------------------------------------------------------------


def _entry_to_dict(rb: RunbookEntry) -> dict:
    return asdict(rb)


def _entry_from_dict(d: dict) -> RunbookEntry:
    return RunbookEntry(
        runbook_id=d.get("runbook_id", str(uuid.uuid4())),
        title=d.get("title", "Untitled"),
        error_class=d.get("error_class", "unknown"),
        task_type=d.get("task_type", "unknown"),
        trigger_symptoms=d.get("trigger_symptoms", []),
        steps=d.get("steps", []),
        validation=d.get("validation", ""),
        first_seen=d.get("first_seen", _now_iso()),
        last_seen=d.get("last_seen", _now_iso()),
        success_count=d.get("success_count", 1),
        total_count=d.get("total_count", 1),
        avg_cost_tokens=float(d.get("avg_cost_tokens", 0.0)),
        keywords=d.get("keywords", []),
    )


def _load_index(index_path: Path) -> Dict[str, RunbookEntry]:
    if not index_path.exists():
        return {}
    try:
        raw = json.loads(index_path.read_text())
        return {k: _entry_from_dict(v) for k, v in raw.items()}
    except (json.JSONDecodeError, KeyError):
        return {}


def _save_index(index: Dict[str, RunbookEntry], index_path: Path) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps({k: _entry_to_dict(v) for k, v in index.items()}, indent=2))


# ---------------------------------------------------------------------------
# RunbookDB
# ---------------------------------------------------------------------------


class RunbookDB:
    """Storage and retrieval for self-generated runbooks."""

    def __init__(
        self,
        runbooks_dir: Optional[Path | str] = None,
        index_path: Optional[Path | str] = None,
    ) -> None:
        self.runbooks_dir = Path(runbooks_dir) if runbooks_dir else DEFAULT_RUNBOOKS_DIR
        self.index_path = Path(index_path) if index_path else self.runbooks_dir / "_index.json"
        self._index: Dict[str, RunbookEntry] = _load_index(self.index_path)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, rb: RunbookEntry) -> RunbookEntry:
        """Persist a new runbook. Raises ValueError if id already exists."""
        if rb.runbook_id in self._index:
            raise ValueError(f"Runbook {rb.runbook_id!r} already exists; use update().")
        self._index[rb.runbook_id] = rb
        self._write_markdown(rb)
        self._persist()
        return rb

    def get(self, runbook_id: str) -> Optional[RunbookEntry]:
        return self._index.get(runbook_id)

    def update(self, rb: RunbookEntry) -> RunbookEntry:
        rb.last_seen = _now_iso()
        self._index[rb.runbook_id] = rb
        self._write_markdown(rb)
        self._persist()
        return rb

    def delete(self, runbook_id: str) -> bool:
        rb = self._index.pop(runbook_id, None)
        if rb is None:
            return False
        md_path = self.runbooks_dir / f"{rb.slug}.md"
        if md_path.exists():
            md_path.unlink()
        self._persist()
        return True

    def list_all(self) -> List[RunbookEntry]:
        return list(self._index.values())

    def count(self) -> int:
        return len(self._index)

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def find_duplicate(
        self,
        error_class: str,
        task_type: str,
        title: str = "",
    ) -> Optional[RunbookEntry]:
        """Return an existing runbook that covers the same error_class + task_type pair.

        If *title* is provided, also checks for slug collision.
        """
        slug = _slugify(title) if title else ""
        for rb in self._index.values():
            if rb.error_class == error_class and rb.task_type == task_type:
                return rb
            if slug and rb.slug == slug:
                return rb
        return None

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def find_by_error_class(self, error_class: str) -> Optional[RunbookEntry]:
        """Return best (most successes) runbook for the given error class."""
        candidates = [rb for rb in self._index.values() if rb.error_class == error_class]
        if not candidates:
            return None
        return max(candidates, key=lambda rb: rb.success_count)

    def find_by_task_type(self, task_type: str) -> List[RunbookEntry]:
        return [rb for rb in self._index.values() if rb.task_type == task_type]

    def search(self, query: str) -> List[RunbookEntry]:
        """Full-text search across title, keywords, trigger_symptoms, steps."""
        q = query.lower()
        results = []
        for rb in self._index.values():
            haystack = " ".join(
                [rb.title, rb.error_class, rb.task_type]
                + rb.keywords
                + rb.trigger_symptoms
                + rb.steps
            ).lower()
            if q in haystack:
                results.append(rb)
        return results

    # ------------------------------------------------------------------
    # Learning loop
    # ------------------------------------------------------------------

    def record_outcome(
        self, runbook_id: str, *, success: bool, tokens_used: float = 0.0
    ) -> Optional[RunbookEntry]:
        """Update success/failure counts and rolling avg cost."""
        rb = self._index.get(runbook_id)
        if rb is None:
            return None
        rb.total_count += 1
        if success:
            rb.success_count += 1
        # Rolling average cost
        if tokens_used > 0:
            n = rb.total_count
            rb.avg_cost_tokens = rb.avg_cost_tokens * (n - 1) / n + tokens_used / n
        return self.update(rb)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_markdown(self, rb: RunbookEntry) -> None:
        self.runbooks_dir.mkdir(parents=True, exist_ok=True)
        md_path = self.runbooks_dir / f"{rb.slug}.md"
        md_path.write_text(render_markdown(rb))

    def _persist(self) -> None:
        _save_index(self._index, self.index_path)

    def reload(self) -> None:
        self._index = _load_index(self.index_path)


# ---------------------------------------------------------------------------
# Trigger guard
# ---------------------------------------------------------------------------


@dataclass
class Episode:
    """Represents a completed task episode for runbook generation evaluation."""

    task_type: str
    error_class: str
    title: str
    trigger_symptoms: List[str]
    steps: List[str]
    validation: str
    success: bool
    validation_passed: bool
    tokens_used: float = 0.0
    keywords: List[str] = field(default_factory=list)
    # How many times has this pattern been seen before (caller must supply)
    prior_occurrences: int = 0


def _is_specific_enough(episode: Episode) -> bool:
    """Check that the trigger description is specific, not a generic catch-all."""
    if not episode.trigger_symptoms:
        return False
    total_len = sum(len(s) for s in episode.trigger_symptoms)
    return total_len >= MIN_SPECIFICITY_LEN


def should_generate(episode: Episode) -> bool:
    """Return True when the episode meets all runbook generation conditions.

    Conditions:
    1. Task completed successfully
    2. Validation passed
    3. Similar task seen ≥ MIN_OCCURRENCES times (caller tracks this)
    4. Pattern specific enough (trigger description non-trivial)
    """
    return (
        episode.success
        and episode.validation_passed
        and episode.prior_occurrences >= MIN_OCCURRENCES
        and _is_specific_enough(episode)
    )


def maybe_generate(
    db: RunbookDB,
    episode: Episode,
) -> Optional[RunbookEntry]:
    """Generate or update a runbook from *episode* if conditions are met.

    - If a matching runbook already exists → update its outcome (no duplicate).
    - If conditions not met → return None.
    - Otherwise → create and persist a new runbook.
    """
    if not should_generate(episode):
        return None

    # Check for duplicate
    existing = db.find_duplicate(
        error_class=episode.error_class,
        task_type=episode.task_type,
        title=episode.title,
    )
    if existing is not None:
        # Update learning loop instead of creating a duplicate
        db.record_outcome(
            existing.runbook_id, success=episode.success, tokens_used=episode.tokens_used
        )
        return existing

    rb = RunbookEntry(
        runbook_id=str(uuid.uuid4()),
        title=episode.title,
        error_class=episode.error_class,
        task_type=episode.task_type,
        trigger_symptoms=episode.trigger_symptoms,
        steps=episode.steps,
        validation=episode.validation,
        avg_cost_tokens=episode.tokens_used,
        keywords=episode.keywords,
    )
    db.add(rb)
    return rb


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------

_db: Optional[RunbookDB] = None


def _get_db() -> RunbookDB:
    global _db
    if _db is None:
        _db = RunbookDB()
    return _db


def generate_from_episode(episode: Episode) -> Optional[RunbookEntry]:
    """Convenience wrapper using the module-level singleton DB."""
    return maybe_generate(_get_db(), episode)


def get_runbook(error_class: str) -> Optional[RunbookEntry]:
    """Quick retrieval by error class using the singleton DB."""
    return _get_db().find_by_error_class(error_class)
