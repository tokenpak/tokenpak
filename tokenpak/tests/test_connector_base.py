# SPDX-License-Identifier: Apache-2.0
"""Unit tests for connectors.base and connectors.base_source."""

import pytest

from tokenpak.sources import get_connector, list_connectors
from tokenpak.sources.base import (
    Connector,
    ConnectorConfig,
    RemoteFile,
)
from tokenpak.sources.base_source import (
    Provenance,
    SourceAdapter,
    SourceFetchError,
)


# ---------------------------------------------------------------------------
# ConnectorConfig
# ---------------------------------------------------------------------------


class TestConnectorConfig:
    def test_required_fields(self):
        cfg = ConnectorConfig(name="test", source_path="/tmp/foo")
        assert cfg.name == "test"
        assert cfg.source_path == "/tmp/foo"

    def test_defaults(self):
        cfg = ConnectorConfig(name="x", source_path="/y")
        assert cfg.auth_token is None
        assert cfg.sync_interval_minutes == 5
        assert cfg.include_patterns == ["**/*"]
        assert cfg.exclude_patterns == []
        assert cfg.max_file_size_mb == 10

    def test_custom_values(self):
        cfg = ConnectorConfig(
            name="gh",
            source_path="owner/repo",
            auth_token="token123",
            sync_interval_minutes=30,
            include_patterns=["**/*.py"],
            exclude_patterns=["**/__pycache__/**"],
            max_file_size_mb=5,
        )
        assert cfg.auth_token == "token123"
        assert cfg.sync_interval_minutes == 30
        assert cfg.include_patterns == ["**/*.py"]
        assert cfg.exclude_patterns == ["**/__pycache__/**"]
        assert cfg.max_file_size_mb == 5

    def test_include_patterns_independent_per_instance(self):
        cfg1 = ConnectorConfig(name="a", source_path="/a")
        cfg2 = ConnectorConfig(name="b", source_path="/b")
        cfg1.include_patterns.append("**/*.py")
        assert cfg2.include_patterns == ["**/*"]

    def test_exclude_patterns_independent_per_instance(self):
        cfg1 = ConnectorConfig(name="a", source_path="/a")
        cfg2 = ConnectorConfig(name="b", source_path="/b")
        cfg1.exclude_patterns.append("*.tmp")
        assert cfg2.exclude_patterns == []


# ---------------------------------------------------------------------------
# RemoteFile
# ---------------------------------------------------------------------------


class TestRemoteFile:
    def test_required_fields(self):
        rf = RemoteFile(
            path="notes/foo.md",
            source_id="abc123",
            size_bytes=1024,
            modified_at="2024-01-01T00:00:00",
        )
        assert rf.path == "notes/foo.md"
        assert rf.source_id == "abc123"
        assert rf.size_bytes == 1024
        assert rf.modified_at == "2024-01-01T00:00:00"

    def test_optional_fields_default_none(self):
        rf = RemoteFile(path="x", source_id="y", size_bytes=0, modified_at="2024-01-01")
        assert rf.content_hash is None
        assert rf.file_type is None

    def test_custom_optional_fields(self):
        rf = RemoteFile(
            path="a.md",
            source_id="s1",
            size_bytes=100,
            modified_at="2024-01-01",
            content_hash="sha256:abc",
            file_type="markdown",
        )
        assert rf.content_hash == "sha256:abc"
        assert rf.file_type == "markdown"


# ---------------------------------------------------------------------------
# Connector (abstract base)
# ---------------------------------------------------------------------------


class ConcreteConnector(Connector):
    """Minimal concrete implementation for testing base class."""

    name = "test"
    tier = "free"

    def connect(self) -> bool:
        return True

    def list_files(self, since=None):
        return iter([])

    def get_content(self, file):
        return b"content"


class TestConnectorBase:
    def test_init_sets_config(self):
        cfg = ConnectorConfig(name="test", source_path="/tmp")
        conn = ConcreteConnector(cfg)
        assert conn.config is cfg

    def test_init_sync_state_empty(self):
        cfg = ConnectorConfig(name="test", source_path="/tmp")
        conn = ConcreteConnector(cfg)
        assert conn.get_sync_state() == {}

    def test_set_and_get_sync_state(self):
        cfg = ConnectorConfig(name="test", source_path="/tmp")
        conn = ConcreteConnector(cfg)
        state = {"last_sync": "2024-01-01", "cursor": "abc"}
        conn.set_sync_state(state)
        assert conn.get_sync_state() == state

    def test_disconnect_is_noop(self):
        cfg = ConnectorConfig(name="test", source_path="/tmp")
        conn = ConcreteConnector(cfg)
        conn.disconnect()  # Should not raise

    def test_sync_state_not_shared_between_instances(self):
        cfg = ConnectorConfig(name="test", source_path="/tmp")
        c1 = ConcreteConnector(cfg)
        c2 = ConcreteConnector(cfg)
        c1.set_sync_state({"x": 1})
        assert c2.get_sync_state() == {}

    def test_abstract_methods_enforced(self):
        """Connector cannot be instantiated without implementing abstract methods."""
        with pytest.raises(TypeError):
            Connector(ConnectorConfig(name="x", source_path="/y"))  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# get_connector / list_connectors
