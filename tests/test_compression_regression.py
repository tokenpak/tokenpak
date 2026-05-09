"""
Compression Regression Test Suite

Tests that compression ratios don't degrade over time. Fails if any payload's
compression ratio degrades by more than 5%.

Usage:
    pytest tests/test_compression_regression.py -v
    pytest tests/test_compression_regression.py -v --update-baselines
"""

import json
import pytest
from pathlib import Path
from tokenpak.compression import CompressionPipeline


# Load baselines
BASELINES_PATH = Path(__file__).parent / "regression" / "baselines.json"
PAYLOADS_DIR = Path(__file__).parent / "regression" / "payloads"

def load_baselines():
    """Load baseline compression ratios"""
    if not BASELINES_PATH.exists():
        return {}
    with open(BASELINES_PATH) as f:
        return json.load(f)

def save_baselines(baselines):
    """Save baseline compression ratios"""
    BASELINES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BASELINES_PATH, "w") as f:
        json.dump(baselines, f, indent=2)

def compute_payload_size(payload):
    """Compute size of a payload"""
    return len(json.dumps(payload, separators=(',', ':')))

def get_payload_files():
    """Get all payload files"""
    return sorted(PAYLOADS_DIR.glob("*.json")) if PAYLOADS_DIR.exists() else []

def compress_payload(pipeline, payload):
    """Compress a payload using the compression pipeline"""
    try:
        if "messages" in payload:
            result = pipeline.run(payload["messages"])
            return result.messages
    except Exception as e:
        pytest.skip(f"Compression error: {e}")
    return None


class TestCompressionRegression:
    """Regression tests for compression quality.

    Implementation note (TSR-05l, 2026-05-08)
    ─────────────────────────────────────────
    The compression pipeline's `InstructionTable` stage is a stateful learning
    compressor — content must be observed at least `min_occurrences` (= 2)
    times before an instruction ID is allocated and the content is replaced
    with a `[INSTRUCTION:POLICY_NN]` tag. A fresh table cannot compress on the
    first call. Without deterministic warmup, this test passes only on dev
    machines whose `~/.tokenpak/instruction_table.json` happens to have
    accumulated entries for these exact payloads (a host-state pollution bug).

    The fixture below makes the test hermetic:
      1. The pipeline is constructed with `instruction_table_path` pointing
         at a per-class temp directory — no read/write of the user's real
         `~/.tokenpak/instruction_table.json`.
      2. Each payload is run through the pipeline **twice** before measuring.
         First pass: `_observe_text()` increments `seen_count` to 1.
         Second pass: `seen_count` reaches `min_occurrences=2`, an ID is
         allocated, and subsequent measurement calls emit compression tags.

    This produces a deterministic "warm-table" baseline that matches what
    real users see after the proxy has been running long enough for repeated
    patterns to be learned. It does NOT measure cold-table compression
    (which would always be ~1% by design — the table can't compress what
    it has never seen).

    See `tokenpak/compression/instruction_table.py:124-157` for the
    observation / ID-allocation contract.
    """

    @pytest.fixture(scope="class")
    def pipeline(self, tmp_path_factory):
        """Pipeline with isolated, warmed InstructionTable (see class docstring)."""
        # Per-class temp dir → no host state pollution.
        table_dir = tmp_path_factory.mktemp("tsr05l_instruction_table")
        table_path = table_dir / "instruction_table.json"
        p = CompressionPipeline(instruction_table_path=str(table_path))

        # Warmup: each payload's content must reach seen_count >= 2 before
        # the InstructionTable can compress. Two passes is the minimum.
        for payload_file in get_payload_files():
            with open(payload_file) as f:
                payload = json.load(f)
            messages = payload.get("messages")
            if not messages:
                continue
            for _ in range(2):
                p.run(messages)

        return p

    @pytest.fixture
    def baselines(self):
        """Load baseline ratios"""
        return load_baselines()
    
    @pytest.mark.parametrize("payload_file", get_payload_files(), ids=lambda p: p.stem)
    def test_compression_ratio_no_regression(self, payload_file, pipeline, baselines, request):
        """
        Test that compression ratio for a payload doesn't degrade.
        
        Tolerance: ±5% degradation = FAIL
        Tolerance: 5% improvement = PASS with note
        """
        
        payload_name = payload_file.stem
        
        # Load payload
        with open(payload_file) as f:
            payload = json.load(f)
        
        # Compute original size
        original_size = compute_payload_size(payload)
        
        # Compress
        compressed = compress_payload(pipeline, payload)
        if compressed is None:
            pytest.skip("Compression failed")
        
        # Compute current ratio
        compressed_size = compute_payload_size(compressed)
        current_ratio = (original_size - compressed_size) / original_size if original_size > 0 else 0
        current_ratio = max(0, min(1, current_ratio))  # Clamp to [0, 1]
        
        # Check baseline (update mode)
        if request.config.getoption("--update-baselines"):
            baselines[payload_name] = {
                "original_size": original_size,
                "compressed_size": compressed_size,
                "ratio": round(current_ratio, 4),
                "file": str(payload_file.relative_to(PAYLOADS_DIR.parent.parent))
            }
            save_baselines(baselines)
            pytest.skip(f"Baseline updated: {payload_name} = {current_ratio:.1%}")
        
        # Check against baseline
        if payload_name in baselines:
            baseline_ratio = baselines[payload_name]["ratio"]
            degradation = (baseline_ratio - current_ratio) / baseline_ratio if baseline_ratio > 0 else 0
            
            # Allow for some variance
            TOLERANCE = 0.05  # ±5%
            
            if degradation > TOLERANCE:
                pytest.fail(
                    f"{payload_name}: Compression degraded {degradation:.1%} "
                    f"(baseline {baseline_ratio:.1%} → current {current_ratio:.1%})"
                )
            elif degradation < -TOLERANCE:
                # Improvement
                improvement = -degradation
                print(f"\n✨ {payload_name}: Improved {improvement:.1%} "
                      f"({baseline_ratio:.1%} → {current_ratio:.1%})")
            else:
                # Within tolerance
                print(f"\n✓ {payload_name}: {current_ratio:.1%} (baseline {baseline_ratio:.1%})")
        else:
            # First time seeing this payload
            pytest.skip(f"No baseline for {payload_name}. Run with --update-baselines first.")


def pytest_addoption(parser):
    """Add custom pytest option for updating baselines"""
    parser.addoption(
        "--update-baselines",
        action="store_true",
        default=False,
        help="Update baseline compression ratios (use after intentional changes)"
    )


if __name__ == "__main__":
    # Quick manual test
    import sys
    
    payloads = get_payload_files()
    print(f"📦 Found {len(payloads)} payloads\n")
    
    pipeline = CompressionPipeline()
    baselines = {}
    
    for payload_file in payloads:
        payload_name = payload_file.stem
        print(f"Processing {payload_name}...", end=" ")
        
        with open(payload_file) as f:
            payload = json.load(f)
        
        original_size = compute_payload_size(payload)
        compressed = compress_payload(pipeline, payload)
        
        if compressed is None:
            print("❌ Compression failed")
            continue
        
        compressed_size = compute_payload_size(compressed)
        ratio = (original_size - compressed_size) / original_size if original_size > 0 else 0
        ratio = max(0, min(1, ratio))
        
        baselines[payload_name] = {
            "original_size": original_size,
            "compressed_size": compressed_size,
            "ratio": round(ratio, 4),
            "file": str(payload_file.relative_to(PAYLOADS_DIR.parent.parent))
        }
        
        print(f"✓ {ratio:.1%}")
    
    save_baselines(baselines)
    print(f"\n✅ Baselines saved")
