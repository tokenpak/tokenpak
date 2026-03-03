"""license command — show license and edition info."""

from __future__ import annotations


def run() -> None:
    """Print license and edition info."""
    print("TOKENPAK  |  License")
    print("────────────────────────")
    print()
    print("  Edition:   OSS (Community)")
    print("  License:   Apache 2.0")
    print()
    print("  Pro features (not included in this build):")
    print("    • DirectiveApplier (rule-based compression)")
    print("    • Advanced vault context injection")
    print("    • Priority support")
    print()
    print("  https://github.com/anthropics/tokenpak")


try:
    import click

    @click.command("license")
    def license_cmd():
        """Show license and edition info."""
        run()

except ImportError:
    pass