# ---------------------------------------------------------------------------


class TestGetConnector:
    def test_get_local_connector(self):
        cfg = ConnectorConfig(name="local", source_path="/tmp")
        conn = get_connector("local", cfg)
        assert conn.name == "local"

    def test_get_unknown_connector_raises(self):
        cfg = ConnectorConfig(name="x", source_path="/tmp")
        with pytest.raises(ValueError, match="Unknown connector"):
            get_connector("nonexistent_connector_xyz", cfg)

    def test_list_connectors_contains_local(self):
        connectors = list_connectors()
        assert "local" in connectors

    def test_list_connectors_returns_list(self):
        assert isinstance(list_connectors(), list)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_required_fields(self):
        p = Provenance(
            source_type="filesystem",
            source_id="/path/to/file.md",
            source_version="sha256:abc123",
            fetched_at="2024-01-01T00:00:00Z",
        )
        assert p.source_type == "filesystem"
        assert p.source_id == "/path/to/file.md"
        assert p.source_version == "sha256:abc123"
        assert p.fetched_at == "2024-01-01T00:00:00Z"

    def test_title_defaults_empty(self):
        p = Provenance(
            source_type="url",
            source_id="https://example.com",
            source_version="etag123",
            fetched_at="2024-01-01T00:00:00Z",
        )
        assert p.title == ""

    def test_custom_title(self):
        p = Provenance(
            source_type="notion",
            source_id="page-id-123",
            source_version="2024-01-01T00:00:00Z",
            fetched_at="2024-01-01T00:00:00Z",
            title="My Page",
        )
        assert p.title == "My Page"


# ---------------------------------------------------------------------------
# SourceFetchError
# ---------------------------------------------------------------------------


class TestSourceFetchError:
    def test_is_exception(self):
        err = SourceFetchError("something went wrong")
        assert isinstance(err, Exception)

    def test_message(self):
        err = SourceFetchError("fetch failed: timeout")
        assert "fetch failed" in str(err)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(SourceFetchError, match="timeout"):
            raise SourceFetchError("timeout")


# ---------------------------------------------------------------------------
# SourceAdapter (abstract base)
# ---------------------------------------------------------------------------


class ConcreteAdapter(SourceAdapter):
    source_type = "test"

    def ingest(self, source_id: str, **kwargs):
        return ("content", Provenance(
            source_type="test", source_id=source_id,
            source_version="v1", fetched_at=self._now(),
        ))

    def has_changed(self, source_id: str, cached_version: str, **kwargs) -> bool:
        return cached_version != "v1"


class TestSourceAdapterBase:
    def test_now_returns_iso_string(self):
        adapter = ConcreteAdapter()
        ts = adapter._now()
        assert "T" in ts
        assert ts.endswith("+00:00") or ts.endswith("Z") or "+" in ts

    def test_sha256_returns_hex_string(self):
        adapter = ConcreteAdapter()
        h = adapter._sha256("hello world")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_sha256_deterministic(self):
        adapter = ConcreteAdapter()
        assert adapter._sha256("test") == adapter._sha256("test")

    def test_sha256_different_for_different_inputs(self):
        adapter = ConcreteAdapter()
        assert adapter._sha256("foo") != adapter._sha256("bar")

    def test_abstract_methods_enforced(self):
        with pytest.raises(TypeError):
            SourceAdapter()  # type: ignore[abstract]

    def test_concrete_ingest(self):
        adapter = ConcreteAdapter()
        content, prov = adapter.ingest("my-id")
        assert content == "content"
        assert prov.source_id == "my-id"

    def test_concrete_has_changed_true(self):
        adapter = ConcreteAdapter()
        assert adapter.has_changed("x", "old-version") is True

    def test_concrete_has_changed_false(self):
        adapter = ConcreteAdapter()
        assert adapter.has_changed("x", "v1") is False
