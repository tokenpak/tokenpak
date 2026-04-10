"""
Unit tests for session_capsules.py

Tests cover:
- build_session_capsule: parsing, frontmatter, section extraction
- serialize_capsule: JSON serialization
- score_capsule_sections: section scoring logic
- capsule_retrieval_score: retrieval scoring with boost
- Edge cases: empty capsules, missing sections, invalid input
"""

import pytest
from tokenpak._internal.memory.session_capsules import (
    build_session_capsule,
    serialize_capsule,
    score_capsule_sections,
    capsule_retrieval_score,
    REQUIRED_CAPSULE_SECTIONS,
)


class TestBuildSessionCapsule:
    """Test capsule builder with various input formats."""

    def test_build_basic_capsule(self):
        """Test basic capsule with all required sections."""
        text = """\
---
session_id: sess-001
timestamp: 2026-03-27
---

# Decisions Made
- Deploy new proxy
- Enable HTTP100 keepalive

# Artifacts Created
- docs/PROXY-CONFIG.md
- test_session_capsules.py

# Action Items
- Review proxy config in production
- Monitor compression time

# Insights
- HTTP100 keepalive prevents SSE timeouts
- Session capsules improve memory efficiency

# Raw Transcript Reference
See original session log
"""
        capsule = build_session_capsule(text, "test.md")

        # Check structure
        assert set(capsule.keys()) == set(REQUIRED_CAPSULE_SECTIONS)

        # Check metadata
        assert capsule["session_metadata"]["source_path"] == "test.md"
        assert capsule["session_metadata"]["session_id"] == "sess-001"
        assert capsule["session_metadata"]["timestamp"] == "2026-03-27"
        assert "sha256" in capsule["session_metadata"]

        # Check sections populated
        assert len(capsule["decisions_made"]) == 2
        assert len(capsule["artifacts_created"]) == 2
        assert len(capsule["action_items"]) == 2
        assert len(capsule["insights"]) == 2

    def test_build_capsule_alias_normalization(self):
        """Test section name aliases are normalized."""
        text = """\
# Decisions
- Decision 1

# Artifacts
- Artifact 1

# Actions
- Action 1

# Insights
- Insight 1
"""
        capsule = build_session_capsule(text)

        assert len(capsule["decisions_made"]) == 1
        assert len(capsule["artifacts_created"]) == 1
        assert len(capsule["action_items"]) == 1
        assert len(capsule["insights"]) == 1

    def test_build_empty_capsule(self):
        """Test capsule with no section content."""
        text = "# Some Random Section\nNo structured content here"
        capsule = build_session_capsule(text)

        # All sections should exist but be empty
        assert capsule["decisions_made"] == []
        assert capsule["artifacts_created"] == []
        assert capsule["action_items"] == []
        assert capsule["insights"] == []

    def test_build_capsule_with_bullets(self):
        """Test bullet point extraction."""
        text = """\
# Decisions Made
- First decision
- Second decision
- Third decision
"""
        capsule = build_session_capsule(text)
        assert capsule["decisions_made"] == [
            "First decision",
            "Second decision",
            "Third decision",
        ]

    def test_build_capsule_with_multiline_bullets(self):
        """Test multiline content is flattened."""
        text = """\
# Decisions Made
- Deploy    to    prod
- Enable   multi-line  content
"""
        capsule = build_session_capsule(text)
        # Whitespace should be normalized
        assert capsule["decisions_made"] == [
            "Deploy to prod",
            "Enable multi-line content",
        ]

    def test_build_capsule_empty_input(self):
        """Test empty input produces valid structure."""
        capsule = build_session_capsule("")
        assert capsule["decisions_made"] == []
        assert capsule["artifacts_created"] == []
        assert capsule["action_items"] == []
        assert capsule["insights"] == []

    def test_build_capsule_whitespace_handling(self):
        """Test whitespace is handled correctly."""
        text = """\
# Decisions Made

- Item with     extra spaces

# Artifacts Created

-   Leading spaces on bullet
"""
        capsule = build_session_capsule(text)
        assert capsule["decisions_made"] == ["Item with extra spaces"]
        assert capsule["artifacts_created"] == ["Leading spaces on bullet"]

    def test_build_capsule_sha256_consistency(self):
        """Test SHA256 hash is consistent for same input."""
        text = "Test content"
        capsule1 = build_session_capsule(text)
        capsule2 = build_session_capsule(text)

        assert (
            capsule1["session_metadata"]["sha256"]
            == capsule2["session_metadata"]["sha256"]
        )

    def test_build_capsule_source_path_tracking(self):
        """Test source path is tracked in metadata and reference."""
        path = "/path/to/session.md"
        capsule = build_session_capsule("", path)

        assert capsule["session_metadata"]["source_path"] == path
        assert capsule["raw_transcript_reference"]["source_path"] == path


