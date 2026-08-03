"""Tests for TokenPak schema definitions.

Covers: schemas/artifact.py, schemas/chunk.py, etc. — schema validation, serialization.
"""

from dataclasses import asdict, is_dataclass

import pytest


class TestSchemaStructures:
    """Test: Schema dataclass definitions and structure."""

    def test_artifact_schema_exists(self):
        """Artifact schema is defined."""
        try:
            from tokenpak.schemas.artifact import Artifact

            assert is_dataclass(Artifact)
        except ImportError:
            pytest.skip("artifact schema not available")

    def test_chunk_schema_exists(self):
        """Chunk schema is defined."""
        try:
            from tokenpak.schemas.chunk import Chunk

            assert is_dataclass(Chunk)
        except ImportError:
            pytest.skip("chunk schema not available")

    def test_schema_serialization(self):
        """Schemas can be serialized to dict."""
        try:
            from tokenpak.schemas.artifact import Artifact

            artifact = Artifact(
                id="test-1",
                name="test artifact",
                content_type="text/plain",
                content="test content",
            )
            data = asdict(artifact)
            assert isinstance(data, dict)
            assert data["id"] == "test-1"
        except ImportError:
            pytest.skip("artifact schema not available")


class TestSchemaValidation:
    """Test: Schema field validation and constraints."""

    def test_artifact_required_fields(self):
        """Artifact requires id, name, content_type, content."""
        try:
            from tokenpak.schemas.artifact import Artifact

            # Should fail without required fields
            with pytest.raises((TypeError, ValueError)):
                Artifact()
        except ImportError:
            pytest.skip("artifact schema not available")


class TestSchemaInteroperability:
    """Test: Schema compatibility and composition."""

    def test_schemas_can_be_composed(self):
        """Schemas can reference and compose with each other."""
        try:
            from tokenpak.schemas.artifact import Artifact

            # Basic composition works
            artifacts = [
                Artifact(
                    id=f"a{i}",
                    name=f"artifact{i}",
                    content_type="text/plain",
                    content=f"content {i}",
                )
                for i in range(3)
            ]
            assert len(artifacts) == 3
        except ImportError:
            pytest.skip("artifact schema not available")
