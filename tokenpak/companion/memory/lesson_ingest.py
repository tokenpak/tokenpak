"""
Lesson Ingestion Module

Extracts lessons from vault daily logs (markdown) and populates the DecisionMemoryDB.
Parses sections like "## Lessons Learned", "## Notes", decision patterns, and stores them.

Usage:
    from lesson_ingest import extract_lessons, ingest_from_vault
    from decision_memory import DecisionMemoryDB

    db = DecisionMemoryDB()
    lessons = extract_lessons("~/.tokenpak/memory/2026-03-27.md")
    count = ingest_from_vault("~/.tokenpak", db)
"""

import os
import re
from typing import Any, Dict, List

from tokenpak.companion.memory.decision_memory import DecisionMemoryDB


def extract_lessons(filepath: str) -> List[Dict[str, Any]]:
    """
    Parse a vault daily log markdown file and extract lessons.

    Looks for:
    - "## Lessons Learned" sections
    - "## Notes" sections with decision/insight content
    - "## Status" or "## Result" patterns that contain insights
    - Task completion summaries with outcomes

    Args:
        filepath: path to markdown daily log file

    Returns:
        List of lesson dictionaries with keys:
        - lesson: the lesson/insight text
        - section: which section it came from (e.g., "Lessons Learned", "Notes", "Task Summary")
        - task_id: extracted task ID if found
        - timestamp: extracted from filename or file stat
        - confidence: initial confidence (0.7 for standard lessons, 0.9 for explicit "Lesson")
    """
    lessons = []

    filepath = os.path.expanduser(filepath)
    if not os.path.exists(filepath):
        return lessons

    # Extract date from filename (YYYY-MM-DD.md)
    filename = os.path.basename(filepath)
    date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
    file_date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}" if date_match else None

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Split into sections
    sections = re.split(r'^## ', content, flags=re.MULTILINE)

    for section in sections[1:]:  # skip first empty split
        lines = section.split('\n')
        section_name = lines[0].strip()
        section_content = '\n'.join(lines[1:]).strip()

        # Extract lessons from explicit "Lessons Learned" section
        if 'lesson' in section_name.lower():
            # Each bullet/line in this section is a lesson
            for line in section_content.split('\n'):
                line = line.strip()
                if line and line.startswith('-'):
                    lesson_text = line.lstrip('-').strip()
                    if lesson_text:
                        lessons.append({
                            'lesson': lesson_text,
                            'section': section_name,
                            'task_id': None,
                            'timestamp': file_date,
                            'confidence': 0.9
                        })

        # Extract insights from "Notes" section
        elif 'note' in section_name.lower():
            for line in section_content.split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    lessons.append({
                        'lesson': line,
                        'section': section_name,
                        'task_id': None,
                        'timestamp': file_date,
                        'confidence': 0.7
                    })

        # Extract task completion patterns from task summary sections
        elif any(x in section_name.lower() for x in ['task', 'work', 'status', 'result']):
            # Look for patterns like "Task X: description → outcome"
            task_match = re.search(r'([A-Z]+-[A-Z0-9-]+)[:|\s]+(.+?)(?:→|—|-{2,}|✓|✅)(.*?)(?:\n\n|\n\*\*|$)', section_content, re.DOTALL)
            if task_match:
                task_id = task_match.group(1)
                task_desc = task_match.group(2).strip()
                task_outcome = task_match.group(3).strip() if task_match.group(3) else ''

                # Extract decision/lesson from outcome
                if task_outcome:
                    lessons.append({
                        'lesson': f"Task {task_id}: {task_outcome}",
                        'section': section_name,
                        'task_id': task_id,
                        'timestamp': file_date,
                        'confidence': 0.8
                    })

            # Also extract any bold patterns as insights (e.g., **Action**: ...)
            bold_patterns = re.findall(r'\*\*([^*]+)\*\*:\s*([^\n]+)', section_content)
            for key, value in bold_patterns:
                if key.lower() in ['lesson', 'insight', 'finding', 'recommendation', 'action', 'result']:
                    lessons.append({
                        'lesson': f"{key}: {value}",
                        'section': section_name,
                        'task_id': None,
                        'timestamp': file_date,
                        'confidence': 0.8
                    })

    return lessons


MARKDOWN_SUFFIXES = ('.md', '.markdown')


def _record_lessons(lessons: List[Dict[str, Any]], db: DecisionMemoryDB, source: str) -> int:
    """Write a list of extracted lessons to the DB. Returns count written.

    Uses the same deterministic ``query`` key shape as :func:`ingest_from_vault`
    (``lesson_<timestamp>_<task_id>``) so callers that later dedupe by query
    hash can do so.  NOTE: ``DecisionMemoryDB.record`` currently always inserts
    (it does not upsert on the query hash), so re-running ingestion re-inserts —
    this matches the existing vault-path behavior and is intentionally not
    changed here (out of scope for this change).
    """
    count = 0
    for lesson in lessons:
        query = f"lesson_{lesson['timestamp']}_{lesson.get('task_id', 'general')}"
        db.record(
            query=query,
            decision=lesson['lesson'],
            confidence=lesson['confidence'],
            notes=f"source: {source}, section: {lesson['section']}",
        )
        count += 1
    return count


