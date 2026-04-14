"""Claude Code registry adapter for TokenPak.

Importing this package does **not** auto-register the adapter.  Call
:func:`register` explicitly, or install the adapter via the entry-points
mechanism so that :func:`tokenpak.extensions.discover` picks it up.

Example::

    from tokenpak.core.registry.claude_code import register
    register()
"""
from tokenpak.core.registry.claude_code.adapter import ClaudeCodeAdapter
from tokenpak.core.registry.claude_code.config import ClaudeCodeConfig

__all__ = ["ClaudeCodeAdapter", "ClaudeCodeConfig", "register"]


def register() -> None:
    """Register a default :class:`ClaudeCodeAdapter` instance with the extensions registry.

    Idempotent: calling this multiple times overwrites the previous entry
    with a new default-config adapter (tokenpak.extensions.register logs a
    warning on overwrite).
    """
    from tokenpak import extensions  # noqa: PLC0415 — lazy to avoid circular import

    extensions.register("claude-code", ClaudeCodeAdapter())