class TestSerializeCapsule:
    """Test JSON serialization."""

    def test_serialize_basic(self):
        """Test capsule serialization to JSON."""
        capsule = build_session_capsule("# Decisions Made\n- Test")
        serialized = serialize_capsule(capsule)

        assert isinstance(serialized, str)
        assert '"decisions_made"' in serialized

    def test_serialize_deserialize_roundtrip(self):
        """Test capsule survives serialize/deserialize."""
        import json

        original = build_session_capsule(
            """\
---
session_id: test-123
---
# Decisions Made
- Decision A
# Artifacts Created
- Artifact B
"""
        )

        serialized = serialize_capsule(original)
        deserialized = json.loads(serialized)

        assert deserialized["session_metadata"]["session_id"] == "test-123"
        assert deserialized["decisions_made"] == ["Decision A"]
        assert deserialized["artifacts_created"] == ["Artifact B"]

    def test_serialize_unicode_handling(self):
        """Test Unicode content is preserved."""
        capsule = build_session_capsule("# Decisions Made\n- Café ☕ 🚀")
        serialized = serialize_capsule(capsule)

        assert "Café" in serialized
        assert "☕" in serialized
        assert "🚀" in serialized


class TestScoreCapsuleSections:
    """Test section scoring logic."""

    def test_score_empty_capsule(self):
        """Test scoring of empty sections."""
        capsule = build_session_capsule("")
        scores = score_capsule_sections(capsule)

        # All sections present in scores
        for section in REQUIRED_CAPSULE_SECTIONS:
            assert section in scores

        # List-based sections should be 0 (empty)
        assert scores["decisions_made"] == 0.0
        assert scores["artifacts_created"] == 0.0
        assert scores["action_items"] == 0.0
        assert scores["insights"] == 0.0

        # session_metadata and raw_transcript_reference are dicts with content
        # so they get non-zero scores even when "empty"
        assert scores["session_metadata"] > 0.0

    def test_score_full_capsule(self):
        """Test scoring with populated sections."""
        text = """\
# Decisions Made
- Decision 1

# Artifacts Created
- Artifact 1
- Artifact 2

# Action Items
- Action 1

# Insights
- Insight 1
- Insight 2
"""
        capsule = build_session_capsule(text)
        scores = score_capsule_sections(capsule)

        # Scores should be > 0 for populated sections
        assert scores["decisions_made"] > 0
        assert scores["artifacts_created"] > 0
        assert scores["action_items"] > 0
        assert scores["insights"] > 0

    def test_score_section_weights(self):
        """Test section weights are applied correctly."""
        text = """\
# Decisions Made
- D1

# Artifacts Created
- A1

# Action Items
- AI1

# Insights
- I1
"""
        capsule = build_session_capsule(text)
        scores = score_capsule_sections(capsule)

        # Decisions should have highest weight (3.0)
        # Artifacts (2.5), Actions (2.0), Insights (1.8)
        assert scores["decisions_made"] > scores["artifacts_created"]
        assert scores["artifacts_created"] > scores["action_items"]
        assert scores["action_items"] > scores["insights"]

    def test_score_returns_dict(self):
        """Test score returns all required sections."""
        capsule = build_session_capsule("Test")
        scores = score_capsule_sections(capsule)

        for section in REQUIRED_CAPSULE_SECTIONS:
            assert section in scores
            assert isinstance(scores[section], float)


