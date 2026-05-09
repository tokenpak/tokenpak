#!/usr/bin/env python3
"""
Compute baseline compression ratios for all test payloads.

Usage:
    python3 scripts/compute_compression_baselines.py [--out PATH]

State assumptions (TSR-05l, 2026-05-08):
    `CompressionPipeline.run()` is the canonical OSS API. Its strongest
    stage — `InstructionTable` — is a stateful learning compressor that
    only emits a `[INSTRUCTION:POLICY_NN]` tag once a content hash has
    been observed at least `min_occurrences` (= 2) times.

    A first call against a fresh table can never compress: it can only
    observe. To produce reproducible "warm-table" baselines, this
    script runs each payload through the pipeline twice before
    recording its ratio.

    To stay hermetic, the script uses a temp-dir-backed instruction
    table (does NOT touch the user's `~/.tokenpak/instruction_table.json`).

History note: prior versions of this script called `pipeline.compress()`
which never existed on OSS `CompressionPipeline` (only `.run()` ever has).
The bare-except swallowed the resulting `AttributeError` and the script
silently emitted no baselines. The committed `tests/regression/baselines.json`
therefore came from a different code path (likely a vault-internal
predecessor) and the values must be regenerated whenever the pipeline's
deterministic behavior actually changes.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# Add tokenpak to path so the script can run from a checkout without install.
sys.path.insert(0, str(Path(__file__).parent.parent))

from tokenpak.compression.pipeline import CompressionPipeline  # noqa: E402


def compute_payload_size(payload: Any) -> int:
    """Size (in bytes) of a payload encoded as compact JSON."""
    return len(json.dumps(payload, separators=(",", ":")))


def compress_payload(pipeline: CompressionPipeline, payload: dict) -> Any:
    """Run the pipeline against a single payload's messages.

    Errors are intentionally NOT swallowed — fail loudly so a refactored
    or renamed API doesn't silently produce zero-baseline output (TSR-05l
    history note in the module docstring).
    """
    if "messages" not in payload:
        raise ValueError("payload missing 'messages' key")
    result = pipeline.run(payload["messages"])
    return result.messages


def warm_pipeline(pipeline: CompressionPipeline, payload_files: list) -> None:
    """Warm the InstructionTable so each payload's content reaches
    seen_count >= 2 (the threshold for ID allocation)."""
    for payload_file in payload_files:
        with open(payload_file) as f:
            payload = json.load(f)
        if "messages" not in payload:
            continue
        # Two passes: first observes, second observes again → ID is
        # allocated mid-run, then compression fires.
        for _ in range(2):
            pipeline.run(payload["messages"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output baselines.json path (default: tests/regression/baselines.json)",
    )
    args = parser.parse_args()

    payloads_dir = Path(__file__).parent.parent / "tests" / "regression" / "payloads"
    baselines_path = args.out or (payloads_dir.parent / "baselines.json")

    if not payloads_dir.exists():
        print(f"❌ Payloads directory not found: {payloads_dir}", file=sys.stderr)
        return 1

    payload_files = sorted(payloads_dir.glob("*.json"))
    print(f"📦 Found {len(payload_files)} payloads")

    # Hermetic instruction table: temp dir, not ~/.tokenpak/.
    with tempfile.TemporaryDirectory(prefix="tsr05l_baseline_") as tmpdir:
        table_path = Path(tmpdir) / "instruction_table.json"
        pipeline = CompressionPipeline(instruction_table_path=str(table_path))
        print(f"✨ Pipeline ready (instruction_table → {table_path})")

        # Warmup so InstructionTable can compress on the measured run.
        print("🔥 Warming InstructionTable (2 passes per payload)...")
        warm_pipeline(pipeline, payload_files)

        baselines = {}
        for payload_file in payload_files:
            payload_name = payload_file.stem
            print(f"\nProcessing {payload_name}...")

            with open(payload_file) as f:
                payload = json.load(f)

            original_size = compute_payload_size(payload)
            print(f"  Original size: {original_size} bytes")

            compressed = compress_payload(pipeline, payload)

            compressed_size = compute_payload_size(compressed)
            print(f"  Compressed size: {compressed_size} bytes")

            ratio = (
                (original_size - compressed_size) / original_size
                if original_size > 0
                else 0
            )
            ratio = max(0.0, min(1.0, ratio))
            print(f"  Ratio: {ratio:.1%}")

            baselines[payload_name] = {
                "original_size": original_size,
                "compressed_size": compressed_size,
                "ratio": round(ratio, 4),
                "file": str(payload_file.relative_to(payloads_dir.parent.parent)),
            }

    baselines_path.parent.mkdir(parents=True, exist_ok=True)
    with open(baselines_path, "w") as f:
        json.dump(baselines, f, indent=2)
        f.write("\n")

    print(f"\n✅ Baselines saved to {baselines_path}")
    print("\nSummary:")
    for name, data in baselines.items():
        print(f"  {name}: {data['ratio']:.1%}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
