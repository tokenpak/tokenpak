# SPDX-License-Identifier: Apache-2.0
"""Compile-Time Tool Orchestration for TokenPak Phase 3.4.

Scans context blocks + query for external references (GitHub issues, URLs,
tickets), pre-fetches them in parallel, and injects them as ephemeral blocks
before packing. Eliminates multi-turn tool calls at LLM time.

Ephemeral blocks:
- Tagged with [EPHEMERAL] in the wire format header
- Lowest budget priority (filled last, dropped if over budget)
- Not persisted to registry
- Cached for 1 hour in .tokenpak/ref_cache.json
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

from tokenpak.compression.reference_fetcher import fetch_reference
from tokenpak.compression.reference_scanner import Reference, scan_for_references
from tokenpak.compression.wire import make_slice_id, pack

DEFAULT_REF_CACHE_PATH = ".tokenpak/ref_cache.json"
_CACHE_TTL_SECONDS = 3600  # 1 hour
_MAX_PARALLEL = 5  # max concurrent fetches
_FETCH_TIMEOUT = 8  # seconds per fetch (includes network)
_EPHEMERAL_TOKENS_PER_CHAR = 0.25  # rough token estimate


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------


def _load_cache(cache_path: str) -> dict:
    p = Path(cache_path)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(data: dict, cache_path: str) -> None:
    p = Path(cache_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def _cache_key(ref: Reference) -> str:
    return f"{ref.ref_type}:{ref.resolved_url}"


def _cache_get(ref: Reference, cache: dict) -> Optional[str]:
    """Return cached content if not stale, else None."""
    entry = cache.get(_cache_key(ref))
    if not entry:
        return None
    age = time.time() - entry.get("fetched_at", 0)
    if age > _CACHE_TTL_SECONDS:
        return None
    return entry.get("content")


def _cache_put(ref: Reference, content: str, cache: dict) -> None:
    cache[_cache_key(ref)] = {
        "content": content,
        "fetched_at": time.time(),
    }


def _prune_stale(cache: dict) -> dict:
    """Remove entries older than TTL."""
    now = time.time()
    return {k: v for k, v in cache.items() if now - v.get("fetched_at", 0) <= _CACHE_TTL_SECONDS}


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    return max(1, int(len(text) * _EPHEMERAL_TOKENS_PER_CHAR))


# ---------------------------------------------------------------------------
# Ephemeral block builder
# ---------------------------------------------------------------------------


def _build_ephemeral_block(ref: Reference, content: str) -> dict:
    """Wrap fetched reference content as an ephemeral wire block dict."""
    tokens = _estimate_tokens(content)
    return {
        "ref": ref.raw_match,
        "type": "EPHEMERAL",
        "quality": 0.8,
        "tokens": tokens,
        "content": content,
        "slice_id": make_slice_id(content, ref.raw_match),
        "ephemeral": True,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compile_with_refs(
    blocks: List[dict],
    query: str,
    budget: int,
    cache_path: str = DEFAULT_REF_CACHE_PATH,
    _inject_refs: bool = True,  # Allows disabling in tests
) -> str:
    """
    Compile context blocks into TOKPAK wire format, injecting ephemeral
    reference blocks fetched at compile time.

    Steps:
      1. Scan query + all block content for external references.
      2. Fetch each reference (parallel, max _MAX_PARALLEL concurrent).
      3. Wrap fetched content as ephemeral blocks.
      4. Budget check: fit regular blocks first, then fill with ephemeral.
      5. Pack into wire format.

    Args:
        blocks:      List of block dicts (standard wire block format).
        query:       The user query (scanned for references).
        budget:      Total token budget.
        cache_path:  Path to ref_cache.json.

    Returns:
        TOKPAK wire format string.
    """
    if not _inject_refs:
        return pack(blocks, budget)

    # 1. Scan for references
    all_text = query + "\n" + "\n".join(b.get("content", "") for b in blocks)
    refs = scan_for_references(all_text)

    # 2. Load cache
    cache = _load_cache(cache_path)

    # 3. Fetch references (parallel, with cache)
    fetched_blocks: List[dict] = []
    if refs:
        to_fetch = []
        for ref in refs:
            cached = _cache_get(ref, cache)
            if cached:
                fetched_blocks.append(_build_ephemeral_block(ref, cached))
            else:
                to_fetch.append(ref)

        if to_fetch:
            results: dict = {}
            with ThreadPoolExecutor(max_workers=_MAX_PARALLEL) as executor:
                future_map = {executor.submit(fetch_reference, ref): ref for ref in to_fetch}
                for future in as_completed(future_map, timeout=_FETCH_TIMEOUT + 2):
                    ref = future_map[future]
                    try:
                        content = future.result(timeout=_FETCH_TIMEOUT)
                        if content:
                            results[_cache_key(ref)] = (ref, content)
                    except Exception:
                        pass  # Fail silently

            fetched = len(results)
            failed = len(to_fetch) - fetched
            cached_ct = len(fetched_blocks)

            print(
                f"[ref-inject] Found {len(refs)} references, "
                f"fetched {fetched}, {failed} failed, {cached_ct} cached",
                file=sys.stderr,
            )

            for key, (ref, content) in results.items():
                _cache_put(ref, content, cache)  # type: ignore[arg-type]
                fetched_blocks.append(_build_ephemeral_block(ref, content))  # type: ignore[arg-type]

        # Prune and save cache
        cache = _prune_stale(cache)
        _save_cache(cache, cache_path)

    # 4. Budget allocation: regular blocks first, then ephemeral
    regular_tokens = sum(b.get("tokens", 0) for b in blocks)
    remaining = budget - regular_tokens

    included_ephemeral = []
    for eb in fetched_blocks:
        if remaining >= eb.get("tokens", 0):
            included_ephemeral.append(eb)
            remaining -= eb.get("tokens", 0)
        # else: drop this ephemeral block (over budget)

    # 5. Pack
    all_blocks = blocks + included_ephemeral
    return pack(all_blocks, budget)
