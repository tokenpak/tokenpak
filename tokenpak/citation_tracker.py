# SPDX-License-Identifier: Apache-2.0
"""Citation-Mapped Utility Scoring for TokenPak.

Tracks which context blocks an LLM actually cites in its response.
Cited blocks gain score; ignored blocks decay. Feeds the budget allocator
so future queries prioritize high-value blocks.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

# Default utility store location (relative to project root)
DEFAULT_UTILITY_PATH = ".tokenpak/utility.json"

# Minimum content length for substring match detection
MIN_MATCH_LEN = 20

# Score bounds
SCORE_MIN = 0.0
SCORE_MAX = 10.0

# Score deltas
CITE_DELTA = 1.0
DECAY_DELTA = 0.1


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _extract_identifiers(block_content: str) -> List[str]:
    """Extract function/class names and file paths from a block."""
    identifiers = []

    # Python/JS/TS: def foo, class Foo, function foo, const foo =
    for pat in [
        r"\bdef\s+(\w+)\s*\(",
        r"\bclass\s+(\w+)\b",
        r"\bfunction\s+(\w+)\s*\(",
        r"\bconst\s+(\w+)\s*=",
        r"\blet\s+(\w+)\s*=",
        r"\bfunc\s+(\w+)\s*\(",  # Go
        r"\bfn\s+(\w+)\s*\(",  # Rust
    ]:
        identifiers.extend(re.findall(pat, block_content))

    # File path patterns  (e.g. src/auth.py, ./utils/helper.js)
    path_pats = re.findall(r"[\w./\-]+\.\w{1,6}", block_content)
    identifiers.extend(p for p in path_pats if "/" in p or p.startswith("."))

    return [i for i in identifiers if len(i) >= 3]


def track_citations(
    response_text: str,
    context_slices: List[Dict],
) -> List[str]:
    """
    Determine which context slices the LLM appears to have cited.

    Detection methods (in order, any match = cited):
    1. Exact substring match of block content (≥ MIN_MATCH_LEN chars).
    2. File path mention in the response.
    3. Function/class name mention from the block.

    Args:
        response_text: The LLM's full response string.
        context_slices: List of dicts with keys: slice_id, content, ref (path).

    Returns:
        List of slice_ids that appear to be referenced.
    """
    cited = []

    for sl in context_slices:
        sid = sl.get("slice_id", "")
        content = sl.get("content", "")
        ref = sl.get("ref", "")

        if not sid:
            continue

        # Method 1: exact substring match of meaningful content chunk
        if len(content) >= MIN_MATCH_LEN:
            # Check the first 200 chars of content to avoid huge strings
            probe = content[:200].strip()
            if len(probe) >= MIN_MATCH_LEN and probe in response_text:
                cited.append(sid)
                continue

        # Method 2: file path mention
        if ref and len(ref) >= 3 and ref in response_text:
            cited.append(sid)
            continue

        # Method 3: function/class name mentions
        identifiers = _extract_identifiers(content)
        for ident in identifiers:
            # Use word-boundary match to avoid false positives
            pattern = r"\b" + re.escape(ident) + r"\b"
            if re.search(pattern, response_text):
                cited.append(sid)
                break

    return cited


# ---------------------------------------------------------------------------
# Utility store
# ---------------------------------------------------------------------------


def _load_utility(utility_path: str) -> Dict:
    """Load utility.json; return empty dict if missing."""
    p = Path(utility_path)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_utility(data: Dict, utility_path: str) -> None:
    """Persist utility.json."""
    p = Path(utility_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def update_utility(
    cited_ids: List[str],
    all_ids: List[str],
    utility_path: str = DEFAULT_UTILITY_PATH,
) -> Dict:
    """
    Update utility scores after an LLM response.

    - Cited blocks: score += CITE_DELTA, hits += 1
    - Uncited blocks: score -= DECAY_DELTA, misses += 1
    - Scores clamped to [SCORE_MIN, SCORE_MAX]

    Args:
        cited_ids:    Slice IDs that were cited (from track_citations).
        all_ids:      All slice IDs that were in context for this response.
        utility_path: Path to utility.json store.

    Returns:
        Updated utility dict.
    """
    data = _load_utility(utility_path)
    now = datetime.now(timezone.utc).isoformat()
    cited_set = set(cited_ids)

    for sid in all_ids:
        entry = data.get(sid, {"score": 5.0, "hits": 0, "misses": 0, "last_cited": None})

        if sid in cited_set:
            entry["score"] = min(SCORE_MAX, entry["score"] + CITE_DELTA)
            entry["hits"] = entry.get("hits", 0) + 1
            entry["last_cited"] = now
        else:
            entry["score"] = max(SCORE_MIN, entry["score"] - DECAY_DELTA)
            entry["misses"] = entry.get("misses", 0) + 1

        data[sid] = entry

    _save_utility(data, utility_path)
    return data


def get_utility_score(
    slice_id: str,
    utility_path: str = DEFAULT_UTILITY_PATH,
) -> float:
    """
    Return the current utility score for a slice_id.
    Returns 5.0 (neutral) if no data exists.
    """
    data = _load_utility(utility_path)
    return data.get(slice_id, {}).get("score", 5.0)


def get_utility_weight(
    slice_id: str,
    utility_path: str = DEFAULT_UTILITY_PATH,
) -> float:
    """
    Return the budget multiplier for a slice_id.

    score=5.0 → weight=1.0 (neutral)
    score=10.0 → weight=2.0 (double priority)
    score=0.0 → weight=0.0 (penalized)
    """
    score = get_utility_score(slice_id, utility_path)
    return score / 5.0
