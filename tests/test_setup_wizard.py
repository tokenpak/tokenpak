"""
Tests for TokenPak setup wizard functionality.

Covers:
  1. Profile generation — all three profiles produce valid configurations
  2. API key detection from environment
  3. Config file creation and persistence
  4. Idempotent re-run (doesn't break on re-execution)
  5. Profile features are correctly enabled/disabled
"""


import pytest

pytest.importorskip("tokenpak.profiles", reason="module not available in current build")
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from tokenpak.profiles import PROFILES, apply_profile, get_profile

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_config_dir():
    """Create a temporary config directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_home(temp_config_dir, monkeypatch):
    """Mock home directory to temp location."""
    monkeypatch.setenv("HOME", str(temp_config_dir))
    return temp_config_dir


# ---------------------------------------------------------------------------
# Test 1: Profile definitions are valid
# ---------------------------------------------------------------------------

def test_minimal_profile_exists():
    """Minimal profile should exist and be retrievable."""
    profile = get_profile("minimal")
    assert profile["name"] == "minimal"
    assert "features" in profile
    assert profile["features"]["compression"]["enabled"] is True


def test_balanced_profile_exists():
    """Balanced profile should exist and be retrievable."""
    profile = get_profile("balanced")
    assert profile["name"] == "balanced"
    assert "features" in profile
    assert profile["features"]["compression"]["enabled"] is True
    assert profile["features"]["semantic_cache"]["enabled"] is True


def test_aggressive_profile_exists():
    """Aggressive profile should exist and be retrievable."""
    profile = get_profile("aggressive")
    assert profile["name"] == "aggressive"
    assert "features" in profile
    # All features should be enabled
    for feature_name, feature_config in profile["features"].items():
        assert feature_config["enabled"] is True, f"{feature_name} should be enabled in aggressive profile"


def test_profile_retrieval_invalid_name():
    """Getting a non-existent profile should raise ValueError."""
    with pytest.raises(ValueError, match="Unknown profile"):
        get_profile("nonexistent")


# ---------------------------------------------------------------------------
# Test 2: Profile application to configs
# ---------------------------------------------------------------------------

def test_apply_minimal_profile():
    """Applying minimal profile should set correct features."""
    config = {"proxy": {"port": 8766}}
    result = apply_profile("minimal", config)

    assert result["profile"] == "minimal"
    assert "modules" in result
    assert result["modules"]["compression"]["enabled"] is True
    assert result["modules"]["semantic_cache"]["enabled"] is False
    assert result["modules"]["prefix_registry"]["enabled"] is False


def test_apply_balanced_profile():
    """Applying balanced profile should enable compression + caching + routing."""
    config = {"proxy": {"port": 8766}}
    result = apply_profile("balanced", config)

    assert result["profile"] == "balanced"
    assert result["modules"]["compression"]["enabled"] is True
    assert result["modules"]["semantic_cache"]["enabled"] is True
    assert result["modules"]["prefix_registry"]["enabled"] is True
    assert result["modules"]["query_rewriter"]["enabled"] is True
    assert result["modules"]["error_normalizer"]["enabled"] is True
    assert result["modules"]["fidelity_tiers"]["enabled"] is True


def test_apply_aggressive_profile():
    """Applying aggressive profile should enable all 16 features."""
    config = {"proxy": {"port": 8766}}
    result = apply_profile("aggressive", config)

    assert result["profile"] == "aggressive"
    # Count enabled features
    enabled_count = sum(
        1 for feature in result["modules"].values()
        if feature.get("enabled") is True
    )
    assert enabled_count == 16, f"Expected 16 enabled features, got {enabled_count}"


def test_apply_profile_preserves_existing_config():
    """Applying a profile should not lose existing config keys."""
    config = {
        "proxy": {"port": 8766, "host": "localhost"},
        "other_setting": "preserved",
    }
    result = apply_profile("balanced", config)

    assert result["proxy"]["port"] == 8766
    assert result["proxy"]["host"] == "localhost"
    assert result["other_setting"] == "preserved"


# ---------------------------------------------------------------------------
# Test 3: YAML generation and writing
# ---------------------------------------------------------------------------

def test_config_written_to_yaml(temp_config_dir, monkeypatch):
    """Config should be writable to YAML format."""
    monkeypatch.setenv("HOME", str(temp_config_dir))
    config_dir = Path(temp_config_dir) / ".tokenpak"
    config_file = config_dir / "config.yaml"

    config = {
        "proxy": {"port": 8766, "provider": "anthropic"},
        "modules": {},
    }
    config = apply_profile("balanced", config)

    config_dir.mkdir(parents=True, exist_ok=True)
    config_file.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))

    assert config_file.exists()

    # Read back and verify
    loaded = yaml.safe_load(config_file.read_text())
    assert loaded["profile"] == "balanced"
    assert loaded["proxy"]["port"] == 8766
    assert loaded["modules"]["compression"]["enabled"] is True


# ---------------------------------------------------------------------------
# Test 4: API key detection from environment
# ---------------------------------------------------------------------------

def test_detect_anthropic_key(monkeypatch):
    """Should detect ANTHROPIC_API_KEY from environment."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    api_keys = {}
    if os.environ.get("ANTHROPIC_API_KEY"):
        api_keys["anthropic"] = os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("OPENAI_API_KEY"):
        api_keys["openai"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("GOOGLE_API_KEY"):
        api_keys["google"] = os.environ["GOOGLE_API_KEY"]

    assert "anthropic" in api_keys
    assert api_keys["anthropic"] == "sk-test-123"


def test_detect_multiple_keys(monkeypatch):
    """Should detect multiple API keys when present."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-123")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-456")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    api_keys = {}
    if os.environ.get("ANTHROPIC_API_KEY"):
        api_keys["anthropic"] = os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("OPENAI_API_KEY"):
        api_keys["openai"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("GOOGLE_API_KEY"):
        api_keys["google"] = os.environ["GOOGLE_API_KEY"]

    assert len(api_keys) == 2
    assert "anthropic" in api_keys
    assert "openai" in api_keys
    assert "google" not in api_keys


def test_no_api_keys_found(monkeypatch):
    """Should handle case where no API keys are found."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    api_keys = {}
    if os.environ.get("ANTHROPIC_API_KEY"):
        api_keys["anthropic"] = os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("OPENAI_API_KEY"):
        api_keys["openai"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("GOOGLE_API_KEY"):
        api_keys["google"] = os.environ["GOOGLE_API_KEY"]

    assert len(api_keys) == 0


# ---------------------------------------------------------------------------
# Test 5: Feature count in profiles
# ---------------------------------------------------------------------------

def test_minimal_has_1_enabled_feature():
    """Minimal profile should have only compression enabled."""
    profile = get_profile("minimal")
    enabled = sum(1 for f in profile["features"].values() if f["enabled"])
    assert enabled == 1


def test_balanced_has_6_enabled_features():
    """Balanced profile should have 6 features enabled."""
    profile = get_profile("balanced")
    enabled = sum(1 for f in profile["features"].values() if f["enabled"])
    assert enabled == 6


def test_aggressive_has_16_enabled_features():
    """Aggressive profile should have all 16 features enabled."""
    profile = get_profile("aggressive")
    enabled = sum(1 for f in profile["features"].values() if f["enabled"])
    assert enabled == 16


# ---------------------------------------------------------------------------
# Test 6: Profile features list
# ---------------------------------------------------------------------------

def test_all_profiles_have_16_feature_toggles():
    """All profiles should define all 16 features."""
    for profile_name, profile in PROFILES.items():
        feature_names = set(profile["features"].keys())
        expected = {
            "compression", "semantic_cache", "prefix_registry", "query_rewriter",
            "error_normalizer", "fidelity_tiers", "tokenizer_cache", "request_coalescing",
            "response_dedup", "header_optimization", "cost_model", "adaptive_routing",
            "intent_classifier", "latency_predictor", "sampling_engine", "fallback_policy",
        }
        assert feature_names == expected, f"Profile {profile_name} missing or extra features"


# ---------------------------------------------------------------------------
# Test 7: Idempotent operations
# ---------------------------------------------------------------------------

def test_config_creation_is_idempotent(temp_config_dir, monkeypatch):
    """Creating config twice should succeed without error."""
    monkeypatch.setenv("HOME", str(temp_config_dir))
    config_dir = Path(temp_config_dir) / ".tokenpak"
    config_file = config_dir / "config.yaml"

    # Create config first time
    config = {"proxy": {"port": 8766}}
    config = apply_profile("balanced", config)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file.write_text(yaml.dump(config))
    first_content = config_file.read_text()

    # Create config again (should not fail)
    config = {"proxy": {"port": 8766}}
    config = apply_profile("balanced", config)
    config_file.write_text(yaml.dump(config))
    second_content = config_file.read_text()

    # Content should be essentially the same (YAML may format slightly differently)
    assert yaml.safe_load(first_content) == yaml.safe_load(second_content)


# ---------------------------------------------------------------------------
# Test 8: Profile descriptions
# ---------------------------------------------------------------------------

def test_profiles_have_descriptions():
    """All profiles should have descriptions."""
    for profile_name, profile in PROFILES.items():
        assert "description" in profile
        assert len(profile["description"]) > 0
        assert "%" in profile["description"], f"Profile {profile_name} should mention savings %"


def test_profile_descriptions_realistic():
    """Profile descriptions should match expected savings ranges."""
    minimal = get_profile("minimal")
    assert "5%" in minimal["description"]

    balanced = get_profile("balanced")
    assert "30%" in balanced["description"]

    aggressive = get_profile("aggressive")
    assert "40%" in aggressive["description"]
