"""trigger command — re-exported from agent triggers."""

from __future__ import annotations

try:
    # Re-export trigger_group from the agent triggers CLI module
    from tokenpak.agent.cli.trigger_cmd import trigger_group  # noqa: F401
except ImportError:
    try:
        import click

        @click.group("trigger", help="Manage event triggers and actions")
        def trigger_group():
            """Event triggers (agent triggers module not available)."""
            pass

    except ImportError:
        trigger_group = None
