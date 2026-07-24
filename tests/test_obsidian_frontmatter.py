import pytest

pytest.importorskip("tokenpak.connectors.obsidian", reason="module not available in current build")
from tokenpak.connectors.base import ConnectorConfig
from tokenpak.connectors.obsidian import ObsidianConnector


def _connector() -> ObsidianConnector:
    return ObsidianConnector(ConnectorConfig(name="obsidian", source_path="/tmp"))


def test_duplicate_keys_detected_in_lenient_mode():
    connector = _connector()
    content = """---
assigned_to: sue
assigned_to: kevin
priority: p1
---
Body
"""

    data = connector.extract_frontmatter(content)
    diag = connector.last_frontmatter_diagnostics

    assert "assigned_to" in diag.duplicate_keys
    assert data["assigned_to"] == ["kevin"]
    assert data["priority"] == "p1"


def test_strict_mode_rejects_duplicate_keys():
    connector = _connector()
    content = """---
assigned_to: sue
assigned_to: kevin
---
Body
"""

    try:
        connector.extract_frontmatter(content, strict=True)
        assert False, "Expected strict mode to reject duplicate keys"
    except ValueError as exc:
        assert "Duplicate frontmatter keys detected" in str(exc)


def test_malformed_yaml_lenient_vs_strict():
    connector = _connector()
    content = """---
key: [1, 2
---
Body
"""

    assert connector.extract_frontmatter(content) == {}

    try:
        connector.extract_frontmatter(content, strict=True)
        assert False, "Expected strict mode to reject malformed YAML"
    except ValueError as exc:
        assert "Malformed YAML frontmatter" in str(exc)


def test_canonical_output_is_deterministic_and_sorted():
    connector = _connector()
    a = """---
z: 1
a: 2
---
Body
"""
    b = """---
a: 2
z: 1
---
Body
"""

    data_a = connector.extract_frontmatter(a)
    data_b = connector.extract_frontmatter(b)

    assert data_a == data_b
    assert list(data_a.keys()) == ["a", "z"]


def test_multi_assignee_normalizes_to_list():
    connector = _connector()
    content = """---
assigned_to: sue, kevin, trix
---
Body
"""

    data = connector.extract_frontmatter(content)
    assert data["assigned_to"] == ["sue", "kevin", "trix"]


def test_clean_frontmatter_backward_compatible():
    connector = _connector()
    content = """---
title: Hello
tags:
  - one
  - two
---
Body
"""

    data = connector.extract_frontmatter(content)
    assert data["title"] == "Hello"
    assert data["tags"] == ["one", "two"]
    assert connector.last_frontmatter_diagnostics.errors == []
    assert connector.last_frontmatter_diagnostics.duplicate_keys == []
