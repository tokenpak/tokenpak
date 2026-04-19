"""retain command — /tokenpak retain — Pro+ feature.

Pin blocks so they're never removed by compression or prune.

Usage:
    /tokenpak retain <block-id>        # Pin a specific block
    /tokenpak retain --list            # Show all pinned blocks
    /tokenpak retain --remove <id>     # Unpin a block
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEP = "────────────────────────────────────────"
_PINS_PATH = os.path.expanduser("~/.tokenpak/pinned_blocks.json")
_BLOCK_STORE_PATH = os.environ.get(
    "TOKENPAK_VAULT_INDEX",
    os.path.expanduser("~/.tokenpak/vault_index.json"),
)


# ---------------------------------------------------------------------------
# Pin persistence
# ---------------------------------------------------------------------------


def _load_pin_data() -> dict:
    """Load raw pin store (dict with 'pinned' list and optional metadata)."""
    path = Path(_PINS_PATH)
    if not path.exists():
        return {"pinned": [], "meta": {}}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"pinned": [], "meta": {}}


def _save_pin_data(data: dict) -> None:
    """Write pin store to disk atomically."""
    path = Path(_PINS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to temp then rename for atomicity
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def load_pins() -> set:
    """Return set of pinned block IDs (public — used by prune)."""
    return set(_load_pin_data().get("pinned", []))


def pin_block(block_id: str) -> bool:
    """Pin a block. Returns True if newly pinned, False if already pinned."""
    data = _load_pin_data()
    pins = set(data.get("pinned", []))
    if block_id in pins:
        return False
    pins.add(block_id)
    data["pinned"] = sorted(pins)
    _save_pin_data(data)
    return True


def unpin_block(block_id: str) -> bool:
    """Unpin a block. Returns True if removed, False if wasn't pinned."""
    data = _load_pin_data()
    pins = set(data.get("pinned", []))
    if block_id not in pins:
        return False
    pins.discard(block_id)
    data["pinned"] = sorted(pins)
    _save_pin_data(data)
    return True


def _block_exists(block_id: str) -> bool:
    """Check if block_id exists in the block store."""
    path = Path(_BLOCK_STORE_PATH)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
        blocks = data.get("blocks", {})
        return block_id in blocks
    except Exception:
        return False


def _get_block_info(block_id: str) -> Optional[dict]:
    """Return block metadata if it exists."""
    path = Path(_BLOCK_STORE_PATH)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data.get("blocks", {}).get(block_id)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main runners
# ---------------------------------------------------------------------------


def run_retain_pin(block_id: str) -> None:
    """Pin a specific block."""
    newly_pinned = pin_block(block_id)
    info = _get_block_info(block_id)
    if not newly_pinned:
        print(f"ℹ  Block already pinned: {block_id}")
        return
    print(f"📌 Pinned: {block_id}")
    if info:
        score = info.get("quality_score", "?")
        raw = info.get("raw_tokens", 0)
        print(f"   quality_score={score}  raw_tokens={raw:,}")
    print("   This block will be preserved by prune and compression.")


def run_retain_list() -> None:
    """List all pinned blocks."""
    data = _load_pin_data()
    pins = data.get("pinned", [])
    print(f"TOKENPAK  |  Pinned Blocks\n{SEP}")
    if not pins:
        print("  (no blocks pinned — use: tokenpak retain <block-id>)")
        return
    print(f"  {len(pins)} pinned block(s):\n")
    for bid in sorted(pins):
        info = _get_block_info(bid)
        if info:
            score = info.get("quality_score", "?")
            raw = info.get("raw_tokens", 0)
            path = info.get("path", "")
            print(f"  📌 {bid}")
            print(f"     score={score}  raw={raw:,}")
            print(f"     path: {path}")
        else:
            print(f"  📌 {bid}  (block not in current store)")
        print()


def run_retain_remove(block_id: str) -> None:
    """Unpin a block."""
    removed = unpin_block(block_id)
    if removed:
        print(f"🔓 Unpinned: {block_id}")
        print("   Block will now be eligible for pruning/compression.")
    else:
        print(f"ℹ  Block was not pinned: {block_id}")


def run_retain(
    block_id: Optional[str] = None,
    list_pins: bool = False,
    remove: Optional[str] = None,
) -> None:
    """Dispatch retain subcommand."""
    # Tier gate
    try:
        from tokenpak.agent.license.activation import is_pro
        if not is_pro():
            print("⛔ /tokenpak retain requires a Pro or higher license.")
            print("   Upgrade at: https://tokenpak.dev/pro")
            sys.exit(1)
    except ImportError:
        pass  # Allow in test/dev environments

    if list_pins:
        run_retain_list()
    elif remove:
        run_retain_remove(remove)
    elif block_id:
        run_retain_pin(block_id)
    else:
        print("Usage: tokenpak retain <block-id>")
        print("       tokenpak retain --list")
        print("       tokenpak retain --remove <block-id>")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Click command (if available)
# ---------------------------------------------------------------------------


try:
    import click

    @click.command("retain")
    @click.argument("block_id", required=False, default=None)
    @click.option("--list", "list_pins", is_flag=True, help="Show all pinned blocks")
    @click.option("--remove", metavar="BLOCK_ID", default=None, help="Unpin a block")
    def retain_cmd(block_id, list_pins, remove):
        """Pin blocks so they survive compression and prune (Pro+).

        \b
        Examples:
          tokenpak retain path/to/file.py#abc123   # pin a block
          tokenpak retain --list                   # show all pins
          tokenpak retain --remove path/to/file.py#abc123  # unpin
        """
        run_retain(block_id=block_id, list_pins=list_pins, remove=remove)

except ImportError:
    def retain_cmd(*args, **kwargs):  # type: ignore
        run_retain()