class TestCapsuleRetrievalScore:
    """Test retrieval scoring with boost calculation."""

    def test_retrieval_score_none_capsule(self):
        """Test score with None capsule."""
        base = 2.5
        score = capsule_retrieval_score(base, None)
        assert score == base

    def test_retrieval_score_empty_capsule(self):
        """Test score with empty capsule."""
        capsule = build_session_capsule("")
        base = 2.5
        score = capsule_retrieval_score(base, capsule)

        # Empty capsule has no boost
        assert score == base

    def test_retrieval_score_high_signal_capsule(self):
        """Test score boosted by high-signal sections."""
        text = """\
# Decisions Made
- Decision 1
- Decision 2

# Artifacts Created
- Artifact 1
- Artifact 2

# Action Items
- Action 1

# Insights
- Insight 1
"""
        capsule = build_session_capsule(text)
        base = 2.5
        score = capsule_retrieval_score(base, capsule)

        # Should have positive boost
        assert score > base

    def test_retrieval_score_max_boost(self):
        """Test boost is capped at 5.0."""
        # Create capsule with very high signal
        text = """\
# Decisions Made
""" + "\n".join([f"- Decision {i}" for i in range(100)])

        capsule = build_session_capsule(text)
        base = 1.0
        score = capsule_retrieval_score(base, capsule)

        # Boost should be capped: score <= base + 5.0
        assert score <= base + 5.0


class TestIntegration:
    """Integration tests for full workflow."""

    def test_full_workflow(self):
        """Test complete workflow: build -> serialize -> score."""
        # Build
        text = """\
---
session_id: integration-test
user_id: user-123
---

# Decisions Made
- Choice A
- Choice B

# Artifacts Created
- docs/README.md
- src/main.py

# Action Items
- Review PR
- Deploy to staging

# Insights
- Learned about session capsules
- Improved memory efficiency
"""
        capsule = build_session_capsule(text, "integration-test.md")

        # Verify structure
        assert capsule["session_metadata"]["session_id"] == "integration-test"
        assert len(capsule["decisions_made"]) == 2

        # Serialize
        serialized = serialize_capsule(capsule)
        assert isinstance(serialized, str)
        assert len(serialized) > 0

        # Score
        scores = score_capsule_sections(capsule)
        assert all(v >= 0 for v in scores.values())

        # Retrieval score
        retrieval_score = capsule_retrieval_score(3.0, capsule)
        assert retrieval_score > 3.0

    def test_real_world_session_capsule(self):
        """Test realistic session capsule structure."""
        text = """\
---
session_id: tokenPak-work-2026-03-27
timestamp: 2026-03-27T06:00:00Z
agent: Alpha
---

# Decisions Made
- Deploy memory-promoter as systemd timer
- Create comprehensive proxy config documentation
- Write session capsule test suite

# Artifacts Created
- .config/systemd/user/memory-promoter.timer
- docs/PROXY-CONFIG.md (9k+)
- test_session_capsules.py (15+ tests)

# Action Items
- Push commits to vault
- Wait for QA approval
- Monitor memory-promoter runs

# Insights
- Systemd timers are simpler alternative to tokenpak cron when RPC fails
- Documentation helps with onboarding and troubleshooting
- Comprehensive test coverage prevents regressions

# Raw Transcript Reference
Full session log at .tokenpak/agents/alpha/memory/2026-03-27.md
"""

        capsule = build_session_capsule(text, "2026-03-27-session.md")

        # Validate complete workflow
        assert len(capsule["decisions_made"]) == 3
        assert len(capsule["artifacts_created"]) == 3
        assert len(capsule["action_items"]) == 3
        assert len(capsule["insights"]) == 3

        scores = score_capsule_sections(capsule)
        total_score = sum(scores.values())
        assert total_score > 0

        retrieval_score = capsule_retrieval_score(2.0, capsule)
        assert retrieval_score > 2.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
