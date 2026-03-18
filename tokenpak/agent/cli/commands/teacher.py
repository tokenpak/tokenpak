"""Teacher pack builder CLI commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tokenpak.agent.teacher import build_teacher_pack


DEFAULT_SOURCE_ROOTS = ["~/vault", "~/vault/06_RUNTIME/SYSTEM", "~/vault/03_AGENT_PACKS"]
DEFAULT_COMMAND_ROOTS = ["~/Projects/tokenpak/tokenpak/agent/cli/commands"]
DEFAULT_OUTPUT_ROOT = "~/Projects/tokenpak/recipes/context"


def run_teacher_cmd(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="tokenpak teacher", add_help=True)
    sub = p.add_subparsers(dest="teacher_cmd")

    gen = sub.add_parser("generate", help="Generate deterministic context recipes")
    gen.add_argument("--source-root", action="append", default=None, help="Source root (repeatable)")
    gen.add_argument("--command-root", action="append", default=None, help="Command root (repeatable)")
    gen.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT, help="Output root directory")
    gen.add_argument("--version", default="v1", help="Version folder under output root")
    gen.add_argument("--token-budget", type=int, default=1600, help="Default token budget per intent")
    gen.add_argument("--json", action="store_true", help="Print JSON result")

    val = sub.add_parser("validate", help="Generate + print validation summary")
    val.add_argument("--source-root", action="append", default=None)
    val.add_argument("--command-root", action="append", default=None)
    val.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    val.add_argument("--version", default="v1")

    args = p.parse_args(argv)
    if args.teacher_cmd not in {"generate", "validate"}:
        p.print_help()
        return

    source_roots = args.source_root or DEFAULT_SOURCE_ROOTS
    command_roots = args.command_root or DEFAULT_COMMAND_ROOTS

    result = build_teacher_pack(
        source_roots=source_roots,
        command_roots=command_roots,
        output_root=args.output_root,
        version=args.version,
        default_budget=getattr(args, "token_budget", 1600),
    )

    validation = json.loads(Path(result.validation_path).read_text(encoding="utf-8"))
    payload = {
        "version": result.version,
        "source_fingerprint": result.source_fingerprint,
        "recipe_count": result.recipe_count,
        "recipes_path": str(result.recipes_path),
        "validation_path": str(result.validation_path),
        "validation_summary": validation.get("summary", {}),
    }

    if getattr(args, "json", False) or args.teacher_cmd == "validate":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"✓ Generated {result.recipe_count} recipes @ {result.recipes_path} "
            f"(fingerprint={result.source_fingerprint})"
        )
        print(f"✓ Validation report: {result.validation_path}")
