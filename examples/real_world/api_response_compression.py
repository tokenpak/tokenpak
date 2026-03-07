"""
API Response Compression
==========================
Compress third-party API responses before injecting them into LLM context.

Problem: API responses (REST, GraphQL) often include verbose metadata, nested objects,
         and human-readable descriptions that inflate token usage.
Solution: Extract relevant fields + compress prose fields before LLM injection.

Expected savings: 50-75% on typical API responses.
Setup: pip install tokenpak
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine


engine = HeuristicEngine()


# Simulated GitHub API response (issues endpoint)
GITHUB_ISSUE = {
    "id": 1234567,
    "node_id": "I_kwDOABCDEF5678",
    "number": 42,
    "title": "Add support for streaming compression in the API",
    "state": "open",
    "locked": False,
    "assignee": {"login": "jsmith", "id": 9876, "type": "User"},
    "assignees": [{"login": "jsmith", "id": 9876}],
    "milestone": None,
    "comments": 7,
    "created_at": "2024-11-15T10:23:45Z",
    "updated_at": "2024-12-01T14:55:22Z",
    "closed_at": None,
    "author_association": "CONTRIBUTOR",
    "body": """
    ## Problem

    The current TokenPak API does not support streaming compression. This is a significant
    limitation for use cases where data arrives incrementally, such as log processing,
    real-time monitoring, or streaming LLM outputs. Users who are processing large volumes
    of data currently must buffer the entire input before compression can begin, which
    creates high memory pressure and increases latency significantly.

    ## Proposed Solution

    Implement a streaming compression endpoint that accepts chunked transfer encoding
    and processes data as it arrives. The compression algorithm would need to be adapted
    to handle partial inputs gracefully, potentially using a sliding window approach or
    sentence-boundary detection to determine safe flush points.

    ## Expected Impact

    - Memory usage reduced by 60-80% for large documents
    - Latency for first compressed output reduced from O(n) to near-constant
    - Enables real-time use cases currently impossible with batch API

    ## Acceptance Criteria

    - [ ] Streaming endpoint at `/v1/compress/stream`
    - [ ] Compatible with HTTP chunked transfer encoding
    - [ ] Minimum 90% compression quality vs batch mode
    - [ ] Documentation and examples
    """,
    "labels": [
        {"id": 111, "name": "enhancement", "color": "a2eeef", "description": "New feature or request"},
        {"id": 222, "name": "api", "color": "0075ca", "description": "API-related changes"},
    ],
    "reactions": {"total_count": 12, "+1": 10, "-1": 0, "laugh": 0, "hooray": 2, "confused": 0, "heart": 0, "rocket": 0, "eyes": 0},
    "url": "https://api.github.com/repos/example/tokenpak/issues/42",
    "html_url": "https://github.com/example/tokenpak/issues/42",
    "repository_url": "https://api.github.com/repos/example/tokenpak",
    "labels_url": "https://api.github.com/repos/example/tokenpak/issues/42/labels{/name}",
    "comments_url": "https://api.github.com/repos/example/tokenpak/issues/42/comments",
    "events_url": "https://api.github.com/repos/example/tokenpak/issues/42/events",
    "timeline_url": "https://api.github.com/repos/example/tokenpak/issues/42/timeline",
}


def extract_and_compress_issue(issue: dict) -> dict:
    """
    Extract relevant fields and compress prose from a GitHub issue.
    Strips metadata clutter; compresses body text.
    """
    # Extract only what's useful for LLM analysis
    extracted = {
        "number": issue["number"],
        "title": issue["title"],
        "state": issue["state"],
        "author": issue.get("assignee", {}).get("login", "unknown"),
        "labels": [l["name"] for l in issue.get("labels", [])],
        "comments": issue["comments"],
        "reactions": issue["reactions"]["total_count"],
        "created": issue["created_at"][:10],
        "body": issue.get("body", "").strip(),
    }

    # Compress the prose body
    if extracted["body"]:
        original_body = extracted["body"]
        extracted["body"] = engine.compact(original_body)
        return extracted, len(original_body), len(extracted["body"])

    return extracted, 0, 0


def compress_api_payload(payload: dict, prose_fields: list[str] = None) -> dict:
    """
    Generic API response compressor. Compresses string fields recursively.
    """
    prose_fields = prose_fields or ["body", "description", "content", "summary", "notes"]

    def compress_recursive(obj, depth=0):
        if depth > 5:
            return obj, 0, 0

        if isinstance(obj, dict):
            total_orig = 0
            total_comp = 0
            result = {}
            for k, v in obj.items():
                if k in prose_fields and isinstance(v, str) and len(v) > 100:
                    compressed = engine.compact(v.strip())
                    result[k] = compressed
                    total_orig += len(v)
                    total_comp += len(compressed)
                else:
                    new_v, o, c = compress_recursive(v, depth + 1)
                    result[k] = new_v
                    total_orig += o
                    total_comp += c
            return result, total_orig, total_comp
        elif isinstance(obj, list):
            total_orig = 0
            total_comp = 0
            result = []
            for item in obj:
                new_item, o, c = compress_recursive(item, depth + 1)
                result.append(new_item)
                total_orig += o
                total_comp += c
            return result, total_orig, total_comp
        else:
            return obj, 0, 0

    compressed, orig_chars, comp_chars = compress_recursive(payload)
    savings_pct = (1 - comp_chars / orig_chars) * 100 if orig_chars > 0 else 0
    return compressed, {"original_chars": orig_chars, "compressed_chars": comp_chars, "savings_pct": savings_pct}


def main():
    print("=== API Response Compression ===\n")

    # --- Approach 1: Smart extraction + compression ---
    print("📥 Approach 1: Extract + Compress (targeted)\n")
    compressed_issue, orig_len, comp_len = extract_and_compress_issue(GITHUB_ISSUE)

    raw_json_size = len(json.dumps(GITHUB_ISSUE))
    compressed_json_size = len(json.dumps(compressed_issue))

    print(f"  Raw API response:    {raw_json_size:,} chars (~{raw_json_size // 4} tokens)")
    print(f"  After extract+compress: {compressed_json_size:,} chars (~{compressed_json_size // 4} tokens)")
    print(f"  Total reduction:     {(1 - compressed_json_size/raw_json_size)*100:.0f}%")
    print(f"  Body compression:    {orig_len}→{comp_len} chars ({(1 - comp_len/orig_len)*100:.0f}% saved)\n")
    print(f"  LLM-ready payload:")
    print(json.dumps(compressed_issue, indent=2)[:600] + "...")

    # --- Approach 2: Generic recursive compression ---
    print(f"\n\n📥 Approach 2: Generic Recursive Compression\n")
    compressed_payload, stats = compress_api_payload(GITHUB_ISSUE)
    print(f"  Compressed {stats['original_chars']:,}→{stats['compressed_chars']:,} prose chars ({stats['savings_pct']:.0f}% saved)")
    print(f"  All other fields (IDs, dates, counts) preserved exactly")


if __name__ == "__main__":
    main()
