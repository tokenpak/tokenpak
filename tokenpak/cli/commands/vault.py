"""vault command — vault index management."""

from __future__ import annotations


def run(action: str = "status", raw: bool = False) -> None:
    """Vault index management."""
    print(f"vault {action}: not yet implemented (stub)")


try:
    import click

    @click.group("vault")
    def vault_cmd():
        """Vault index management commands."""
        pass

    @vault_cmd.command("status")
    @click.option("--raw", is_flag=True)
    def vault_status(raw):
        """Show vault index status."""
        run(action="status", raw=raw)

    @vault_cmd.command("reindex")
    @click.option("--verbose", "-v", is_flag=True)
    def vault_reindex(verbose):
        """Rebuild vault block index."""
        run(action="reindex")

except ImportError:
    pass
