"""prune command — /tokenpak prune.

Remove low-priority memory/blocks from compression store.

Usage:
    /tokenpak prune                    # Interactive: show candidates, confirm
    /tokenpak prune --auto             # Auto-prune low-priority blocks
    /tokenpak prune --dry-run          # Show what would be pruned
    /tokenpak prune --threshold 0.3    # Prune blocks below relevance score
"""

from __future__ import annotations

__all__ = (
    "DEFAULT_THRESHOLD",
    "SEP",
    "run_prune",
)


import json
import os
import sys
from pathlib import Path
from typing import TypedDict, cast

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEP = "────────────────────────────────────────"
DEFAULT_THRESHOLD = 0.4  # Quality score below which blocks are prunable
_PINS_PATH = os.path.expanduser("~/.tokenpak/pinned_blocks.json")
_BLOCK_STORE_PATH = os.environ.get(
    "TOKENPAK_VAULT_INDEX",
    os.path.expanduser("~/.tokenpak/vault_index.json"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class Block(TypedDict, total=False):
    """Fields consumed from a persisted vault block."""

    block_id: str
    quality_score: float
    raw_tokens: int
    tokens_saved: int
    path: str


def _load_pins() -> set[str]:
    """Load set of pinned block IDs from disk."""
    path = Path(_PINS_PATH)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return set()
        pinned = data.get("pinned", [])
        if not isinstance(pinned, list):
            return set()
        return {item for item in pinned if isinstance(item, str)}
    except Exception:
        return set()


def _load_blocks() -> list[Block]:
    """Load all blocks from the block store JSON."""
    path = Path(_BLOCK_STORE_PATH)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        # BlockStore stores blocks under the key "blocks"
        blocks = data.get("blocks", {})
        if not isinstance(blocks, dict):
            return []
        return [cast(Block, block) for block in blocks.values() if isinstance(block, dict)]
    except Exception:
        return []


def _save_blocks(blocks_list: list[Block]) -> None:
    """Save updated block list back to store."""
    path = Path(_BLOCK_STORE_PATH)
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        # Rebuild blocks dict keyed by block_id
        data["blocks"] = {b["block_id"]: b for b in blocks_list}
        path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"⚠  Could not save block store: {e}", file=sys.stderr)


def _prune_candidates(
    blocks: list[Block], pins: set[str], threshold: float
) -> tuple[list[Block], list[Block]]:
    """Split blocks into (candidates_to_prune, blocks_to_keep)."""
    candidates = []
    keep = []
    for b in blocks:
        bid = b.get("block_id", "")
        score = b.get("quality_score", 1.0)
        if bid in pins:
            keep.append(b)
        elif score < threshold:
            candidates.append(b)
        else:
            keep.append(b)
    return candidates, keep


def _fmt_block(b: Block) -> str:
    bid = b.get("block_id", "?")
    score = b.get("quality_score", 0.0)
    raw = b.get("raw_tokens", 0)
    saved = b.get("tokens_saved", 0)
    path = b.get("path", "")
    return f"  {bid:<40}  score={score:.2f}  raw={raw:,}  saved={saved:,}\n    path: {path}"


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_prune(
    auto: bool = False,
    dry_run: bool = False,
    threshold: float = DEFAULT_THRESHOLD,
    as_json: bool = False,
) -> None:
    """Core prune logic — callable from tests or CLI."""
    pins = _load_pins()
    all_blocks = _load_blocks()

    if not all_blocks:
        if as_json:
            print(json.dumps({"pruned": 0, "freed_tokens": 0, "dry_run": dry_run}))
        else:
            print("ℹ  No blocks in store — nothing to prune.")
        return

    candidates, keep = _prune_candidates(all_blocks, pins, threshold)

    if not candidates:
        if as_json:
            print(json.dumps({"pruned": 0, "freed_tokens": 0, "dry_run": dry_run, "candidates": 0}))
        else:
            print(f"✓  No blocks below threshold {threshold:.2f} — nothing to prune.")
        return

    total_freed = sum(b.get("raw_tokens", 0) for b in candidates)

    if as_json:
        result: dict[str, object] = {
            "candidates": len(candidates),
            "freed_tokens": total_freed,
            "dry_run": dry_run,
            "pruned": 0 if dry_run else len(candidates),
        }
        if not (auto or dry_run):
            result["blocks"] = [b.get("block_id") for b in candidates]
        print(json.dumps(result, indent=2))
        return

    print(f"TOKENPAK  |  Prune (threshold={threshold:.2f})\n{SEP}")
    print(f"  Pinned blocks (protected):  {len(pins)}")
    print(f"  Total blocks:               {len(all_blocks)}")
    print(f"  Prune candidates:           {len(candidates)}")
    print(f"  Tokens to free:             {total_freed:,}")
    print()

    if dry_run:
        print("── Dry Run — no changes will be made ──\n")
        for b in candidates:
            print(_fmt_block(b))
        print(f"\n  Would remove {len(candidates)} block(s) and free {total_freed:,} tokens.")
        return

    # Show candidates
    print("── Prune Candidates ──\n")
    for b in candidates:
        print(_fmt_block(b))
    print()

    # Confirm (unless --auto)
    if not auto:
        try:
            answer = input(f"Remove {len(candidates)} block(s)? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("y", "yes"):
            print("Aborted — no changes made.")
            return

    # Apply
    _save_blocks(keep)
    print(f"\n✓  Pruned {len(candidates)} block(s) — freed {total_freed:,} tokens.")


# ---------------------------------------------------------------------------
# Click command (if available)
# ---------------------------------------------------------------------------


try:
    import click

    @click.command("prune")
    @click.option("--auto", is_flag=True, help="Auto-prune without confirmation")
    @click.option(
        "--dry-run", "dry_run", is_flag=True, help="Show what would be pruned (no changes)"
    )
    @click.option(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        show_default=True,
        help="Quality score below which blocks are pruned",
    )
    @click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
    def prune_cmd(auto: bool, dry_run: bool, threshold: float, as_json: bool) -> None:
        """Remove low-priority blocks from compression store.

        \b
        Examples:
          tokenpak prune                     # interactive review
          tokenpak prune --dry-run           # preview without changes
          tokenpak prune --auto              # prune without confirmation
          tokenpak prune --threshold 0.3     # custom quality threshold
        """
        run_prune(auto=auto, dry_run=dry_run, threshold=threshold, as_json=as_json)

except ImportError:

    def prune_cmd_fallback(*args: object, **kwargs: object) -> None:
        run_prune()

    globals()["prune_cmd"] = prune_cmd_fallback
