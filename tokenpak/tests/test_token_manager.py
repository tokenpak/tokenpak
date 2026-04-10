# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak/token_manager.py"""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from tokenpak.token_manager import (
    generate_token,
    load_or_create_token,
    regenerate_token,
    get_token,
    TOKEN_FILE,
)


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_token_dir():
    """Create a temporary directory for token file operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_token_file(temp_token_dir):
    """Mock TOKEN_FILE to point to a temporary location."""
    token_path = temp_token_dir / ".tokenpak" / "dashboard_token"
    with patch("tokenpak.token_manager.TOKEN_FILE", token_path):
        yield token_path


# ---------------------------------------------------------------------------
# Test: generate_token()
# ---------------------------------------------------------------------------

def test_generate_token_returns_hex_string():
    """generate_token() should return a valid 32-character hex string."""
    token = generate_token()
    assert isinstance(token, str)
    assert len(token) == 32
    # Verify it's valid hex
    try:
        int(token, 16)
    except ValueError:
        pytest.fail("Generated token is not valid hex")


def test_generate_token_randomness():
    """generate_token() should produce different values on successive calls."""
    token1 = generate_token()
    token2 = generate_token()
    assert token1 != token2, "Tokens should be random and unique"


def test_generate_token_never_empty():
    """generate_token() should never return an empty string."""
    for _ in range(10):
        token = generate_token()
        assert token
        assert len(token) > 0


# ---------------------------------------------------------------------------
# Test: load_or_create_token()
# ---------------------------------------------------------------------------

def test_load_existing_token(mock_token_file):
    """load_or_create_token() should load and return an existing token."""
    # Pre-write a token to the file
    mock_token_file.parent.mkdir(parents=True, exist_ok=True)
    test_token = "abcd1234efgh5678ijkl9012mnop3456"
    mock_token_file.write_text(test_token)
    
    result = load_or_create_token()
    assert result == test_token


def test_create_token_if_missing(mock_token_file):
    """load_or_create_token() should create a new token if file is missing."""
    # Ensure the file doesn't exist
    assert not mock_token_file.exists()
    
    token = load_or_create_token()
    
    # Should have created the file
    assert mock_token_file.exists()
    # Should have written a valid token
    assert len(token) == 32
    # File should contain the token
    assert mock_token_file.read_text().strip() == token


def test_load_or_create_token_idempotent(mock_token_file):
    """load_or_create_token() should return same token on multiple calls."""
    token1 = load_or_create_token()
    token2 = load_or_create_token()
    assert token1 == token2, "Repeated calls should return the same token"


def test_load_or_create_token_strips_whitespace(mock_token_file):
    """load_or_create_token() should strip whitespace from stored token."""
    mock_token_file.parent.mkdir(parents=True, exist_ok=True)
    # Write token with extra whitespace
    test_token = "abcd1234efgh5678ijkl9012mnop3456"
    mock_token_file.write_text(f"  {test_token}  \n")
    
    result = load_or_create_token()
    assert result == test_token


def test_load_or_create_sets_secure_permissions(mock_token_file):
    """load_or_create_token() should set file permissions to 0o600."""
    load_or_create_token()
    
    # Check file permissions
    stat_info = mock_token_file.stat()
    # 0o600 means owner read+write only
    file_mode = stat_info.st_mode & 0o777
    assert file_mode == 0o600, f"Expected 0o600 but got {oct(file_mode)}"


# ---------------------------------------------------------------------------
# Test: regenerate_token()
# ---------------------------------------------------------------------------

def test_regenerate_token_creates_new_token(mock_token_file):
    """regenerate_token() should create a new token and overwrite the file."""
    # Create initial token
    initial_token = load_or_create_token()
    
    # Regenerate
    new_token = regenerate_token()
    
    # New token should be different
    assert new_token != initial_token
    # File should contain the new token
    assert mock_token_file.read_text().strip() == new_token


def test_regenerate_token_valid_hex(mock_token_file):
    """regenerate_token() should return a valid hex string."""
    token = regenerate_token()
    assert len(token) == 32
    try:
        int(token, 16)
    except ValueError:
        pytest.fail("Regenerated token is not valid hex")


def test_regenerate_overwrites_missing_file(mock_token_file):
    """regenerate_token() should work even if no token file exists initially."""
    assert not mock_token_file.exists()
    
    token = regenerate_token()
    
    assert mock_token_file.exists()
    assert mock_token_file.read_text().strip() == token


def test_regenerate_token_sets_secure_permissions(mock_token_file):
    """regenerate_token() should set file permissions to 0o600."""
    regenerate_token()
    
    stat_info = mock_token_file.stat()
    file_mode = stat_info.st_mode & 0o777
    assert file_mode == 0o600


# ---------------------------------------------------------------------------
# Test: get_token()
# ---------------------------------------------------------------------------

def test_get_token_returns_existing_token(mock_token_file):
    """get_token() should return the current token from file."""
    test_token = "abcd1234efgh5678ijkl9012mnop3456"
    mock_token_file.parent.mkdir(parents=True, exist_ok=True)
    mock_token_file.write_text(test_token)
    
    result = get_token()
    assert result == test_token


def test_get_token_raises_if_missing(mock_token_file):
    """get_token() should raise FileNotFoundError if token file is missing."""
    assert not mock_token_file.exists()
    
    with pytest.raises(FileNotFoundError):
        get_token()


def test_get_token_strips_whitespace(mock_token_file):
    """get_token() should strip whitespace from the stored token."""
    test_token = "abcd1234efgh5678ijkl9012mnop3456"
    mock_token_file.parent.mkdir(parents=True, exist_ok=True)
    mock_token_file.write_text(f"\n  {test_token}  \n")
    
    result = get_token()
    assert result == test_token


def test_get_token_error_message_helpful(mock_token_file):
    """get_token() error message should suggest how to fix it."""
    assert not mock_token_file.exists()
    
    with pytest.raises(FileNotFoundError) as exc_info:
        get_token()
    
    # Check error message includes helpful info
    error_msg = str(exc_info.value).lower()
    assert "token" in error_msg
    assert "dashboard" in error_msg or "show-token" in error_msg.lower()


# ---------------------------------------------------------------------------
# Edge cases and stress tests
# ---------------------------------------------------------------------------

def test_token_file_directory_creation(mock_token_file):
    """load_or_create_token() should create parent directories if needed."""
    # Ensure parent dirs don't exist
    assert not mock_token_file.parent.exists()
    
    load_or_create_token()
    
    # Parent directory should now exist
    assert mock_token_file.parent.exists()


def test_regenerate_multiple_times(mock_token_file):
    """Calling regenerate_token() multiple times should produce different tokens."""
    tokens = set()
    for _ in range(5):
        token = regenerate_token()
        assert token not in tokens, "Regenerated tokens should be unique"
        tokens.add(token)


def test_load_or_create_after_regenerate(mock_token_file):
    """After regenerate_token(), load_or_create_token() should return same token."""
    regenerated = regenerate_token()
    loaded = load_or_create_token()
    assert regenerated == loaded


def test_token_format_consistency():
    """All token-generating functions should produce consistent format."""
    gen = generate_token()
    
    # All should be 32 hex chars
    assert len(gen) == 32
    assert all(c in "0123456789abcdef" for c in gen.lower())


def test_get_token_after_load_or_create(mock_token_file):
    """get_token() should return the same value as load_or_create_token()."""
    created = load_or_create_token()
    retrieved = get_token()
    assert created == retrieved


# ---------------------------------------------------------------------------
# Robustness and error handling
# ---------------------------------------------------------------------------

def test_token_with_leading_trailing_newlines(mock_token_file):
    """Should handle tokens with various whitespace edge cases."""
    test_token = "abcd1234efgh5678ijkl9012mnop3456"
    mock_token_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Test with various whitespace patterns
    whitespace_patterns = [
        f"\n{test_token}",
        f"{test_token}\n",
        f"\n{test_token}\n",
        f"  {test_token}  ",
        f"\t{test_token}\t",
    ]
    
    for pattern in whitespace_patterns:
        mock_token_file.write_text(pattern)
        result = get_token()
        assert result == test_token


def test_concurrent_token_access_simulation(mock_token_file):
    """Verify token operations don't corrupt state under repeated access."""
    # Simulate repeated rapid access
    for _ in range(10):
        load_or_create_token()
        result = get_token()
        assert len(result) == 32
        assert result.isalnum()
