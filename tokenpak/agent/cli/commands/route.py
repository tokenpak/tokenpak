"""route command — model routing configuration and status."""

from __future__ import annotations


def run(action: str = "status", raw: bool = False) -> None:
    """Model routing control."""
    print(f"route {action}: not yet implemented (stub)")


try:
    import click

    @click.group("route")
    def route_cmd():
        """Model routing commands."""
        pass

    @route_cmd.command("status")
    @click.option("--raw", is_flag=True)
    def route_status(raw):
        """Show router status."""
        run(action="status", raw=raw)

    @route_cmd.command("on")
    def route_on():
        """Enable the deterministic router."""
        run(action="on")

    @route_cmd.command("off")
    def route_off():
        """Disable the deterministic router."""
        run(action="off")

except ImportError:
    pass
