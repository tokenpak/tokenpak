
import pytest
pytest.importorskip("tokenpak._internal.memory.session_capsules", reason="module not available in current build")
from tokenpak._internal.memory.session_capsules import (
    REQUIRED_CAPSULE_SECTIONS,
    build_session_capsule,
    capsule_retrieval_score,
    score_capsule_sections,
    serialize_capsule,
)
from tokenpak.vault.retrieval import sort_retrieval_results


RAW_SESSION = """---
id: sess-001
title: Example Session
---

# Session Metadata
owner: trix

# Decisions Made
- Move retrieval ranking to capsule-aware scoring

# Artifacts Created
- tokenpak/agent/memory/session_capsules.py

# Action Items
- Add deterministic tests

# Insights
- Structured memory beats transcript dumps

# Raw Transcript Reference
full chat transcript in source log
"""


def test_build_session_capsule_is_deterministic():
    c1 = build_session_capsule(RAW_SESSION, source_path="memory/2026-03-10.md")
    c2 = build_session_capsule(RAW_SESSION, source_path="memory/2026-03-10.md")

    assert c1 == c2
    assert serialize_capsule(c1) == serialize_capsule(c2)


def test_capsule_includes_required_sections_and_reference():
    capsule = build_session_capsule(RAW_SESSION, source_path="memory/2026-03-10.md")

    for section in REQUIRED_CAPSULE_SECTIONS:
        assert section in capsule

    ref = capsule["raw_transcript_reference"]
    assert ref["source_path"] == "memory/2026-03-10.md"
    assert ref["sha256"]
    assert "fallback" in ref


def test_utility_scores_are_stable_and_high_signal_weighted():
    capsule = build_session_capsule(RAW_SESSION, source_path="memory/2026-03-10.md")
    scores_1 = score_capsule_sections(capsule)
    scores_2 = score_capsule_sections(capsule)

    assert scores_1 == scores_2
    assert scores_1["decisions_made"] > scores_1["raw_transcript_reference"]
    assert scores_1["artifacts_created"] > scores_1["raw_transcript_reference"]


def test_retrieval_prioritizes_capsule_high_signal_sections_over_raw_only():
    rich_capsule = build_session_capsule(RAW_SESSION, source_path="memory/2026-03-10.md")
    raw_only_capsule = {
        "session_metadata": {"source_path": "memory/raw.md"},
        "decisions_made": [],
        "artifacts_created": [],
        "action_items": [],
        "insights": [],
        "raw_transcript_reference": {"source_path": "memory/raw.md", "sha256": "abc"},
    }

    assert capsule_retrieval_score(10.0, rich_capsule) > capsule_retrieval_score(10.0, raw_only_capsule)

    results = [
        ({"source_path": "raw.md", "block_id": "b", "metadata": {"session_capsule": raw_only_capsule}}, 10.0),
        ({"source_path": "rich.md", "block_id": "a", "metadata": {"session_capsule": rich_capsule}}, 10.0),
    ]
    ranked = sort_retrieval_results(results)
    assert ranked[0][0]["source_path"] == "rich.md"
