"""Tests for the tokenpak.agent backward-compatibility shim (FIN-11)."""

import importlib
import warnings

import pytest


def test_agent_agentic_shim_emits_warning():
    """Importing tokenpak.agent.agentic should work but emit DeprecationWarning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import tokenpak.agent
        mod = tokenpak.agent.agentic  # triggers __getattr__
        assert mod is not None
    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warnings) >= 1
    msg = str(deprecation_warnings[0].message)
    assert "tokenpak.agent.agentic" in msg
    assert "tokenpak.agentic" in msg or "deprecated" in msg.lower()
    assert "1.1.0" in msg


def test_agent_vault_shim_emits_warning():
    """Importing tokenpak.agent.vault should work but emit DeprecationWarning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import tokenpak.agent
        mod = tokenpak.agent.vault
        assert mod is not None
    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warnings) >= 1
    msg = str(deprecation_warnings[0].message)
    assert "tokenpak.agent.vault" in msg
    assert "tokenpak.vault" in msg or "deprecated" in msg.lower()


def test_agent_shim_redirects_to_correct_module():
    """tokenpak.agent.vault should resolve to the same object as tokenpak.vault."""
    import tokenpak.vault as canonical_vault
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        import tokenpak.agent
        shim_vault = tokenpak.agent.vault
    assert shim_vault is canonical_vault


def test_agent_shim_unknown_attribute_raises():
    """Accessing a non-existent attribute should raise AttributeError (not DeprecationWarning)."""
    import tokenpak.agent
    with pytest.raises(AttributeError, match="has no attribute"):
        _ = tokenpak.agent.nonexistent_module_xyz


def test_agent_directory_has_only_init():
    """The agent/ directory should contain exactly __init__.py and nothing else."""
    import pathlib
    agent_dir = pathlib.Path(importlib.util.find_spec("tokenpak.agent").origin).parent
    files = [f.name for f in agent_dir.iterdir() if not f.name.startswith("__pycache__")]
    assert files == ["__init__.py"], (
        f"agent/ should contain only __init__.py, found: {files}"
    )
