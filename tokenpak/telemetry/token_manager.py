"""
token_manager.py — Dashboard token generation and management for TokenPak.

Token file: ~/.tokenpak/dashboard_token
Permissions: 0o600 (owner read-write only)
Format: 32-char random hex string
"""

import secrets
from pathlib import Path

TOKEN_FILE = Path.home() / ".tokenpak" / "dashboard_token"


def generate_token() -> str:
    """Generate a 32-char random hex token."""
    return secrets.token_hex(16)


def load_or_create_token() -> str:
    """Load token from file, or create a new one if missing."""
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    return _write_token(generate_token())


def regenerate_token() -> str:
    """Create a new token, overwrite the existing file."""
    return _write_token(generate_token())


def get_token() -> str:
    """
    Return the current token.

    Raises FileNotFoundError if token file is missing.
    Use load_or_create_token() if auto-creation is acceptable.
    """
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            "Dashboard token not found. " "Run: tokenpak dashboard --show-token  (auto-creates one)"
        )
    return TOKEN_FILE.read_text().strip()


def _write_token(token: str) -> str:
    """Write token to file with secure permissions and return it."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)
    TOKEN_FILE.chmod(0o600)
    return token
