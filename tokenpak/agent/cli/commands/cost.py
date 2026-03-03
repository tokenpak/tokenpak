"""cost command — token usage and cost reporting."""

from __future__ import annotations

# Stub — full implementation pending DB integration

def run(days: int = 7, model: str = None, raw: bool = False) -> None:
    """Print cost summary."""
    print("cost: not yet implemented (stub)")


try:
    import click

    @click.command("cost")
    @click.option("--days", type=int, default=7, help="Report window in days")
    @click.option("--model", default=None, help="Filter by model name")
    @click.option("--raw", is_flag=True, help="Output raw JSON")
    def cost_cmd(days, model, raw):
        """Show token usage and cost report."""
        run(days=days, model=model, raw=raw)

except ImportError:
    pass
