"""tokenpak diff command — Shows context changes (removed, compressed, retained blocks)."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Any


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


class DiffBlock:
    """A single block in the diff."""

    def __init__(
        self,
        block_id: str,
        name: str,
        status: str,  # "removed", "compressed", "retained"
        tokens_before: int = 0,
        tokens_after: int = 0,
        pinned: bool = False,
        compression_pct: Optional[float] = None,
    ):
        self.block_id = block_id
        self.name = name
        self.status = status
        self.tokens_before = tokens_before
        self.tokens_after = tokens_after
        self.pinned = pinned
        self.compression_pct = compression_pct

    @property
    def symbol(self) -> str:
        """Return the symbol for this block."""
        if self.status == "removed":
            return "+"
        elif self.status == "compressed":
            return "~"
        else:  # retained
            return "="

    def to_dict(self) -> dict:
        return {
            "id": self.block_id,
            "name": self.name,
            "symbol": self.symbol,
            "status": self.status,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "compression_pct": self.compression_pct,
            "pinned": self.pinned,
        }


class ContextDiff:
    """A complete context diff."""

    def __init__(
        self,
        trace_id: str,
        timestamp: Optional[str] = None,
        removed: Optional[list[DiffBlock]] = None,
        compressed: Optional[list[DiffBlock]] = None,
        retained: Optional[list[DiffBlock]] = None,
    ):
        self.trace_id = trace_id
        self.timestamp = timestamp or datetime.now().isoformat()
        self.removed = removed or []
        self.compressed = compressed or []
        self.retained = retained or []

    @property
    def total_blocks(self) -> int:
        return len(self.removed) + len(self.compressed) + len(self.retained)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
            "summary": {
                "removed": len(self.removed),
                "compressed": len(self.compressed),
                "retained": len(self.retained),
                "total": self.total_blocks,
            },
            "removed": [b.to_dict() for b in self.removed],
            "compressed": [b.to_dict() for b in self.compressed],
            "retained": [b.to_dict() for b in self.retained],
        }


# ---------------------------------------------------------------------------
# Segment Classification
# ---------------------------------------------------------------------------


def _classify_segment(segment: dict) -> str:
    """Classify a segment as removed, compressed, or retained."""
    try:
        actions = json.loads(segment.get("actions", "[]"))
    except (json.JSONDecodeError, TypeError):
        actions = []

    tokens_raw = segment.get("tokens_raw", 0) or 0
    tokens_after = segment.get("tokens_after_tp", 0) or 0

    # Check for explicit remove action
    if "remove" in actions:
        return "removed"

    # Check for explicit compress action
    if "compress" in actions:
        return "compressed"

    # Heuristic: zero tokens_after with non-zero raw → removed
    if tokens_raw > 0 and tokens_after == 0:
        return "removed"

    # Heuristic: significant token reduction → compressed
    if tokens_raw > 0 and tokens_after > 0:
        ratio = tokens_after / tokens_raw
        if ratio < 0.8:  # >20% reduction
            return "compressed"

    # Default: retained
    return "retained"


def _is_pinned(segment: dict) -> bool:
    """Check if a segment is pinned (instruction/system prompt)."""
    content_type = segment.get("content_type", "").lower()
    segment_type = segment.get("segment_type", "").lower()

    # Pinned if it's a pinned instruction or system-level content
    pinned_types = {"pinned_instruction", "pinned", "system", "instruction"}
    return content_type in pinned_types or (
        segment_type == "instruction" and content_type != "knowledge"
    )


# ---------------------------------------------------------------------------
# Diff Building
# ---------------------------------------------------------------------------


def _build_diff_from_segments(trace_id: str, segments: list[dict]) -> ContextDiff:
    """Build a ContextDiff from a list of segment records."""
    removed = []
    compressed = []
    retained = []

    for seg in segments:
        seg_id = seg.get("segment_id", "unknown")
        name = seg.get("debug_ref", seg.get("segment_source", "Unknown block"))
        tokens_before = seg.get("tokens_raw", 0) or 0
        tokens_after = seg.get("tokens_after_tp", 0) or 0
        pinned = _is_pinned(seg)

        status = _classify_segment(seg)

        # Calculate compression percentage
        compression_pct = None
        if status == "compressed" and tokens_before > 0:
            compression_pct = ((tokens_before - tokens_after) / tokens_before) * 100

        block = DiffBlock(
            block_id=seg_id,
            name=name,
            status=status,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            pinned=pinned,
            compression_pct=compression_pct,
        )

        if status == "removed":
            removed.append(block)
        elif status == "compressed":
            compressed.append(block)
        else:
            retained.append(block)

    return ContextDiff(trace_id=trace_id, removed=removed, compressed=compressed, retained=retained)


def _empty_diff(trace_id: str = "none") -> ContextDiff:
    """Create an empty diff with no changes."""
    return ContextDiff(trace_id=trace_id, removed=[], compressed=[], retained=[])


# ---------------------------------------------------------------------------
# Database/Trace Access (Stub for now)
# ---------------------------------------------------------------------------


def _get_recent_trace(since: Optional[str] = None) -> Optional[dict]:
    """Load recent trace from vault or storage. Returns None if not found."""
    # TODO: Implement actual trace retrieval from tp_events or vault
    # For now, this is a stub that returns None (simulating no trace data)
    return None


def _get_diff_since(since: Optional[str] = None) -> ContextDiff:
    """Get diff since a specific timestamp or most recent."""
    trace = _get_recent_trace(since=since)
    if trace is None:
        return _empty_diff()

    trace_id = trace.get("trace_id", "unknown")
    segments = trace.get("segments", [])
    return _build_diff_from_segments(trace_id, segments)


# ---------------------------------------------------------------------------
# Display Functions
# ---------------------------------------------------------------------------

SEP = "────────────────────────────────────────"


def print_diff(diff: ContextDiff, verbose: bool = False, raw: bool = False) -> None:
    """Print diff in human-readable format."""
    if raw:
        print(json.dumps(diff.to_dict(), indent=2))
        return

    # Empty check
    if diff.total_blocks == 0:
        print("TOKENPAK  |  Context Diff")
        print(SEP)
        print()
        print("  No context changes — all blocks retained.")
        print()
        return

    print("TOKENPAK  |  Context Diff")
    print(SEP)
    print()

    # Removed section
    if diff.removed:
        print(f"REMOVED ({len(diff.removed)} blocks)")
        for block in diff.removed:
            tokens_str = f" ({block.tokens_before} tokens)" if verbose else ""
            print(f"  {block.symbol} {block.name}{tokens_str}")
        print()

    # Compressed section
    if diff.compressed:
        print(f"COMPRESSED ({len(diff.compressed)} blocks)")
        for block in diff.compressed:
            if verbose:
                if block.compression_pct is not None:
                    tokens_str = (
                        f" ({block.tokens_before} → {block.tokens_after}, {block.compression_pct:.0f}%)"
                    )
                else:
                    tokens_str = f" ({block.tokens_before} → {block.tokens_after})"
                print(f"  {block.symbol} {block.name}{tokens_str}")
            else:
                print(f"  {block.symbol} {block.name}")
        print()

    # Retained section (Pinned)
    if diff.retained:
        print(f"RETAINED (Pinned)")
        for block in diff.retained:
            tokens_str = f" ({block.tokens_before} tokens)" if verbose else ""
            print(f"  {block.symbol} {block.name}{tokens_str}")
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
    raw = getattr(args, "json", False) or getattr(args, "raw", False)
    since = getattr(args, "since", None)

    diff = _get_diff_since(since=since)
    print_diff(diff, verbose=verbose, raw=raw)
