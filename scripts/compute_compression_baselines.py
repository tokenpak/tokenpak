#!/usr/bin/env python3
"""
Compute baseline compression ratios for all test payloads.
Usage:
    python3 scripts/compute_compression_baselines.py
"""

import json
import os
from pathlib import Path

# Add tokenpak to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from tokenpak.compression.pipeline import CompressionPipeline

def compute_payload_size(payload):
    """Compute size of a payload (in bytes when JSON-encoded)"""
    return len(json.dumps(payload, separators=(',', ':')))

def compress_payload(pipeline, payload):
    """Compress a payload using the pipeline"""
    try:
        # Pipeline expects messages format
        if "messages" in payload:
            result = pipeline.compress(payload["messages"])
            return result
    except Exception as e:
        print(f"  ⚠️  Compression error: {e}")
        return None
    return None

def main():
    payloads_dir = Path(__file__).parent.parent / "tests" / "regression" / "payloads"
    baselines_path = payloads_dir.parent / "baselines.json"
    
    if not payloads_dir.exists():
        print(f"❌ Payloads directory not found: {payloads_dir}")
        return
    
    # Load all payloads
    payload_files = sorted(payloads_dir.glob("*.json"))
    print(f"📦 Found {len(payload_files)} payloads")
    
    # Initialize compression pipeline
    pipeline = CompressionPipeline()
    print("✨ Pipeline ready\n")
    
    baselines = {}
    
    for payload_file in payload_files:
        payload_name = payload_file.stem
        print(f"Processing {payload_name}...")
        
        with open(payload_file) as f:
            payload = json.load(f)
        
        # Compute original size
        original_size = compute_payload_size(payload)
        print(f"  Original size: {original_size} bytes")
        
        # Compress
        compressed = compress_payload(pipeline, payload)
        
        if compressed is None:
            print(f"  ❌ Compression failed, skipping")
            continue
        
        # Compute compressed size
        compressed_size = compute_payload_size(compressed)
        print(f"  Compressed size: {compressed_size} bytes")
        
        # Compute ratio
        ratio = (original_size - compressed_size) / original_size if original_size > 0 else 0
        ratio = max(0, min(1, ratio))  # Clamp to [0, 1]
        
        print(f"  Ratio: {ratio:.1%}\n")
        
        baselines[payload_name] = {
            "original_size": original_size,
            "compressed_size": compressed_size,
            "ratio": round(ratio, 4),
            "file": str(payload_file.relative_to(payloads_dir.parent.parent))
        }
    
    # Save baselines
    with open(baselines_path, "w") as f:
        json.dump(baselines, f, indent=2)
    
    print(f"✅ Baselines saved to {baselines_path}")
    print(f"\nSummary:")
    for name, data in baselines.items():
        print(f"  {name}: {data['ratio']:.1%}")

if __name__ == "__main__":
    main()
