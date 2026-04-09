"""compression command — compression pipeline stats and control."""

from __future__ import annotations


def run(raw: bool = False) -> None:
    """Print compression pipeline stats."""
    print("compression: not yet implemented (stub)")


try:
    import click

    @click.command("compression")
    @click.option("--raw", is_flag=True, help="Output raw JSON")
    def compression_cmd(raw):
        """Show compression pipeline stats."""
        run(raw=raw)

except ImportError:
    pass
