"""
Dedup — remove duplicate / near-duplicate message turns.

Provides basic deduplication of a messages list:
- Exact-duplicate removal (same role + same content hash)
- Near-duplicate removal via 4-gram Jaccard similarity
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

# Similarity threshold above which two messages are considered near-duplicates
DEDUP_JACCARD_THRESHOLD = 0.90


def _content_to_str(content: Any) -> str:
    """Flatten content (str or list of blocks) to a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _ngrams(text: str, n: int = 4) -> set[str]:
    if len(text) < n:
        return set()
    return set(text[i : i + n] for i in range(len(text) - n + 1))


def _jaccard(a: str, b: str, n: int = 4) -> float:
    sa, sb = _ngrams(a, n), _ngrams(b, n)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def dedup_messages(
    messages: List[Dict[str, Any]],
    *,
    threshold: float = DEDUP_JACCARD_THRESHOLD,
    keep: str = "last",
) -> List[Dict[str, Any]]:
    """
    Remove duplicate and near-duplicate messages.

    Parameters
    ----------
    messages:
        List of message dicts.
    threshold:
        Jaccard similarity above which messages are considered duplicates.
        Default 0.90 (90% character 4-gram overlap).
    keep:
        Which copy to keep when duplicates are found.
        "last" (default) — keep the later occurrence.
        "first" — keep the earlier occurrence.

    Returns
    -------
    List[Dict[str, Any]]
        Deduplicated messages list (order preserved).
    """
    if not messages:
        return messages

    # Step 1: exact-hash dedup
    seen_hashes: dict[str, int] = {}  # hash -> index of last seen
    for i, msg in enumerate(messages):
        content_str = _content_to_str(msg.get("content", ""))
        role = msg.get("role", "")
        key = _sha256(f"{role}:{content_str}")
        seen_hashes[key] = i

    if keep == "last":
        exact_keep: set[int] = set(seen_hashes.values())
    else:
        first_seen: dict[str, int] = {}
        for i, msg in enumerate(messages):
            content_str = _content_to_str(msg.get("content", ""))
            role = msg.get("role", "")
            key = _sha256(f"{role}:{content_str}")
            if key not in first_seen:
                first_seen[key] = i
        exact_keep = set(first_seen.values())

    after_exact = [msg for i, msg in enumerate(messages) if i in exact_keep]

    # Step 2: near-duplicate dedup (Jaccard)
    if threshold >= 1.0 or len(after_exact) < 2:
        return after_exact

    content_strs = [_content_to_str(m.get("content", "")) for m in after_exact]
    roles = [m.get("role", "") for m in after_exact]

    # Mark indices to drop
    drop: set[int] = set()
    for i in range(len(after_exact)):
        if i in drop:
            continue
        for j in range(i + 1, len(after_exact)):
            if j in drop:
                continue
            # Only dedup same-role messages
            if roles[i] != roles[j]:
                continue
            sim = _jaccard(content_strs[i], content_strs[j])
            if sim >= threshold:
                # Drop the earlier one when keep="last", later when keep="first"
                drop.add(i if keep == "last" else j)
                break  # move to next i

    return [msg for i, msg in enumerate(after_exact) if i not in drop]


def count_duplicates(
    messages: List[Dict[str, Any]],
    threshold: float = DEDUP_JACCARD_THRESHOLD,
) -> dict:
    """
    Count exact and near-duplicate messages without modifying the list.

    Returns
    -------
    dict with keys: exact_duplicates, near_duplicates, total_messages
    """
    if not messages:
        return {"exact_duplicates": 0, "near_duplicates": 0, "total_messages": 0}

    content_strs = [_content_to_str(m.get("content", "")) for m in messages]
    roles = [m.get("role", "") for m in messages]

    seen_hashes: dict[str, int] = {}
    exact_dupes = 0
    for i, (role, cs) in enumerate(zip(roles, content_strs)):
        key = _sha256(f"{role}:{cs}")
        if key in seen_hashes:
            exact_dupes += 1
        else:
            seen_hashes[key] = i

    near_dupes = 0
    checked: set[int] = set()
    for i in range(len(messages)):
        if i in checked:
            continue
        for j in range(i + 1, len(messages)):
            if j in checked:
                continue
            if roles[i] != roles[j]:
                continue
            if _jaccard(content_strs[i], content_strs[j]) >= threshold:
                near_dupes += 1
                checked.add(j)

    return {
        "exact_duplicates": exact_dupes,
        "near_duplicates": near_dupes,
        "total_messages": len(messages),
    }
