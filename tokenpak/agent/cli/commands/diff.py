"""tokenpak diff command — Shows context changes (removed, compressed, retained blocks)."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

SEP = "────────────────────────────────────────"


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


class BlockDiff:
    """A single block in the diff."""

    def __init__(
        self,
        name: str,
        kind: str,  # "removed", "compressed", "retained"
        tokens_before: int = 0,
        tokens_after: int = 0,
        pinned: bool = False,
    ):
        self.name = name
        self.kind = kind
        self.tokens_before = tokens_before
        self.tokens_after = tokens_after
        self.pinned = pinned

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "compression_ratio": (
                f"{(100 * self.tokens_after / self.tokens_before):.0f}%"
                if self.tokens_before > 0
                else "N/A"
            ),
            "pinned": self.pinned,
        }


class ContextDiff:
    """A complete context diff."""

    def __init__(self):
        self.removed: list[BlockDiff] = []
        self.compressed: list[BlockDiff] = []
        self.retained: list[BlockDiff] = []
        self.timestamp: str = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "summary": {
                "removed_count": len(self.removed),
                "compressed_count": len(self.compressed),
                "retained_count": len(self.retained),
            },
            "removed": [b.to_dict() for b in self.removed],
            "compressed": [b.to_dict() for b in self.compressed],
            "retained": [b.to_dict() for b in self.retained],
        }


# ---------------------------------------------------------------------------
# Diff Detection
# ---------------------------------------------------------------------------


def _load_diff_history() -> Optional[ContextDiff]:
    """Load diff history from vault or storage.
    
    For now, this is a stub that returns a mock diff.
    Real implementation would query actual compression history.
    """
    # Stub: return sample data
    diff = ContextDiff()

    # Mock removed blocks
    diff.removed.append(BlockDiff("Legacy telemetry cache", "removed", tokens_before=500))
    diff.removed.append(BlockDiff("Duplicate system prompt", "removed", tokens_before=300))

    # Mock compressed blocks
    diff.compressed.append(
        BlockDiff("MasterPlaybook.v3.9", "compressed", tokens_before=2500, tokens_after=800)
    )
    diff.compressed.append(
        BlockDiff("KPI-ContractorPercent sheet summary", "compressed", tokens_before=1200, tokens_after=400)
    )

    # Mock retained blocks (pinned)
    diff.retained.append(
        BlockDiff("Architecture Decision Log", "retained", tokens_before=1500, pinned=True)
    )
    diff.retained.append(
        BlockDiff("API Contract v2", "retained", tokens_before=2000, pinned=True)
    )

    return diff


def _get_diff_since(since: Optional[str] = None) -> ContextDiff:
    """Get diff since a specific timestamp or most recent."""
    # For now, always return full diff (stub implementation)
    # Real implementation would filter by since parameter
    return _load_diff_history() or ContextDiff()


# ---------------------------------------------------------------------------
# Display Functions
# ---------------------------------------------------------------------------


def print_diff(diff: ContextDiff, verbose: bool = False, raw: bool = False) -> None:
    """Print diff in human-readable format."""
    if raw:
        print(json.dumps(diff.to_dict(), indent=2))
        return

    print("TOKENPAK  |  Context Diff")
    print(SEP)
    print()

    # Removed section
    if diff.removed:
        print(f"REMOVED ({len(diff.removed)} blocks)")
        for block in diff.removed:
            symbol = "+"
            tokens_str = f" ({block.tokens_before} tokens)" if verbose else ""
            print(f"  {symbol} {block.name}{tokens_str}")
        print()

    # Compressed section
    if diff.compressed:
        print(f"COMPRESSED ({len(diff.compressed)} blocks)")
        for block in diff.compressed:
            symbol = "~"
            if verbose:
                ratio = f"{(100 * block.tokens_after / block.tokens_before):.0f}%" if block.tokens_before else "N/A"
                tokens_str = f" ({block.tokens_before} → {block.tokens_after}, {ratio})"
                print(f"  {symbol} {block.name}{tokens_str}")
            else:
                print(f"  {symbol} {block.name}")
        print()

    # Retained section (Pinned)
    if diff.retained:
        print(f"RETAINED (Pinned)")
        for block in diff.retained:
            symbol = "="
            tokens_str = f" ({block.tokens_before} tokens)" if verbose else ""
            print(f"  {symbol} {block.name}{tokens_str}")
        print()

    # Summary if all empty
    if not diff.removed and not diff.compressed and not diff.retained:
        print("  No changes — context is stable.")
        print()


def print_diff_json(diff: ContextDiff) -> None:
    """Print diff as JSON."""
    print(json.dumps(diff.to_dict(), indent=2))


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def run_diff_cmd(args) -> None:
    """Main dispatcher for 'tokenpak diff' subcommand."""
    from tokenpak.agent.license.activation import is_pro

    if not is_pro():
        print("⚠ Context Diff requires a Pro (or higher) license.")
        print("  Run: tokenpak license activate <key>")
        return

    verbose = getattr(args, "verbose", False)
    raw = getattr(args, "raw", False) or getattr(args, "json", False)
    since = getattr(args, "since", None)

    diff = _get_diff_since(since=since)
    print_diff(diff, verbose=verbose, raw=raw)
