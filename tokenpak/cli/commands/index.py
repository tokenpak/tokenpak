"""index command — vault file indexer CLI."""

from __future__ import annotations

import os
import sys


def _get_indexer():
    """Lazy import to keep startup fast."""
    from tokenpak.agent.vault import VaultIndexer
    from tokenpak.agent.vault.blocks import BlockStore

    store_path = os.environ.get(
        "TOKENPAK_VAULT_INDEX",
        os.path.expanduser("~/.tokenpak/vault_index.json"),
    )
    os.makedirs(os.path.dirname(store_path), exist_ok=True)
    return VaultIndexer(block_store=BlockStore(store_path))


def run_index_status() -> None:
    """Show indexed file count by type."""
    indexer = _get_indexer()
    stats = indexer.stats_by_type()
    total = stats["total_files"]
    print("Vault Index Status")
    print(f"{'─' * 40}")
    print(f"  Total indexed files: {total}")
    if total == 0:
        print("  (no files indexed yet — run: tokenpak index <path>)")
        return
    print()
    by_type = stats.get("by_type", {})
    if by_type:
        print("  By type:")
        for ftype, count in sorted(by_type.items()):
            print(f"    {ftype:<10} {count:>6} files")
    by_ext = stats.get("by_extension", {})
    if by_ext:
        print()
        print("  By extension:")
        for ext, count in sorted(by_ext.items(), key=lambda x: -x[1])[:20]:
            print(f"    {ext:<12} {count:>6} files")
    full = indexer.stats()
    print()
    print(f"  Tokens raw:          {full.get('total_raw_tokens', 0):,}")
    print(f"  Tokens compressed:   {full.get('total_compressed_tokens', 0):,}")
    print(f"  Symbols indexed:     {full.get('total_symbols', 0):,}")


def run_index_path(path: str, verbose: bool = False) -> None:
    """Index a directory path."""
    root = os.path.expanduser(path)
    if not os.path.isdir(root):
        print(f"✖ Not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    indexer = _get_indexer()
    print(f"Indexing: {root}")
    print(f"{'─' * 40}")

    def on_progress(p: str) -> None:
        if verbose:
            rel = os.path.relpath(p, root)
            print(f"  ✓ {rel}")

    results = indexer.index_directory(root, on_progress=on_progress)

    print(f"  Files found:      {results['files_found']:>6}")
    print(f"  Files indexed:    {results['files_indexed']:>6}")
    print(f"  Files skipped:    {results['files_skipped']:>6}")
    print(f"  Tokens raw:       {results['tokens_raw']:>10,}")
    print(f"  Tokens saved:     {results['tokens_saved']:>10,}")
    dur = results["duration_ms"]
    print(f"  Duration:         {dur}ms")
    print()
    if results["files_indexed"] > 0:
        pct = results["tokens_saved"] / max(results["tokens_raw"], 1) * 100
        print(f"  ✓ Done — {pct:.1f}% token reduction")
    else:
        print("  ⚠ No supported files found")


try:
    import click

    @click.command("index")
    @click.argument("path", required=False, default=None)
    @click.option("--status", is_flag=True, help="Show index status (file count by type)")
    @click.option("--verbose", "-v", is_flag=True, help="Show each file as it's indexed")
    def index_cmd(path, status, verbose):
        """Index files into vault block storage.

        Examples:

        \b
          tokenpak index ~/projects/myapp     # index a directory
          tokenpak index --status             # show indexed file counts by type
        """
        if status:
            run_index_status()
        elif path:
            run_index_path(path, verbose=verbose)
        else:
            click.echo(click.get_current_context().get_help())

except ImportError:
    # Fallback if click not installed
    def index_cmd(*args, **kwargs):  # type: ignore
        print("click not installed; index command unavailable")
