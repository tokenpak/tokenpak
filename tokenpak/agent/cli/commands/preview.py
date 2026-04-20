"""
TokenPak Preview Command

Shows compression result BEFORE sending to LLM (dry-run mode).
Core UX command for understanding token savings.
"""

import json
from pathlib import Path


def register_preview(subparsers):
    """Register preview subcommand."""
    p = subparsers.add_parser(
        "preview",
        help="Preview compression result for input text (dry-run)",
    )
    p.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Input text to preview (or reads from stdin)",
    )
    p.add_argument(
        "--file",
        type=str,
        help="Read input from file instead of command line",
    )
    p.add_argument(
        "--raw",
        action="store_true",
        help="Show raw compression output (no formatting)",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed block breakdown",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (machine-readable)",
    )
    p.set_defaults(func=cmd_preview)


def cmd_preview(args):
    """Run compression dry-run and show preview."""
    import sys

    from tokenpak.compression.core import Compressor
    from tokenpak.formatting import OutputFormatter, OutputMode

    # Get input text
    if args.file:
        text = Path(args.file).read_text()
    elif args.input:
        text = args.input
    else:
        text = sys.stdin.read()

    if not text.strip():
        print("Error: No input provided.")
        sys.exit(1)

    # Run compression (dry-run)
    try:
        compressor = Compressor(mode="hybrid")
        result = compressor.compress(text, dry_run=True)
    except Exception as e:
        print(f"Error during compression: {e}", file=sys.stderr)
        sys.exit(1)

    # Format output
    if args.json:
        output = {
            "input_tokens": result.get("input_tokens", len(text.split())),
            "output_tokens": result.get("output_tokens", 0),
            "saved_tokens": result.get("saved_tokens", 0),
            "compression_ratio": result.get("compression_ratio", 0.0),
            "retained_blocks": result.get("retained_blocks", []),
            "removed_blocks": result.get("removed_blocks", []),
            "flags": result.get("flags", []),
        }
        print(json.dumps(output, indent=2))
    elif args.raw:
        print(f"Input:     {result.get('input_tokens', 0):,} tokens")
        print(f"Output:    {result.get('output_tokens', 0):,} tokens")
        print(
            f"Saved:     {result.get('saved_tokens', 0):,} tokens ({result.get('compression_ratio', 0.0) * 100:.1f}%)"
        )
        print()
        print("Retained blocks:")
        for block in result.get("retained_blocks", []):
            print(f"  - {block['type']}: {block['tokens']} tokens")
        print()
        print("Removed blocks:")
        for block in result.get("removed_blocks", []):
            print(f"  - {block['type']}: {block['tokens']} tokens")
    else:
        # Pretty format (default)
        fmt = OutputFormatter("Preview", mode=OutputMode.NORMAL)
        print(fmt.header())
        print()

        inp = result.get("input_tokens", 0)
        out = result.get("output_tokens", 0)
        saved = result.get("saved_tokens", 0)
        ratio = result.get("compression_ratio", 0.0)

        print(f"  Input:          {inp:,} tokens")
        print(f"  → Compressed:   {out:,} tokens")
        print(f"  Savings:        {saved:,} tokens ({ratio * 100:.1f}% reduction)")
        print()

        retained = result.get("retained_blocks", [])
        removed = result.get("removed_blocks", [])

        if retained:
            print(f"  Retained blocks ({len(retained)}):")
            for block in retained:
                print(f"    • {block['type']:<15} {block['tokens']:>6,} tokens")
            print()

        if removed:
            print(f"  Removed blocks ({len(removed)}):")
            for block in removed:
                print(f"    • {block['type']:<15} {block['tokens']:>6,} tokens")
            print()

        flags = result.get("flags", [])
        if flags:
            print(f"  Flags: {', '.join(flags)}")

        if args.verbose:
            print()
            print("  Detailed info:")
            print(f"    Mode: {result.get('mode', 'unknown')}")
            print(f"    Duration: {result.get('duration_ms', 0):.1f}ms")