def ingest_from_dir(directory: str, db: DecisionMemoryDB) -> int:
    """
    Recursively ingest lessons from any directory of Markdown notes.

    This is the generic "bring your own knowledge base" path: it does NOT
    require the vault ``03_AGENT_PACKS/<agent>/memory/`` schema.  Every
    ``.md`` / ``.markdown`` file under ``directory`` (at any depth) is parsed
    with the same :func:`extract_lessons` parser used for vault logs.

    Missing, unreadable, or empty directories return ``0`` without raising,
    preserving the companion's fail-open posture.  Use :func:`ingest_sources`
    for a structured per-source status report.

    Args:
        directory: path to a directory holding the user's Markdown notes
        db: DecisionMemoryDB instance to write to

    Returns:
        Total count of lessons ingested
    """
    directory = os.path.expanduser(directory)
    if not os.path.isdir(directory):
        return 0

    total_ingested = 0
    for root, _dirs, files in os.walk(directory):
        for filename in sorted(files):
            if filename.lower().endswith(MARKDOWN_SUFFIXES):
                filepath = os.path.join(root, filename)
                try:
                    lessons = extract_lessons(filepath)
                except (OSError, UnicodeDecodeError):
                    # Skip files we cannot read; never abort the whole walk.
                    continue
                rel = os.path.relpath(filepath, directory)
                total_ingested += _record_lessons(lessons, db, source=rel)
    return total_ingested


def ingest_sources(
    db: DecisionMemoryDB,
    vault_dir: str | None = None,
    memory_dirs: "list | None" = None,
) -> Dict[str, Any]:
    """
    Ingest from all configured memory sources and report per-source status.

    Orchestrates both ingestion paths so a fresh user gets a self-explaining
    result instead of a bare ``0``:

    - ``vault_dir`` (optional): vault-schema ingestion via
      :func:`ingest_from_vault` (backwards-compatible default).
    - ``memory_dirs`` (optional): generic per-directory ingestion via
      :func:`ingest_from_dir` ("bring your own knowledge base").

    Each source carries a ``reason`` classification so callers (status/doctor)
    can distinguish *no source configured* from *configured but
    missing/unreadable* from *present but no matching files*.

    Returns:
        ``{"total": int, "sources": [{"path", "kind", "ingested", "reason"}]}``
    """
    sources: List[Dict[str, Any]] = []
    total = 0

    if vault_dir:
        vp = os.path.expanduser(vault_dir)
        packs = os.path.join(vp, '03_AGENT_PACKS')
        if not os.path.isdir(packs):
            sources.append({"path": vault_dir, "kind": "vault",
                            "ingested": 0, "reason": "missing-or-not-vault-schema"})
        else:
            n = ingest_from_vault(vault_dir, db)
            sources.append({"path": vault_dir, "kind": "vault", "ingested": n,
                            "reason": "ok" if n else "present-but-no-matching-files"})
            total += n

    for d in (memory_dirs or []):
        ds = str(d)
        dp = os.path.expanduser(ds)
        if not os.path.exists(dp):
            sources.append({"path": ds, "kind": "memory-dir", "ingested": 0,
                            "reason": "missing"})
        elif not os.path.isdir(dp):
            sources.append({"path": ds, "kind": "memory-dir", "ingested": 0,
                            "reason": "not-a-directory"})
        else:
            n = ingest_from_dir(ds, db)
            sources.append({"path": ds, "kind": "memory-dir", "ingested": n,
                            "reason": "ok" if n else "present-but-no-matching-files"})
            total += n

    if not sources:
        sources.append({"path": None, "kind": None, "ingested": 0,
                        "reason": "no-source-configured"})

    return {"total": total, "sources": sources}


def ingest_from_vault(vault_dir: str, db: DecisionMemoryDB) -> int:
    """
    Walk vault daily logs and ingest all lessons into the DecisionMemoryDB.

    Scans all files matching ``<vault_dir>/03_AGENT_PACKS/<agent>/memory/YYYY-MM-DD.md``
    and extracts lessons, populating the database.  This is the vault-schema
    path; for arbitrary user note directories use :func:`ingest_from_dir`.

    Args:
        vault_dir: path to vault root directory
        db: DecisionMemoryDB instance to write to

    Returns:
        Total count of lessons ingested
    """
    vault_dir = os.path.expanduser(vault_dir)
    memory_dir = os.path.join(vault_dir, '03_AGENT_PACKS')

    total_ingested = 0

    if not os.path.isdir(memory_dir):
        return 0

    # Walk all agent memory directories
    for agent_dir in os.listdir(memory_dir):
        agent_memory_path = os.path.join(memory_dir, agent_dir, 'memory')

        if not os.path.isdir(agent_memory_path):
            continue

        # Process all YYYY-MM-DD.md files
        for filename in sorted(os.listdir(agent_memory_path)):
            if re.match(r'\d{4}-\d{2}-\d{2}\.md$', filename):
                filepath = os.path.join(agent_memory_path, filename)
                lessons = extract_lessons(filepath)

                for lesson in lessons:
                    # Record in DecisionMemoryDB
                    query = f"lesson_{lesson['timestamp']}_{lesson.get('task_id', 'general')}"

                    record_id = db.record(
                        query=query,
                        decision=lesson['lesson'],
                        confidence=lesson['confidence'],
                        notes=f"source: {agent_dir}/memory/{filename}, section: {lesson['section']}"
                    )

                    total_ingested += 1

    return total_ingested


if __name__ == '__main__':
    # Simple test
    import sys

    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        lessons = extract_lessons(filepath)
        print(f"Extracted {len(lessons)} lessons from {filepath}:")
        for lesson in lessons:
            print(f"  - [{lesson['confidence']}] {lesson['lesson']}")
    else:
        print("Usage: python3 lesson_ingest.py <path-to-daily-log.md>")
