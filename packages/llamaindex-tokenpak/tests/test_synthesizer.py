"""Tests for TokenPakSynthesizer."""

from llamaindex_tokenpak import TokenPakSynthesizer


def test_synthesizer_creation():
    """Test synthesizer creation."""
    synthesizer = TokenPakSynthesizer(budget=4000)
    assert synthesizer.budget == 4000
