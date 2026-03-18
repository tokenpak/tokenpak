"""TokenPak Agent Demo Command — visualize the compression pipeline."""

from __future__ import annotations

from typing import Optional

from .collector import TelemetryCollector


def run_demo(request_id: Optional[str] = None) -> str:
    """Render a demo pipeline breakdown using synthetic data.

    Returns a formatted string suitable for terminal output.
    Used by `tokenpak demo` CLI command.
    """
    req, sess = TelemetryCollector.create_demo_stats()

    lines = [
        "",
        "╔══════════════════════════════════════════════╗",
        "║          TokenPak Compression Pipeline        ║",
        "╠══════════════════════════════════════════════╣",
        f"║  Stage 1 — Ingest      {req.input_tokens_raw:>6,} tokens          ║",
        "║  Stage 2 — Segment     split into blocks      ║",
        "║  Stage 3 — Deduplicate remove repeating segs  ║",
        "║  Stage 4 — Compress    apply recipe rules     ║",
        f"║  Stage 5 — Emit        {req.input_tokens_sent:>6,} tokens          ║",
        "╠══════════════════════════════════════════════╣",
        f"║  Saved:  {req.tokens_saved:>4,} tokens  ({req.percent_saved:.1f}%)  ${req.cost_saved:.3f}   ║",
        "╠══════════════════════════════════════════════╣",
        f"║  Session: {sess.session_requests} reqs | -{sess.session_total_saved:,} tokens | ${sess.session_total_cost_saved:.2f} ║",
        "╚══════════════════════════════════════════════╝",
        "",
    ]
    return "\n".join(lines)


def print_demo(request_id: Optional[str] = None) -> None:
    """Print the demo pipeline to stdout."""
    print(run_demo(request_id=request_id))
